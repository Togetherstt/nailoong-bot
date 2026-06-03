from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Optional

from pypinyin import Style, lazy_pinyin


DATA_DIR = Path("data") / "homophone_box"
INDEX_PATH = DATA_DIR / "initials.json"
INITIALS_PATTERN = re.compile(r"^[a-z]{2,5}$")
CHINESE_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
TOKEN_PATTERN = re.compile(r"[A-Za-z]+|[\u4e00-\u9fff]")


@dataclass(frozen=True, slots=True)
class HomophoneToken:
    text: str
    initial: str


@dataclass(frozen=True, slots=True)
class ResultRotationState:
    remaining: tuple[str, ...]
    last_batch: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InitialsEntry:
    initials: str
    member_qq: Optional[str] = None


@dataclass(frozen=True, slots=True)
class HomophoneMatch:
    candidate: str
    initials: str
    member_qq: Optional[str] = None


class InitialsStore:
    def __init__(self, index_path: Path = INDEX_PATH) -> None:
        self.index_path = index_path
        self._lock = asyncio.Lock()

    async def add_initials(
        self,
        initials: str,
        member_qq: Optional[str] = None,
    ) -> bool:
        normalized = normalize_initials(initials)
        if normalized is None:
            raise ValueError("Initials must be 2 to 5 lowercase letters.")

        async with self._lock:
            current = await self._load_entries()
            if any(entry.initials == normalized for entry in current):
                return False
            current.append(
                InitialsEntry(initials=normalized, member_qq=normalize_member_qq(member_qq))
            )
            current.sort(key=lambda entry: entry.initials)
            await self._save_entries(current)
        return True

    async def list_entries(self) -> list[InitialsEntry]:
        async with self._lock:
            return await self._load_entries()

    async def list_initials(self) -> list[str]:
        entries = await self.list_entries()
        return [entry.initials for entry in entries]

    async def count(self) -> int:
        async with self._lock:
            return len(await self._load_entries())

    async def delete_initials(self, initials: str) -> bool:
        normalized = normalize_initials(initials)
        if normalized is None:
            raise ValueError("Initials must be 2 to 5 lowercase letters.")

        async with self._lock:
            current = await self._load_entries()
            filtered = [entry for entry in current if entry.initials != normalized]
            if len(filtered) == len(current):
                return False
            await self._save_entries(filtered)
        return True

    async def bind_member(self, initials: str, member_qq: str) -> bool:
        normalized = normalize_initials(initials)
        normalized_member_qq = normalize_member_qq(member_qq)
        if normalized is None:
            raise ValueError("Initials must be 2 to 5 lowercase letters.")
        if normalized_member_qq is None:
            raise ValueError("Member QQ must be numeric.")

        async with self._lock:
            current = await self._load_entries()
            updated = False
            next_entries: list[InitialsEntry] = []
            for entry in current:
                if entry.initials == normalized:
                    next_entries.append(
                        InitialsEntry(initials=entry.initials, member_qq=normalized_member_qq)
                    )
                    updated = True
                else:
                    next_entries.append(entry)
            if not updated:
                return False
            await self._save_entries(next_entries)
        return True

    async def unbind_member(self, initials: str) -> bool:
        normalized = normalize_initials(initials)
        if normalized is None:
            raise ValueError("Initials must be 2 to 5 lowercase letters.")

        async with self._lock:
            current = await self._load_entries()
            updated = False
            next_entries: list[InitialsEntry] = []
            for entry in current:
                if entry.initials == normalized:
                    if entry.member_qq is not None:
                        updated = True
                    next_entries.append(InitialsEntry(initials=entry.initials, member_qq=None))
                else:
                    next_entries.append(entry)
            if not any(entry.initials == normalized for entry in current) or not updated:
                return False
            await self._save_entries(next_entries)
        return True

    async def _load_entries(self) -> list[InitialsEntry]:
        if not self.index_path.exists():
            return []
        raw = await asyncio.to_thread(self.index_path.read_text, "utf-8")
        if not raw.strip():
            return []
        payload = json.loads(raw)
        if not isinstance(payload, list):
            return []

        entries: list[InitialsEntry] = []
        for item in payload:
            if isinstance(item, str):
                normalized = normalize_initials(item)
                if normalized is not None:
                    entries.append(InitialsEntry(initials=normalized))
                continue
            if not isinstance(item, dict):
                continue
            normalized = normalize_initials(str(item.get("initials", "")))
            if normalized is None:
                continue
            entries.append(
                InitialsEntry(
                    initials=normalized,
                    member_qq=normalize_member_qq(item.get("member_qq")),
                )
            )
        entries.sort(key=lambda entry: entry.initials)
        return entries

    async def _save_entries(self, entries: list[InitialsEntry]) -> None:
        await asyncio.to_thread(self.index_path.parent.mkdir, parents=True, exist_ok=True)
        payload = json.dumps(
            [
                {
                    "initials": entry.initials,
                    "member_qq": entry.member_qq,
                }
                for entry in entries
            ],
            ensure_ascii=False,
            indent=2,
        )
        await asyncio.to_thread(self.index_path.write_text, payload, "utf-8")


def normalize_initials(initials: str) -> Optional[str]:
    normalized = initials.strip().lower()
    if INITIALS_PATTERN.fullmatch(normalized) is None:
        return None
    return normalized


def normalize_member_qq(member_qq: object) -> Optional[str]:
    if member_qq is None:
        return None
    value = str(member_qq).strip()
    if not value or not value.isdigit():
        return None
    return value


def extract_chinese_text(text: str) -> str:
    return "".join(CHINESE_CHAR_PATTERN.findall(text))


def is_valid_quote_text(text: str, max_chinese_chars: int = 40) -> bool:
    chinese_text = extract_chinese_text(text)
    return 0 < len(chinese_text) <= max_chinese_chars


def find_homophone_results(
    quoted_text: str,
    initials_entries: list[str] | list[InitialsEntry],
) -> list[str]:
    return [match.candidate for match in find_homophone_matches(quoted_text, initials_entries)]


def find_homophone_matches(
    quoted_text: str,
    initials_entries: list[str] | list[InitialsEntry],
) -> list[HomophoneMatch]:
    tokens = extract_homophone_tokens(quoted_text)
    if len(tokens) < 2:
        return []

    normalized_entries = _normalize_entries(initials_entries)
    matches: list[HomophoneMatch] = []
    seen: set[tuple[str, str]] = set()

    grouped_targets: dict[int, list[InitialsEntry]] = {}
    for entry in normalized_entries:
        grouped_targets.setdefault(len(entry.initials), []).append(entry)

    for length, entries in grouped_targets.items():
        if len(tokens) < length:
            continue
        entries_map = {entry.initials: entry for entry in entries}
        for indexes in combinations(range(len(tokens)), length):
            candidate = "".join(tokens[index].text for index in indexes)
            candidate_initials = "".join(tokens[index].initial for index in indexes)
            if "?" in candidate_initials or candidate_initials not in entries_map:
                continue
            key = (candidate_initials, candidate)
            if key in seen:
                continue
            seen.add(key)
            entry = entries_map[candidate_initials]
            matches.append(
                HomophoneMatch(
                    candidate=candidate,
                    initials=candidate_initials,
                    member_qq=entry.member_qq,
                )
            )

    return matches


def limit_homophone_results(
    results: list[str],
    limit: int = 10,
    rng: Optional[random.Random] = None,
) -> list[str]:
    if limit <= 0:
        return []
    if len(results) <= limit:
        return results[:]
    chooser = rng if rng is not None else random
    return chooser.sample(results, limit)


def rotate_homophone_results(
    key: str,
    results: list[str],
    state_map: dict[str, ResultRotationState],
    limit: int = 10,
    rng: Optional[random.Random] = None,
) -> list[str]:
    if limit <= 0 or not results:
        return []

    chooser = rng if rng is not None else random
    deduped_results = list(dict.fromkeys(results))
    if len(deduped_results) <= limit:
        batch = deduped_results[:]
        state_map[key] = ResultRotationState(remaining=tuple(), last_batch=tuple(batch))
        return batch

    previous_state = state_map.get(key)
    remaining = list(previous_state.remaining) if previous_state else []
    remaining = [item for item in remaining if item in deduped_results]

    if len(remaining) < limit:
        pool = [item for item in deduped_results if item not in remaining]
        last_batch = set(previous_state.last_batch) if previous_state else set()
        fresh_pool = [item for item in pool if item not in last_batch]
        refill_source = fresh_pool if len(remaining) + len(fresh_pool) >= limit else pool
        chooser.shuffle(refill_source)
        remaining.extend(refill_source)

    batch = remaining[:limit]
    next_remaining = remaining[limit:]
    state_map[key] = ResultRotationState(
        remaining=tuple(next_remaining),
        last_batch=tuple(batch),
    )
    return batch


def extract_homophone_tokens(text: str) -> list[HomophoneToken]:
    tokens: list[HomophoneToken] = []
    for chunk in TOKEN_PATTERN.findall(text):
        if CHINESE_CHAR_PATTERN.fullmatch(chunk):
            tokens.append(HomophoneToken(text=chunk, initial=get_char_initial(chunk)))
            continue
        tokens.append(HomophoneToken(text=chunk, initial=chunk[0].lower()))
    return tokens


def get_char_initial(char: str) -> str:
    initials = lazy_pinyin(char, style=Style.FIRST_LETTER, errors=lambda _: ["?"])
    if not initials:
        return "?"
    return initials[0].lower()


def _normalize_entries(
    initials_entries: list[str] | list[InitialsEntry],
) -> list[InitialsEntry]:
    entries: list[InitialsEntry] = []
    for item in initials_entries:
        if isinstance(item, InitialsEntry):
            entries.append(item)
            continue
        normalized = normalize_initials(item)
        if normalized is not None:
            entries.append(InitialsEntry(initials=normalized))
    return entries
