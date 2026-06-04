import json
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from random import Random

CORE_PATH = Path(__file__).resolve().parent.parent / "src" / "plugins" / "sts_card_guess" / "core.py"
CORE_SPEC = importlib.util.spec_from_file_location("sts_card_guess_core_for_tests", CORE_PATH)
assert CORE_SPEC is not None and CORE_SPEC.loader is not None
core = importlib.util.module_from_spec(CORE_SPEC)
sys.modules[CORE_SPEC.name] = core
CORE_SPEC.loader.exec_module(core)

CardRecord = core.CardRecord
CardRepository = core.CardRepository
build_help_message = core.build_help_message
build_reveal_batches = core.build_reveal_batches
create_game_state = core.create_game_state
extract_command = core.extract_command
load_cards_from_dir = core.load_cards_from_dir
render_masked_description = core.render_masked_description
should_hide_description_char = core.should_hide_description_char


class StsCardGuessRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "attack").mkdir()
        payload = {
            "card_type": "attack",
            "source_pool": "ironclad",
            "cards": [
                {
                    "id": "rampage",
                    "name": "暴走",
                    "description": "造成9点伤害。 将这张牌在本场战斗中的伤害增加5。",
                    "cost": "1",
                    "character_zh": "铁甲战士",
                    "type_zh": "攻击",
                },
                {
                    "id": "event_card",
                    "name": "路人牌",
                    "description": "造成1点伤害。",
                    "cost": "0",
                    "character_zh": "事件",
                    "type_zh": "攻击",
                },
            ],
        }
        (root / "attack" / "ironclad.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        self.root = root

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_cards_from_dir_filters_and_maps(self) -> None:
        cards = load_cards_from_dir(self.root)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].name, "暴走")
        self.assertEqual(cards[0].type_label, "攻击")
        self.assertEqual(cards[0].source_label, "铁血战士")

    def test_repository_find_card_by_name_and_id(self) -> None:
        repository = CardRepository(self.root)
        self.assertEqual(repository.find_card("暴走").card_id, "rampage")
        self.assertEqual(repository.find_card("rampage").name, "暴走")


class StsCardGuessHelpersTestCase(unittest.TestCase):
    def test_should_hide_description_char(self) -> None:
        self.assertTrue(should_hide_description_char("造"))
        self.assertTrue(should_hide_description_char("A"))
        self.assertFalse(should_hide_description_char("9"))
        self.assertFalse(should_hide_description_char("。"))

    def test_render_masked_description_separates_underscores(self) -> None:
        rendered = render_masked_description("造成9点伤害。", set())
        self.assertEqual(rendered, "_ _ 9 _ _ _ 。")

    def test_create_game_state_uses_name_length_as_hint_pool_member(self) -> None:
        state = create_game_state(
            CardRecord(
                card_id="rampage",
                name="暴走",
                description="造成9点伤害。",
                type_label="攻击",
                source_label="铁血战士",
                cost="1",
            ),
            rng=Random(1),
        )
        all_labels = {hint.label for hint in state.initial_hints} | {state.delayed_hint.label}
        self.assertEqual(all_labels, {"卡名字数", "类型", "来源", "费用"})
        self.assertEqual(len(state.reveal_batches), 4)

    def test_advance_reveals_delayed_hint_then_description(self) -> None:
        state = create_game_state(
            CardRecord(
                card_id="rampage",
                name="暴走",
                description="造成9点伤害。",
                type_label="攻击",
                source_label="铁血战士",
                cost="1",
            ),
            rng=Random(2),
        )
        first = state.advance()
        self.assertFalse(first.finished)
        self.assertTrue(state.delayed_hint_revealed)
        second = state.advance()
        self.assertFalse(second.finished)
        self.assertGreater(len(state.revealed_description_indexes), 0)

    def test_reveal_batches_cover_hidden_indexes_in_four_fixed_rounds(self) -> None:
        batches = build_reveal_batches([1, 2, 3, 4, 5, 6, 7, 8, 9], rng=Random(1))
        self.assertEqual(len(batches), 4)
        flattened = [index for batch in batches for index in batch]
        self.assertEqual(set(flattened), {1, 2, 3, 4, 5, 6, 7, 8, 9})
        self.assertEqual(len(flattened), 9)

    def test_game_finishes_after_four_description_reveal_rounds(self) -> None:
        state = create_game_state(
            CardRecord(
                card_id="rampage",
                name="暴走",
                description="造成9点伤害。",
                type_label="攻击",
                source_label="铁血战士",
                cost="1",
            ),
            rng=Random(4),
        )
        state.advance()
        for _ in range(3):
            result = state.advance()
            self.assertFalse(result.finished)
        final_result = state.advance()
        self.assertTrue(final_result.finished)
        self.assertIn("本局猜卡失败", final_result.message)

    def test_game_state_check_guess(self) -> None:
        state = create_game_state(
            CardRecord(
                card_id="rampage",
                name="暴走",
                description="造成9点伤害。",
                type_label="攻击",
                source_label="铁血战士",
                cost="1",
            ),
            rng=Random(3),
        )
        self.assertTrue(state.check_guess("暴走"))
        self.assertFalse(state.check_guess("暴走+"))

    def test_extract_command(self) -> None:
        self.assertEqual(extract_command("/猜卡"), ("/猜卡", None))
        self.assertEqual(extract_command("/猜卡测试 rampage"), ("/猜卡测试", "rampage"))
        self.assertEqual(extract_command("暴走"), (None, None))

    def test_help_message_mentions_test_command(self) -> None:
        help_message = build_help_message()
        self.assertIn("/猜卡 help", help_message)
        self.assertIn("/猜卡测试", help_message)
        self.assertIn("25%", help_message)
