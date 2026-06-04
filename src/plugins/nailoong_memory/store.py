from __future__ import annotations

import asyncio
import hashlib
import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import uuid4

from nonebot.adapters.onebot.v11 import Message, MessageSegment

try:
    from PIL import Image
except ImportError:  # pragma: no cover - depends on runtime environment
    Image = None


DATA_DIR = Path("data") / "nailoong"
IMAGE_DIR = DATA_DIR / "images"
INDEX_PATH = DATA_DIR / "index.json"
TRASH_DIR = DATA_DIR / "trash"
DELETED_INDEX_PATH = DATA_DIR / "deleted.json"
DEDUP_DHASH_THRESHOLD = 2


@dataclass(slots=True)
class NailoongRecord:
    id: str
    added_by: str
    added_at: str
    media_kind: Literal["local_image", "segment"] = "local_image"
    image_filename: Optional[str] = None
    segment_type: Optional[str] = None
    segment_data: Optional[dict[str, Any]] = None
    original_name: Optional[str] = None
    content_fingerprint: Optional[str] = None
    image_sha256: Optional[str] = None
    image_dhash: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None

    @property
    def display_name(self) -> str:
        return self.original_name or "未命名奶龙"

    def image_path(self, image_dir: Path = IMAGE_DIR) -> Path:
        if self.image_filename is None:
            raise ValueError("Record does not contain a local image filename.")
        return image_dir / self.image_filename

    def to_message(self, image_dir: Path = IMAGE_DIR) -> Message:
        summary = (
            f"奶龙名称：{self.display_name}\n"
            f"添加者 QQ：{self.added_by}\n"
            f"添加日期：{self.added_at}"
        )
        if self.media_kind == "segment":
            if self.segment_type is None or self.segment_data is None:
                raise ValueError("Segment record is missing serialized message segment data.")
            return MessageSegment(self.segment_type, self.segment_data) + "\n" + summary
        return MessageSegment.image(self.image_path(image_dir).resolve()) + "\n" + summary


class NailoongStore:
    def __init__(
        self,
        index_path: Path = INDEX_PATH,
        image_dir: Path = IMAGE_DIR,
        trash_dir: Path = TRASH_DIR,
        deleted_index_path: Path = DELETED_INDEX_PATH,
    ) -> None:
        self.index_path = index_path
        self.image_dir = image_dir
        self.trash_dir = trash_dir
        self.deleted_index_path = deleted_index_path
        self._lock = asyncio.Lock()

    async def add_record(
        self,
        image_bytes: bytes,
        *,
        added_by: str,
        original_name: Optional[str],
        file_extension: str,
        added_at: Optional[datetime] = None,
    ) -> tuple[NailoongRecord, bool]:
        timestamp = added_at or datetime.now(timezone.utc).astimezone()
        image_sha256 = build_sha256(image_bytes)
        image_dhash, image_width, image_height = build_dhash(image_bytes)
        record = NailoongRecord(
            id=uuid4().hex,
            added_by=added_by,
            added_at=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            media_kind="local_image",
            image_filename=f"{uuid4().hex}{self._normalize_extension(file_extension)}",
            original_name=self._normalize_name(original_name),
            image_sha256=image_sha256,
            image_dhash=image_dhash,
            image_width=image_width,
            image_height=image_height,
        )

        async with self._lock:
            records = await self._load_records()
            duplicate = self._find_duplicate_record(records, record)
            if duplicate is not None:
                return duplicate, False

            await asyncio.to_thread(self.image_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(self._record_path(record).write_bytes, image_bytes)
            records.append(record)
            await self._save_records(records)

        return record, True

    async def add_segment_record(
        self,
        segment: MessageSegment,
        *,
        added_by: str,
        original_name: Optional[str],
        image_bytes: Optional[bytes] = None,
        added_at: Optional[datetime] = None,
    ) -> tuple[NailoongRecord, bool]:
        timestamp = added_at or datetime.now(timezone.utc).astimezone()
        image_sha256: Optional[str] = None
        image_dhash: Optional[str] = None
        image_width: Optional[int] = None
        image_height: Optional[int] = None
        if image_bytes is not None:
            image_sha256 = build_sha256(image_bytes)
            image_dhash, image_width, image_height = build_dhash(image_bytes)

        record = NailoongRecord(
            id=uuid4().hex,
            added_by=added_by,
            added_at=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            media_kind="segment",
            segment_type=segment.type,
            segment_data=dict(segment.data),
            original_name=self._normalize_name(original_name),
            content_fingerprint=build_segment_fingerprint(segment),
            image_sha256=image_sha256,
            image_dhash=image_dhash,
            image_width=image_width,
            image_height=image_height,
        )

        async with self._lock:
            records = await self._load_records()
            duplicate = self._find_duplicate_record(records, record)
            if duplicate is not None:
                return duplicate, False

            records.append(record)
            await self._save_records(records)

        return record, True

    async def random_record(self) -> Optional[NailoongRecord]:
        async with self._lock:
            records = await self._load_records()
        if not records:
            return None
        return random.choice(records)

    async def count(self) -> int:
        async with self._lock:
            records = await self._load_records()
        return len(records)

    async def list_records(self) -> list[NailoongRecord]:
        async with self._lock:
            records = await self._load_records()
        return records

    async def delete_by_index(self, index: int) -> Optional[NailoongRecord]:
        async with self._lock:
            records = await self._load_records()
            if index < 1 or index > len(records):
                return None
            record = records.pop(index - 1)
            await self._archive_deleted_record(record)
            await self._save_records(records)
        return record

    async def delete_by_name(self, name: str) -> list[NailoongRecord]:
        target_name = self._normalize_name(name)
        if target_name is None:
            return []

        async with self._lock:
            records = await self._load_records()
            remained: list[NailoongRecord] = []
            deleted: list[NailoongRecord] = []
            for record in records:
                if record.display_name == target_name:
                    deleted.append(record)
                    await self._archive_deleted_record(record)
                else:
                    remained.append(record)

            if deleted:
                await self._save_records(remained)
        return deleted

    async def delete_latest(self) -> Optional[NailoongRecord]:
        async with self._lock:
            records = await self._load_records()
            if not records:
                return None
            record = records.pop()
            await self._archive_deleted_record(record)
            await self._save_records(records)
        return record

    async def undo_last_delete(self) -> Optional[NailoongRecord]:
        async with self._lock:
            deleted_records = await self._load_deleted_records()
            if not deleted_records:
                return None
            record = deleted_records.pop()
            await self._restore_archived_record(record)
            records = await self._load_records()
            records.append(record)
            await self._save_records(records)
            await self._save_deleted_records(deleted_records)
        return record

    async def _load_records(self) -> list[NailoongRecord]:
        if not self.index_path.exists():
            return []

        raw = await asyncio.to_thread(self.index_path.read_text, "utf-8")
        if not raw.strip():
            return []

        payload = json.loads(raw)
        if not isinstance(payload, list):
            return []

        records: list[NailoongRecord] = []
        migrated = False
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                record = NailoongRecord(**item)
            except TypeError:
                continue
            if self._backfill_record_fingerprints(record):
                migrated = True
            if self._record_exists(record):
                records.append(record)
        if migrated:
            await self._save_records(records)
        return records

    async def _save_records(self, records: list[NailoongRecord]) -> None:
        await asyncio.to_thread(self.index_path.parent.mkdir, parents=True, exist_ok=True)
        payload = json.dumps(
            [asdict(record) for record in records],
            ensure_ascii=False,
            indent=2,
        )
        await asyncio.to_thread(self.index_path.write_text, payload, "utf-8")

    async def _load_deleted_records(self) -> list[NailoongRecord]:
        if not self.deleted_index_path.exists():
            return []

        raw = await asyncio.to_thread(self.deleted_index_path.read_text, "utf-8")
        if not raw.strip():
            return []

        payload = json.loads(raw)
        if not isinstance(payload, list):
            return []

        records: list[NailoongRecord] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                record = NailoongRecord(**item)
            except TypeError:
                continue
            records.append(record)
        return records

    async def _save_deleted_records(self, records: list[NailoongRecord]) -> None:
        await asyncio.to_thread(self.deleted_index_path.parent.mkdir, parents=True, exist_ok=True)
        payload = json.dumps(
            [asdict(record) for record in records],
            ensure_ascii=False,
            indent=2,
        )
        await asyncio.to_thread(self.deleted_index_path.write_text, payload, "utf-8")

    def message_for_record(self, record: NailoongRecord) -> Message:
        return record.to_message(self.image_dir)

    def _record_path(self, record: NailoongRecord) -> Path:
        return record.image_path(self.image_dir)

    def _record_exists(self, record: NailoongRecord) -> bool:
        if record.media_kind == "segment":
            return bool(record.segment_type and record.segment_data is not None)
        return self._record_path(record).exists()

    def _backfill_record_fingerprints(self, record: NailoongRecord) -> bool:
        migrated = False

        if record.media_kind == "segment":
            if (
                record.content_fingerprint is None
                and record.segment_type is not None
                and record.segment_data is not None
            ):
                record.content_fingerprint = build_segment_fingerprint(
                    MessageSegment(record.segment_type, record.segment_data)
                )
                migrated = True
            return migrated

        image_path = self._record_path(record)
        if not image_path.exists():
            return migrated

        image_bytes = image_path.read_bytes()
        if record.image_sha256 is None:
            record.image_sha256 = build_sha256(image_bytes)
            migrated = True
        if (
            record.image_dhash is None
            or record.image_width is None
            or record.image_height is None
        ):
            image_dhash, image_width, image_height = build_dhash(image_bytes)
            if image_dhash is not None and image_width is not None and image_height is not None:
                record.image_dhash = image_dhash
                record.image_width = image_width
                record.image_height = image_height
                migrated = True

        return migrated

    def _find_duplicate_record(
        self,
        records: list[NailoongRecord],
        candidate: NailoongRecord,
    ) -> Optional[NailoongRecord]:
        for record in records:
            if self._is_duplicate_record(record, candidate):
                return record
        return None

    def _is_duplicate_record(
        self,
        existing: NailoongRecord,
        candidate: NailoongRecord,
    ) -> bool:
        if (
            existing.content_fingerprint
            and candidate.content_fingerprint
            and existing.content_fingerprint == candidate.content_fingerprint
        ):
            return True

        if existing.image_sha256 and candidate.image_sha256 and existing.image_sha256 == candidate.image_sha256:
            return True

        if (
            existing.image_dhash
            and candidate.image_dhash
            and existing.image_width is not None
            and existing.image_height is not None
            and candidate.image_width is not None
            and candidate.image_height is not None
            and existing.image_width == candidate.image_width
            and existing.image_height == candidate.image_height
            and hamming_distance(existing.image_dhash, candidate.image_dhash) <= DEDUP_DHASH_THRESHOLD
        ):
            return True

        return False

    async def _archive_deleted_record(self, record: NailoongRecord) -> None:
        deleted_records = await self._load_deleted_records()
        if record.media_kind == "local_image":
            await asyncio.to_thread(self.trash_dir.mkdir, parents=True, exist_ok=True)
            source_path = self._record_path(record)
            if source_path.exists():
                await asyncio.to_thread(
                    source_path.replace,
                    self.trash_dir / source_path.name,
                )
        deleted_records.append(record)
        await self._save_deleted_records(deleted_records)

    async def _restore_archived_record(self, record: NailoongRecord) -> None:
        if record.media_kind != "local_image":
            return
        await asyncio.to_thread(self.image_dir.mkdir, parents=True, exist_ok=True)
        trash_path = self.trash_dir / record.image_filename
        if trash_path.exists():
            await asyncio.to_thread(
                trash_path.replace,
                self.image_dir / trash_path.name,
            )

    @staticmethod
    def _normalize_extension(file_extension: str) -> str:
        suffix = file_extension.strip().lower()
        if not suffix:
            return ".jpg"
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
            return suffix
        return ".jpg"

    @staticmethod
    def _normalize_name(original_name: Optional[str]) -> Optional[str]:
        if original_name is None:
            return None
        stripped = original_name.strip()
        return stripped or None


def extract_command_name(message: Message) -> tuple[str, Optional[str]]:
    plain_text = message.extract_plain_text().strip()
    if not plain_text:
        return "", None

    parts = plain_text.split(maxsplit=1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else None
    return command, argument or None


def find_image_url(reply_message: Message) -> Optional[str]:
    for segment in reply_message:
        if segment.type != "image":
            continue
        for key in ("url", "file"):
            value = segment.data.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
    return None


def find_storable_segment(reply_message: Message) -> Optional[MessageSegment]:
    for segment in reply_message:
        if segment.type == "image":
            return segment
        if segment.type in {"face", "mface", "marketface"}:
            return segment
    return None


def find_segment_image_url(segment: MessageSegment) -> Optional[str]:
    if segment.type not in {"image", "mface", "marketface"}:
        return None
    for key in ("url", "file"):
        value = segment.data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def guess_extension_from_url(url: str) -> str:
    normalized = url.split("?", 1)[0].rsplit("/", 1)[-1].lower()
    if "." not in normalized:
        return ".jpg"
    return f".{normalized.rsplit('.', 1)[-1]}"


def build_record_dict(record: NailoongRecord) -> dict[str, Any]:
    return asdict(record)


def format_record_line(index: int, record: NailoongRecord) -> str:
    media_label = "图片" if record.media_kind == "local_image" else "QQ表情"
    return (
        f"{index}. {record.display_name} | {media_label} | "
        f"添加者:{record.added_by} | 日期:{record.added_at}"
    )


def build_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_dhash(content: bytes) -> tuple[Optional[str], Optional[int], Optional[int]]:
    if Image is None:
        return None, None, None

    with Image.open(BytesIO(content)) as image:
        width, height = image.size
        grayscale = image.convert("L").resize((9, 8))
        pixels = list(grayscale.getdata())

    bits = []
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits.append("1" if left > right else "0")
    value = f"{int(''.join(bits), 2):016x}"
    return value, width, height


def hamming_distance(left: str, right: str) -> int:
    return sum(ch1 != ch2 for ch1, ch2 in zip(left, right)) + abs(len(left) - len(right))


def build_segment_fingerprint(segment: MessageSegment) -> str:
    payload = {
        "type": segment.type,
        "data": _normalize_json_value(dict(segment.data)),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_json_value(inner) for key, inner in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)
