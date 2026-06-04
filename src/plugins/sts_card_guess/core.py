from __future__ import annotations

import json
import random
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_CARD_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "sts_card_guess" / "cards"

TYPE_LABELS = {
    "attack": "攻击",
    "攻击": "攻击",
    "skill": "技能",
    "技能": "技能",
    "power": "能力",
    "能力": "能力",
    "curse": "诅咒",
    "诅咒": "诅咒",
    "status": "状态",
    "状态": "状态",
    "quest": "任务",
    "任务": "任务",
}

SOURCE_LABELS = {
    "defect": "故障机器人",
    "机器人": "故障机器人",
    "故障机器人": "故障机器人",
    "silent": "静默猎手",
    "静默猎手": "静默猎手",
    "ironclad": "铁血战士",
    "铁甲战士": "铁血战士",
    "铁血战士": "铁血战士",
    "regent": "储君",
    "继承者": "储君",
    "储君": "储君",
    "necrobinder": "亡灵契约师",
    "死亡缚者": "亡灵契约师",
    "亡灵契约师": "亡灵契约师",
    "colorless": "无色",
    "无色": "无色",
    "event": "其他",
    "curse": "其他",
    "status": "其他",
    "other": "其他",
    "其它": "其他",
    "其他": "其他",
}


@dataclass(frozen=True)
class CardRecord:
    card_id: str
    name: str
    description: str
    type_label: str
    source_label: str
    cost: str

    @property
    def name_length(self) -> int:
        return len(self.name.strip())


@dataclass(frozen=True)
class CardHint:
    label: str
    value: str


@dataclass
class GuessAdvanceResult:
    message: str
    finished: bool = False


@dataclass
class GuessGameState:
    card: CardRecord
    initial_hints: list[CardHint]
    delayed_hint: CardHint
    hidden_description_indexes: set[int]
    reveal_batches: list[tuple[int, ...]]
    revealed_description_indexes: set[int] = field(default_factory=set)
    delayed_hint_revealed: bool = False
    reveal_round: int = 0
    rng: random.Random = field(default_factory=random.Random)

    def is_finished(self) -> bool:
        return self.reveal_round >= len(self.reveal_batches)

    def render_board(self) -> str:
        lines = ["杀戮尖塔猜卡牌：", "已知信息："]
        visible_hints = list(self.initial_hints)
        if self.delayed_hint_revealed:
            visible_hints.append(self.delayed_hint)
        for hint in visible_hints:
            lines.append(f"- {hint.label}：{hint.value}")
        lines.append(f"- 描述：{render_masked_description(self.card.description, self.revealed_description_indexes)}")
        return "\n".join(lines)

    def build_start_message(self) -> str:
        return (
            f"{self.render_board()}\n"
            "10 秒后将补充最后 1 条基础提示。\n"
            "猜测格式：`@机器人 卡牌完整名称`\n"
            "也可以使用 `@机器人 /提示` 立即进入下一阶段。"
        )

    def check_guess(self, guess: str) -> bool:
        normalized = normalize_guess_text(guess)
        return normalized == normalize_guess_text(self.card.name)

    def advance(self) -> GuessAdvanceResult:
        if not self.delayed_hint_revealed:
            self.delayed_hint_revealed = True
            return GuessAdvanceResult(
                message=(
                    "猜卡提示升级：\n"
                    f"新增提示：{self.delayed_hint.label}：{self.delayed_hint.value}\n"
                    f"{self.render_board()}\n"
                    "60 秒后若仍无人猜中，将开始揭示描述中的隐藏文字。"
                )
            )

        if self.reveal_round >= len(self.reveal_batches):
            return GuessAdvanceResult(
                message=f"这张卡是 `{self.card.name}`，本局猜卡失败。", finished=True
            )

        selected = self.reveal_batches[self.reveal_round]
        self.revealed_description_indexes.update(selected)
        self.reveal_round += 1

        if self.is_finished():
            return GuessAdvanceResult(
                message=(
                    "描述已全部揭晓：\n"
                    f"{self.render_board()}\n"
                    f"这张卡是 `{self.card.name}`，本局猜卡失败。"
                ),
                finished=True,
            )

        return GuessAdvanceResult(
            message=(
                f"提示阶段 {self.reveal_round}：已额外揭示部分描述文字。\n"
                f"{self.render_board()}\n"
                "60 秒后若仍无人猜中，将继续揭示下一批隐藏文字。"
            )
        )


class CardRepository:
    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._data_dir = data_dir or DEFAULT_CARD_DATA_DIR
        self._cards: list[CardRecord] | None = None

    def get_data_dir(self) -> Path:
        return self._data_dir

    def set_data_dir(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._cards = None

    def load_cards(self) -> list[CardRecord]:
        if self._cards is None:
            self._cards = load_cards_from_dir(self._data_dir)
        return list(self._cards)

    def find_card(self, keyword: str) -> Optional[CardRecord]:
        normalized = normalize_guess_text(keyword)
        if not normalized:
            return None
        for card in self.load_cards():
            if normalize_guess_text(card.name) == normalized:
                return card
            if normalize_guess_text(card.card_id) == normalized:
                return card
        return None

    def pick_random_card(self, rng: Optional[random.Random] = None) -> CardRecord:
        cards = self.load_cards()
        if not cards:
            raise ValueError("卡牌库为空")
        chooser = rng or random
        return chooser.choice(cards)


def load_cards_from_dir(data_dir: Path) -> list[CardRecord]:
    if not data_dir.exists():
        raise FileNotFoundError(f"卡牌目录不存在：{data_dir}")

    cards: list[CardRecord] = []
    seen_ids: set[str] = set()

    for path in sorted(data_dir.rglob("*.json")):
        if path.name.lower() == "index.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        default_type = _normalize_type_label(
            payload.get("card_type") or payload.get("type") or payload.get("type_zh")
        )
        default_source = _normalize_source_label(
            payload.get("source_pool"),
            character_zh=payload.get("character_zh"),
            pool=payload.get("pool"),
            character=payload.get("character"),
        )
        for item in payload.get("cards", []):
            source_label = _normalize_source_label(
                payload.get("source_pool"),
                character_zh=item.get("character_zh"),
                pool=item.get("pool"),
                character=item.get("character"),
                fallback=default_source,
            )

            type_label = _normalize_type_label(
                item.get("type_zh") or item.get("type") or payload.get("card_type") or default_type
            )
            if type_label is None:
                continue

            card_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            description = normalize_description_text(str(item.get("description", "")))
            if not card_id or not name or not description:
                continue
            if card_id in seen_ids:
                continue
            seen_ids.add(card_id)
            cards.append(
                CardRecord(
                    card_id=card_id,
                    name=name,
                    description=description,
                    type_label=type_label,
                    source_label=source_label,
                    cost=normalize_cost(str(item.get("cost", ""))),
                )
            )

    return cards


def create_game_state(
    card: CardRecord,
    rng: Optional[random.Random] = None,
) -> GuessGameState:
    game_rng = rng or random.Random()
    hint_pool = [
        CardHint("卡名字数", f"{card.name_length} 字"),
        CardHint("类型", card.type_label),
        CardHint("来源", card.source_label),
        CardHint("费用", card.cost),
    ]
    selected_indexes = sorted(game_rng.sample(range(len(hint_pool)), 3))
    initial_hints = [hint_pool[index] for index in selected_indexes]
    delayed_index = next(index for index in range(len(hint_pool)) if index not in selected_indexes)
    delayed_hint = hint_pool[delayed_index]
    hidden_description_indexes = {
        index for index, char in enumerate(card.description) if should_hide_description_char(char)
    }
    reveal_batches = build_reveal_batches(hidden_description_indexes, rng=game_rng)
    return GuessGameState(
        card=card,
        initial_hints=initial_hints,
        delayed_hint=delayed_hint,
        hidden_description_indexes=hidden_description_indexes,
        reveal_batches=reveal_batches,
        rng=game_rng,
    )


def normalize_guess_text(text: str) -> str:
    return "".join(text.split()).casefold()


def extract_command(text: str) -> tuple[Optional[str], Optional[str]]:
    stripped = text.strip()
    if not stripped:
        return None, None
    if not stripped.startswith("/"):
        return None, None
    parts = stripped.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1].strip()


def build_help_message() -> str:
    return (
        "猜卡功能用法：\n"
        "1. `@机器人 /猜卡`：在当前群开始一局杀戮尖塔猜卡。\n"
        "2. 开局会给出 3 条基础提示，10 秒后补第 4 条，之后每 60 秒按初始隐藏字总数的 25% 揭示一批描述文字，共揭示 4 批。\n"
        "3. 猜测格式：`@机器人 卡牌完整名称`。\n"
        "4. `@机器人 /提示`：立即跳到下一阶段提示，并将计时重置为 60 秒。\n"
        "5. `@机器人 /结束猜卡`：直接结束当前群的这一局。\n"
        "6. `@机器人 /猜卡测试 卡牌名或ID`：管理员指定卡牌开测试局，方便排查与验收。\n"
        "7. `/猜卡 help`：查看本帮助。\n"
        "说明：同一个群同一时间只能进行一局猜卡。"
    )


def normalize_cost(cost: str) -> str:
    stripped = cost.strip()
    return stripped or "未知"


def normalize_description_text(text: str) -> str:
    return "".join(char for char in text.strip() if not char.isspace())


def render_masked_description(description: str, revealed_indexes: Iterable[int]) -> str:
    revealed_set = set(revealed_indexes)
    tokens: list[str] = []
    for index, char in enumerate(description):
        if should_hide_description_char(char) and index not in revealed_set:
            tokens.append("_")
        else:
            tokens.append(char)
    return " ".join(tokens)


def build_reveal_batches(
    hidden_indexes: Iterable[int],
    rng: Optional[random.Random] = None,
    batch_count: int = 4,
) -> list[tuple[int, ...]]:
    indexes = list(hidden_indexes)
    if not indexes or batch_count <= 0:
        return []

    shuffle_rng = rng or random.Random()
    shuffle_rng.shuffle(indexes)
    total = len(indexes)
    base_size = total // batch_count
    extra = total % batch_count
    batches: list[tuple[int, ...]] = []
    start = 0
    for batch_index in range(batch_count):
        size = base_size + (1 if batch_index < extra else 0)
        end = start + size
        batches.append(tuple(indexes[start:end]))
        start = end
    return batches


def should_hide_description_char(char: str) -> bool:
    if not char or char.isspace():
        return False
    if char.isdigit():
        return False
    category = unicodedata.category(char)
    if category.startswith("P") or category.startswith("S"):
        return False
    return True


def _normalize_type_label(value: object) -> Optional[str]:
    if value is None:
        return None
    normalized = TYPE_LABELS.get(str(value).strip().casefold())
    if normalized is not None:
        return normalized
    return TYPE_LABELS.get(str(value).strip())


def _normalize_source_label(
    value: object,
    *,
    character_zh: object = None,
    pool: object = None,
    character: object = None,
    fallback: Optional[str] = None,
) -> str:
    source_pool = _normalize_source_token(value)
    if source_pool == "colorless":
        return "无色"
    if source_pool in {"event", "curse", "status"}:
        return "其他"

    for candidate in (character_zh, pool, character, value):
        normalized = _map_source_candidate(candidate)
        if normalized is not None:
            return normalized

    return fallback or "其他"


def _normalize_source_token(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.casefold()


def _map_source_candidate(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = SOURCE_LABELS.get(text.casefold())
    if normalized is not None:
        return normalized
    return SOURCE_LABELS.get(text)
