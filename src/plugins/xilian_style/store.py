from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DATA_DIR = Path("data") / "xilian_style"
MODE_STATE_PATH = DATA_DIR / "mode_state.json"
MUSICAL_NOTE = "\u266A"
INVALID_NOTE_VARIANTS = ("🎵", "♫", "♬", "♩")
TRAILING_PUNCTUATION = "。！？!?，,、；;：:…~～"


@dataclass(frozen=True, slots=True)
class XiLianModeState:
    enabled: bool = False


class XiLianModeStore:
    def __init__(self, state_path: Path = MODE_STATE_PATH) -> None:
        self.state_path = state_path
        self._lock = asyncio.Lock()

    async def is_enabled(self) -> bool:
        async with self._lock:
            return (await self._load_state()).enabled

    async def set_enabled(self, enabled: bool) -> XiLianModeState:
        state = XiLianModeState(enabled=enabled)
        async with self._lock:
            await self._save_state(state)
        return state

    async def _load_state(self) -> XiLianModeState:
        if not self.state_path.exists():
            return XiLianModeState()
        raw = await asyncio.to_thread(self.state_path.read_text, "utf-8")
        if not raw.strip():
            return XiLianModeState()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return XiLianModeState()
        return XiLianModeState(enabled=bool(payload.get("enabled", False)))

    async def _save_state(self, state: XiLianModeState) -> None:
        await asyncio.to_thread(self.state_path.parent.mkdir, parents=True, exist_ok=True)
        payload = json.dumps({"enabled": state.enabled}, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self.state_path.write_text, payload, "utf-8")


class RollingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def allow(self, now: Optional[float] = None) -> bool:
        async with self._lock:
            return self.allow_sync(now=now)

    def allow_sync(self, now: Optional[float] = None) -> bool:
        current = time.monotonic() if now is None else now
        self.prune_sync(now=current)
        if len(self._timestamps) >= self.limit:
            return False
        self._timestamps.append(current)
        return True

    def prune_sync(self, now: Optional[float] = None) -> None:
        current = time.monotonic() if now is None else now
        while self._timestamps and current - self._timestamps[0] >= self.window_seconds:
            self._timestamps.popleft()

    def size(self, now: Optional[float] = None) -> int:
        self.prune_sync(now=now)
        return len(self._timestamps)


def extract_command(plain_text: str) -> tuple[str, Optional[str]]:
    content = plain_text.strip()
    if not content:
        return "", None
    parts = content.split(maxsplit=1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else None
    return command, argument or None


def normalize_quoted_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_valid_quoted_text(text: str, max_chars: int = 40) -> bool:
    normalized = normalize_quoted_text(text)
    if not normalized:
        return False
    compact = normalized.replace(" ", "")
    return 0 < len(compact) <= max_chars


def build_system_prompt(task: str) -> str:
    task_line = (
        "你要将用户提供的话改写成昔涟风格。"
        if task == "rewrite"
        else "你要以昔涟风格回复用户提供的话。"
    )
    return (
        "你是一个负责中文文案改写的助手。"
        "请模仿一种带有少女感、柔和、抒情、朦胧、文艺气质的“昔涟风格”，"
        "多使用“爱、希望、故事、美好、浪漫、种子、花朵、黎明、明天、天空”等意象，"
        "自称尽量使用“人家”，语气轻柔，允许少量比喻与排比，但不要过度堆砌。"
        f"{task_line}"
        "输出必须是简体中文，且不超过80个字符。"
        f"可适度穿插 U+266A（{MUSICAL_NOTE}），但不要过于频繁。"
        f"全文最后必须以且只能以单个 {MUSICAL_NOTE} 结尾，"
        f"不能使用🎵、♫、♬、♩等替代符号。"
        f"任何以 {MUSICAL_NOTE} 结尾的句子后面都不能再跟标点。"
        "只输出最终文本，不要解释。"
    )


def build_user_prompt(task: str, quoted_text: str) -> str:
    if task == "rewrite":
        return f"请把这句话改写成昔涟口吻：{quoted_text}"
    return f"请以昔涟口吻回复这句话：{quoted_text}"


def sanitize_xilian_output(text: str, max_chars: int = 80) -> str:
    normalized = normalize_quoted_text(text)
    for symbol in INVALID_NOTE_VARIANTS:
        normalized = normalized.replace(symbol, MUSICAL_NOTE)
    normalized = re.sub(rf"{re.escape(MUSICAL_NOTE)}[{re.escape(TRAILING_PUNCTUATION)}]+", MUSICAL_NOTE, normalized)
    normalized = normalized.strip()
    normalized = normalized.rstrip(TRAILING_PUNCTUATION)
    normalized = re.sub(rf"{re.escape(MUSICAL_NOTE)}+$", "", normalized).strip()
    if not normalized:
        return MUSICAL_NOTE

    allowed_length = max_chars - len(MUSICAL_NOTE)
    if allowed_length < 0:
        allowed_length = 0
    if len(normalized) > allowed_length:
        normalized = normalized[:allowed_length].rstrip()
        normalized = normalized.rstrip(TRAILING_PUNCTUATION)
        normalized = re.sub(rf"{re.escape(MUSICAL_NOTE)}+$", "", normalized).strip()
    return f"{normalized}{MUSICAL_NOTE}"
