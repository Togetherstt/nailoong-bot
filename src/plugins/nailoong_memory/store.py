from __future__ import annotations

import asyncio
import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import uuid4

from nonebot.adapters.onebot.v11 import Message, MessageSegment


DATA_DIR = Path("data") / "nailoong"
IMAGE_DIR = DATA_DIR / "images"
INDEX_PATH = DATA_DIR / "index.json"
TRASH_DIR = DATA_DIR / "trash"
DELETED_INDEX_PATH = DATA_DIR / "deleted.json"


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
    ) -> NailoongRecord:
        timestamp = added_at or datetime.now(timezone.utc).astimezone()
        record = NailoongRecord(
            id=uuid4().hex,
            added_by=added_by,
            added_at=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            media_kind="local_image",
            image_filename=f"{uuid4().hex}{self._normalize_extension(file_extension)}",
            original_name=self._normalize_name(original_name),
        )

        async with self._lock:
            records = await self._load_records()
            await asyncio.to_thread(self.image_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(self._record_path(record).write_bytes, image_bytes)
            records.append(record)
            await self._save_records(records)

        return record

    async def add_segment_record(
        self,
        segment: MessageSegment,
        *,
        added_by: str,
        original_name: Optional[str],
        added_at: Optional[datetime] = None,
    ) -> NailoongRecord:
        timestamp = added_at or datetime.now(timezone.utc).astimezone()
        record = NailoongRecord(
            id=uuid4().hex,
            added_by=added_by,
            added_at=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            media_kind="segment",
            segment_type=segment.type,
            segment_data=dict(segment.data),
            original_name=self._normalize_name(original_name),
        )

        async with self._lock:
            records = await self._load_records()
            records.append(record)
            await self._save_records(records)

        return record

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
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                record = NailoongRecord(**item)
            except TypeError:
                continue
            if self._record_exists(record):
                records.append(record)
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
