from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from nonebot.adapters.onebot.v11 import Message, MessageSegment


DATA_DIR = Path("data") / "joke_library"
IMAGE_DIR = DATA_DIR / "images"
INDEX_PATH = DATA_DIR / "index.json"
GENERAL_TAG_LABEL = "通用"
MAX_FUZZY_QUERY_CHARS = 10

ImageDownloader = Callable[[str], Awaitable[bytes]]


@dataclass(slots=True)
class JokeContentSegment:
    kind: str
    segment_type: Optional[str] = None
    segment_data: Optional[dict[str, Any]] = None
    image_filename: Optional[str] = None

    def to_message_segment(self, image_dir: Path) -> MessageSegment:
        if self.kind == "local_image":
            if self.image_filename is None:
                raise ValueError("Missing image filename for local image segment.")
            return MessageSegment.image((image_dir / self.image_filename).resolve())
        if self.segment_type is None or self.segment_data is None:
            raise ValueError("Missing serialized segment data.")
        return MessageSegment(self.segment_type, self.segment_data)


@dataclass(slots=True)
class PreparedImageFile:
    filename: str
    content: bytes


@dataclass(slots=True)
class PreparedJoke:
    fingerprint: str
    plain_text: str
    segments: list[JokeContentSegment]
    image_files: list[PreparedImageFile] = field(default_factory=list)


@dataclass(slots=True)
class JokeRecord:
    id: int
    tag: Optional[str]
    provided_by: str
    provided_at: str
    fingerprint: str
    plain_text: str
    segments: list[JokeContentSegment]

    @property
    def display_tag(self) -> str:
        return self.tag or GENERAL_TAG_LABEL

    def to_message(self, image_dir: Path = IMAGE_DIR, metadata_mode: str = "full") -> Message:
        message = Message()
        for segment in self.segments:
            message.append(segment.to_message_segment(image_dir))

        if metadata_mode == "id_only":
            summary = f"\n\n笑话编号：{self.id}"
        else:
            summary = (
                f"\n\n笑话编号：{self.id}\n"
                f"归属标签：{self.display_tag}\n"
                f"提供人QQ：{self.provided_by}\n"
                f"提供时间：{self.provided_at}"
            )
        message.append(MessageSegment.text(summary))
        return message


class JokeStore:
    def __init__(
        self,
        index_path: Path = INDEX_PATH,
        image_dir: Path = IMAGE_DIR,
        rng: Any = random,
    ) -> None:
        self.index_path = index_path
        self.image_dir = image_dir
        self._rng = rng
        self._lock = asyncio.Lock()

    async def add_prepared_joke(
        self,
        prepared: PreparedJoke,
        *,
        provided_by: str,
        tag: Optional[str],
        provided_at: Optional[datetime] = None,
    ) -> tuple[JokeRecord, bool]:
        normalized_tag = normalize_tag(tag)
        timestamp = provided_at or datetime.now(timezone.utc).astimezone()

        async with self._lock:
            state = await self._load_state()
            fingerprint_map = {record.fingerprint: record for record in state["records"]}
            existing = fingerprint_map.get(prepared.fingerprint)
            if existing is not None:
                return existing, False

            record = JokeRecord(
                id=state["next_id"],
                tag=normalized_tag,
                provided_by=str(provided_by),
                provided_at=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                fingerprint=prepared.fingerprint,
                plain_text=prepared.plain_text,
                segments=prepared.segments,
            )

            await asyncio.to_thread(self.image_dir.mkdir, parents=True, exist_ok=True)
            for image_file in prepared.image_files:
                await asyncio.to_thread(
                    (self.image_dir / image_file.filename).write_bytes,
                    image_file.content,
                )

            state["records"].append(record)
            state["next_id"] += 1
            await self._save_state(state)
            return record, True

    async def random_record(self, tag: Optional[str] = None) -> Optional[JokeRecord]:
        normalized_tag = normalize_tag(tag)
        async with self._lock:
            state = await self._load_state()
            candidates = _filter_records_by_tag(state["records"], normalized_tag)
        if not candidates:
            return None
        return self._rng.choice(candidates)

    async def find_by_id(self, joke_id: int) -> Optional[JokeRecord]:
        async with self._lock:
            state = await self._load_state()
            for record in state["records"]:
                if record.id == joke_id:
                    return record
        return None

    async def fuzzy_find(self, query: str) -> Optional[JokeRecord]:
        normalized_query = normalize_search_text(query)
        if not normalized_query:
            return None

        async with self._lock:
            state = await self._load_state()
            records = list(state["records"])

        best_record: Optional[JokeRecord] = None
        best_score = -1.0
        for record in records:
            score = _score_joke_match(normalized_query, record.plain_text)
            if score > best_score:
                best_score = score
                best_record = record
            elif score == best_score and best_record is not None and record.id < best_record.id:
                best_record = record
        return best_record

    async def delete_by_id(self, joke_id: int) -> Optional[JokeRecord]:
        async with self._lock:
            state = await self._load_state()
            records = state["records"]
            target_index = next(
                (index for index, record in enumerate(records) if record.id == joke_id),
                None,
            )
            if target_index is None:
                return None

            record = records.pop(target_index)
            await self._delete_record_images(record)
            await self._save_state(state)
            return record

    async def count(self) -> int:
        async with self._lock:
            state = await self._load_state()
        return len(state["records"])

    async def _load_state(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"next_id": 1, "records": []}

        raw = await asyncio.to_thread(self.index_path.read_text, "utf-8")
        if not raw.strip():
            return {"next_id": 1, "records": []}

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {"next_id": 1, "records": []}

        next_id = payload.get("next_id", 1)
        if not isinstance(next_id, int) or next_id < 1:
            next_id = 1

        raw_records = payload.get("records", [])
        if not isinstance(raw_records, list):
            raw_records = []

        records: list[JokeRecord] = []
        for item in raw_records:
            if not isinstance(item, dict):
                continue
            try:
                raw_segments = item.get("segments", [])
                if not isinstance(raw_segments, list):
                    continue
                segments = [JokeContentSegment(**segment) for segment in raw_segments if isinstance(segment, dict)]
                record = JokeRecord(
                    id=int(item["id"]),
                    tag=item.get("tag"),
                    provided_by=str(item["provided_by"]),
                    provided_at=str(item["provided_at"]),
                    fingerprint=str(item["fingerprint"]),
                    plain_text=str(item.get("plain_text", "")),
                    segments=segments,
                )
            except (KeyError, TypeError, ValueError):
                continue
            if self._record_exists(record):
                records.append(record)

        if records:
            next_id = max(next_id, max(record.id for record in records) + 1)
        return {"next_id": next_id, "records": records}

    async def _save_state(self, state: dict[str, Any]) -> None:
        await asyncio.to_thread(self.index_path.parent.mkdir, parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "next_id": state["next_id"],
                "records": [self._record_to_dict(record) for record in state["records"]],
            },
            ensure_ascii=False,
            indent=2,
        )
        await asyncio.to_thread(self.index_path.write_text, payload, "utf-8")

    async def _delete_record_images(self, record: JokeRecord) -> None:
        for segment in record.segments:
            if segment.kind != "local_image" or not segment.image_filename:
                continue
            image_path = self.image_dir / segment.image_filename
            if image_path.exists():
                await asyncio.to_thread(image_path.unlink)

    def _record_exists(self, record: JokeRecord) -> bool:
        for segment in record.segments:
            if segment.kind == "local_image":
                if not segment.image_filename:
                    return False
                if not (self.image_dir / segment.image_filename).exists():
                    return False
                continue
            if not segment.segment_type or segment.segment_data is None:
                return False
        return bool(record.segments)

    @staticmethod
    def _record_to_dict(record: JokeRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "tag": record.tag,
            "provided_by": record.provided_by,
            "provided_at": record.provided_at,
            "fingerprint": record.fingerprint,
            "plain_text": record.plain_text,
            "segments": [asdict(segment) for segment in record.segments],
        }


async def prepare_joke_from_reply(
    reply_message: Message,
    image_downloader: ImageDownloader,
) -> PreparedJoke:
    stored_segments: list[JokeContentSegment] = []
    image_files: list[PreparedImageFile] = []
    fingerprint_parts: list[dict[str, Any]] = []

    for segment in reply_message:
        if segment.type == "image":
            image_url = _extract_image_url(segment)
            if image_url:
                image_bytes = await image_downloader(image_url)
                image_digest = hashlib.sha256(image_bytes).hexdigest()
                filename = f"{uuid4().hex}{guess_extension_from_url(image_url)}"
                stored_segments.append(JokeContentSegment(kind="local_image", image_filename=filename))
                image_files.append(PreparedImageFile(filename=filename, content=image_bytes))
                fingerprint_parts.append({"type": "image", "sha256": image_digest})
                continue

        stored_segments.append(
            JokeContentSegment(
                kind="segment",
                segment_type=segment.type,
                segment_data=dict(segment.data),
            )
        )
        fingerprint_parts.append(
            {
                "type": segment.type,
                "data": _normalize_segment_data(segment.data),
            }
        )

    if not stored_segments:
        raise ValueError("Reply message is empty.")

    fingerprint_source = json.dumps(
        fingerprint_parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    return PreparedJoke(
        fingerprint=fingerprint,
        plain_text=normalize_search_text(reply_message.extract_plain_text()),
        segments=stored_segments,
        image_files=image_files,
    )


def extract_command(plain_text: str) -> tuple[str, Optional[str]]:
    content = plain_text.strip()
    if not content:
        return "", None
    parts = content.split(maxsplit=1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else None
    return command, argument or None


def normalize_tag(tag: Optional[str]) -> Optional[str]:
    if tag is None:
        return None
    normalized = re.sub(r"\s+", " ", tag).strip()
    return normalized or None


def normalize_search_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_valid_fuzzy_query(query: str) -> bool:
    normalized = normalize_search_text(query)
    compact = normalized.replace(" ", "")
    return bool(compact) and len(compact) <= MAX_FUZZY_QUERY_CHARS


def parse_joke_id(raw_value: Optional[str]) -> Optional[int]:
    if raw_value is None:
        return None
    text = raw_value.strip()
    if not text.isdigit():
        return None
    joke_id = int(text)
    return joke_id if joke_id > 0 else None


def guess_extension_from_url(url: str) -> str:
    normalized = url.split("?", 1)[0].rsplit("/", 1)[-1].lower()
    if "." not in normalized:
        return ".jpg"
    suffix = f".{normalized.rsplit('.', 1)[-1]}"
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
        return suffix
    return ".jpg"


def _filter_records_by_tag(records: list[JokeRecord], tag: Optional[str]) -> list[JokeRecord]:
    if tag is None:
        return list(records)
    return [record for record in records if normalize_tag(record.tag) == tag]


def _extract_image_url(segment: MessageSegment) -> Optional[str]:
    for key in ("url", "file"):
        value = segment.data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _normalize_segment_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in sorted(data.keys()):
        normalized[key] = _normalize_json_value(data[key])
    return normalized


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_json_value(inner) for key, inner in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, str):
        return normalize_search_text(value) if value.strip() else ""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _score_joke_match(query: str, plain_text: str) -> float:
    text = normalize_search_text(plain_text)
    if not text:
        return -1.0
    if query == text:
        return 10.0
    if query in text:
        return 8.0 + len(query) / max(len(text), 1)
    return _best_partial_ratio(query, text)


def _best_partial_ratio(query: str, text: str) -> float:
    if not query or not text:
        return -1.0
    if len(text) <= len(query) + 2:
        return SequenceMatcher(None, query, text).ratio()

    query_length = len(query)
    sizes = {
        max(1, query_length - 2),
        max(1, query_length - 1),
        query_length,
        query_length + 1,
        query_length + 2,
    }
    best = 0.0
    for size in sorted(sizes):
        if size >= len(text):
            best = max(best, SequenceMatcher(None, query, text).ratio())
            continue
        for start in range(0, len(text) - size + 1):
            candidate = text[start : start + size]
            best = max(best, SequenceMatcher(None, query, candidate).ratio())
    return best
