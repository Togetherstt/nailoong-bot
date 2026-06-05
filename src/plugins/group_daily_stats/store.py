from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from nonebot.adapters.onebot.v11 import Message


DATA_DIR = Path("data") / "group_daily_stats"
STATE_PATH = DATA_DIR / "state.json"
STICKER_SEGMENT_TYPES = {"face", "mface", "marketface"}


@dataclass(slots=True)
class InteractionStats:
    mention_targets: list[str] = field(default_factory=list)
    sticker_count: int = 0
    sticker_targets: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not self.mention_targets
            and self.sticker_count <= 0
            and not self.sticker_targets
        )


@dataclass(slots=True)
class GroupStatsSnapshot:
    group_id: str
    date: str
    enabled: bool
    mentions_received: dict[str, int] = field(default_factory=dict)
    stickers_sent: dict[str, int] = field(default_factory=dict)
    sticker_targets: dict[str, int] = field(default_factory=dict)
    homophone_used: dict[str, int] = field(default_factory=dict)
    homophone_boxed: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class FlushResult:
    date: str
    next_date: str
    snapshots: dict[str, GroupStatsSnapshot]


class GroupDailyStatsStore:
    def __init__(self, state_path: Path = STATE_PATH) -> None:
        self.state_path = state_path
        self._lock = asyncio.Lock()

    async def is_enabled(self, group_id: str, *, today: Optional[str] = None) -> bool:
        async with self._lock:
            state = await self._load_state()
            await self._ensure_current_date(state, today=today)
            return str(group_id) in state["enabled_groups"]

    async def enable_group(self, group_id: str, *, today: Optional[str] = None) -> bool:
        normalized_group_id = str(group_id)
        async with self._lock:
            state = await self._load_state()
            await self._ensure_current_date(state, today=today)
            enabled_groups = set(state["enabled_groups"])
            already_enabled = normalized_group_id in enabled_groups
            enabled_groups.add(normalized_group_id)
            state["enabled_groups"] = sorted(enabled_groups)
            state["group_stats"][normalized_group_id] = _empty_group_stats_payload()
            await self._save_state(state)
            return not already_enabled

    async def disable_group(self, group_id: str, *, today: Optional[str] = None) -> bool:
        normalized_group_id = str(group_id)
        async with self._lock:
            state = await self._load_state()
            await self._ensure_current_date(state, today=today)
            enabled_groups = set(state["enabled_groups"])
            if normalized_group_id not in enabled_groups:
                return False
            enabled_groups.remove(normalized_group_id)
            state["enabled_groups"] = sorted(enabled_groups)
            state["group_stats"].pop(normalized_group_id, None)
            await self._save_state(state)
            return True

    async def list_enabled_groups(self, *, today: Optional[str] = None) -> list[str]:
        async with self._lock:
            state = await self._load_state()
            await self._ensure_current_date(state, today=today)
            return list(state["enabled_groups"])

    async def record_interaction(
        self,
        *,
        group_id: str,
        sender_id: str,
        interaction: InteractionStats,
        today: Optional[str] = None,
    ) -> None:
        if interaction.is_empty():
            return
        normalized_group_id = str(group_id)
        normalized_sender_id = str(sender_id)
        async with self._lock:
            state = await self._load_state()
            await self._ensure_current_date(state, today=today)
            if normalized_group_id not in state["enabled_groups"]:
                return
            group_stats = _get_or_create_group_stats(state, normalized_group_id)
            _increment_many(group_stats["mentions_received"], interaction.mention_targets)
            if interaction.sticker_count > 0:
                _increment(group_stats["stickers_sent"], normalized_sender_id, interaction.sticker_count)
            _increment_many(group_stats["sticker_targets"], interaction.sticker_targets)
            await self._save_state(state)

    async def record_homophone_usage(
        self,
        *,
        group_id: str,
        user_id: str,
        boxed_member_ids: list[str],
        today: Optional[str] = None,
    ) -> None:
        normalized_group_id = str(group_id)
        normalized_user_id = str(user_id)
        async with self._lock:
            state = await self._load_state()
            await self._ensure_current_date(state, today=today)
            if normalized_group_id not in state["enabled_groups"]:
                return
            group_stats = _get_or_create_group_stats(state, normalized_group_id)
            _increment(group_stats["homophone_used"], normalized_user_id, 1)
            _increment_many(
                group_stats["homophone_boxed"],
                [str(member_id) for member_id in boxed_member_ids if str(member_id).strip()],
            )
            await self._save_state(state)

    async def get_group_snapshot(
        self,
        group_id: str,
        *,
        today: Optional[str] = None,
    ) -> GroupStatsSnapshot:
        normalized_group_id = str(group_id)
        async with self._lock:
            state = await self._load_state()
            await self._ensure_current_date(state, today=today)
            return _build_snapshot(state, normalized_group_id)

    async def flush_daily_snapshots(
        self,
        *,
        today: Optional[str] = None,
        next_day: Optional[str] = None,
    ) -> FlushResult:
        async with self._lock:
            state = await self._load_state()
            await self._ensure_current_date(state, today=today)
            current_date = str(state["current_date"])
            resolved_next_day = next_day or next_date_string(current_date)
            snapshots = {
                group_id: _build_snapshot(state, group_id)
                for group_id in state["enabled_groups"]
            }
            state["current_date"] = resolved_next_day
            state["group_stats"] = {}
            await self._save_state(state)
            return FlushResult(
                date=current_date,
                next_date=resolved_next_day,
                snapshots=snapshots,
            )

    async def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "current_date": today_string(),
                "enabled_groups": [],
                "group_stats": {},
            }

        raw = await asyncio.to_thread(self.state_path.read_text, "utf-8")
        if not raw.strip():
            return {
                "current_date": today_string(),
                "enabled_groups": [],
                "group_stats": {},
            }

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {
                "current_date": today_string(),
                "enabled_groups": [],
                "group_stats": {},
            }

        current_date = str(payload.get("current_date") or today_string())
        enabled_groups = payload.get("enabled_groups", [])
        group_stats = payload.get("group_stats", {})

        normalized_enabled_groups = []
        if isinstance(enabled_groups, list):
            for item in enabled_groups:
                text = str(item).strip()
                if text:
                    normalized_enabled_groups.append(text)

        normalized_group_stats: dict[str, dict[str, dict[str, int]]] = {}
        if isinstance(group_stats, dict):
            for raw_group_id, raw_stats in group_stats.items():
                group_id = str(raw_group_id).strip()
                if not group_id or not isinstance(raw_stats, dict):
                    continue
                normalized_group_stats[group_id] = {
                    "mentions_received": _normalize_counter(raw_stats.get("mentions_received")),
                    "stickers_sent": _normalize_counter(raw_stats.get("stickers_sent")),
                    "sticker_targets": _normalize_counter(raw_stats.get("sticker_targets")),
                    "homophone_used": _normalize_counter(raw_stats.get("homophone_used")),
                    "homophone_boxed": _normalize_counter(raw_stats.get("homophone_boxed")),
                }

        return {
            "current_date": current_date,
            "enabled_groups": sorted(set(normalized_enabled_groups)),
            "group_stats": normalized_group_stats,
        }

    async def _save_state(self, state: dict[str, Any]) -> None:
        await asyncio.to_thread(self.state_path.parent.mkdir, parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "current_date": state["current_date"],
                "enabled_groups": state["enabled_groups"],
                "group_stats": state["group_stats"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        await asyncio.to_thread(self.state_path.write_text, payload, "utf-8")

    async def _ensure_current_date(
        self,
        state: dict[str, Any],
        *,
        today: Optional[str] = None,
    ) -> None:
        resolved_today = today or today_string()
        if state["current_date"] == resolved_today:
            return
        state["current_date"] = resolved_today
        state["group_stats"] = {}
        await self._save_state(state)


def extract_interaction_stats(message: Message, *, self_id: Optional[str] = None) -> InteractionStats:
    mention_targets: list[str] = []
    sticker_count = 0

    for segment in message:
        segment_type = getattr(segment, "type", "")
        if segment_type in STICKER_SEGMENT_TYPES:
            sticker_count += 1
            continue
        if segment_type != "at":
            continue
        target = str(segment.data.get("qq", "")).strip()
        if not target or target == "all":
            continue
        if self_id is not None and target == str(self_id):
            continue
        mention_targets.append(target)

    sticker_targets: list[str] = []
    if sticker_count > 0 and mention_targets:
        for target in mention_targets:
            sticker_targets.extend([target] * sticker_count)

    return InteractionStats(
        mention_targets=mention_targets,
        sticker_count=sticker_count,
        sticker_targets=sticker_targets,
    )


def today_string(now: Optional[datetime] = None) -> str:
    resolved_now = now.astimezone() if now is not None else datetime.now().astimezone()
    return resolved_now.date().isoformat()


def next_date_string(current_date: str) -> str:
    return (date.fromisoformat(current_date) + timedelta(days=1)).isoformat()


def _normalize_counter(raw_value: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    if not isinstance(raw_value, dict):
        return result
    for raw_key, raw_count in raw_value.items():
        key = str(raw_key).strip()
        if not key:
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if count > 0:
            result[key] = count
    return result


def _empty_group_stats_payload() -> dict[str, dict[str, int]]:
    return {
        "mentions_received": {},
        "stickers_sent": {},
        "sticker_targets": {},
        "homophone_used": {},
        "homophone_boxed": {},
    }


def _get_or_create_group_stats(
    state: dict[str, Any],
    group_id: str,
) -> dict[str, dict[str, int]]:
    group_stats = state["group_stats"].get(group_id)
    if group_stats is None:
        group_stats = _empty_group_stats_payload()
        state["group_stats"][group_id] = group_stats
    return group_stats


def _build_snapshot(state: dict[str, Any], group_id: str) -> GroupStatsSnapshot:
    raw_stats = state["group_stats"].get(group_id, _empty_group_stats_payload())
    return GroupStatsSnapshot(
        group_id=group_id,
        date=str(state["current_date"]),
        enabled=group_id in state["enabled_groups"],
        mentions_received=dict(raw_stats["mentions_received"]),
        stickers_sent=dict(raw_stats["stickers_sent"]),
        sticker_targets=dict(raw_stats["sticker_targets"]),
        homophone_used=dict(raw_stats["homophone_used"]),
        homophone_boxed=dict(raw_stats["homophone_boxed"]),
    )


def _increment(counter: dict[str, int], key: str, amount: int = 1) -> None:
    normalized_key = str(key).strip()
    if not normalized_key or amount <= 0:
        return
    counter[normalized_key] = counter.get(normalized_key, 0) + amount


def _increment_many(counter: dict[str, int], keys: list[str]) -> None:
    for key in keys:
        _increment(counter, key, 1)
