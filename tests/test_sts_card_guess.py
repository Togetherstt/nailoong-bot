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
        (root / "skill").mkdir()
        (root / "power").mkdir()
        (root / "curse").mkdir()
        (root / "status").mkdir()
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
        (root / "attack" / "defect.json").write_text(
            json.dumps(
                {
                    "card_type": "attack",
                    "source_pool": "defect",
                    "cards": [
                        {
                            "id": "zap",
                            "name": "电击",
                            "description": "造成7点伤害。",
                            "cost": "1",
                            "character_zh": "机器人",
                            "type_zh": "攻击",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "skill" / "regent.json").write_text(
            json.dumps(
                {
                    "card_type": "skill",
                    "source_pool": "regent",
                    "cards": [
                        {
                            "id": "inheritance",
                            "name": "继承",
                            "description": "获得1点格挡。",
                            "cost": "1",
                            "character_zh": "继承者",
                            "type_zh": "技能",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "power" / "necrobinder.json").write_text(
            json.dumps(
                {
                    "card_type": "power",
                    "source_pool": "necrobinder",
                    "cards": [
                        {
                            "id": "death_bind",
                            "name": "死缚",
                            "description": "获得1层力量。",
                            "cost": "1",
                            "character_zh": "死亡缚者",
                            "type_zh": "能力",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "attack" / "colorless.json").write_text(
            json.dumps(
                {
                    "card_type": "attack",
                    "source_pool": "colorless",
                    "cards": [
                        {
                            "id": "boulder",
                            "name": "巨石",
                            "description": "造成12点伤害。",
                            "cost": "2",
                            "character_zh": "无色",
                            "type_zh": "攻击",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "curse" / "curse.json").write_text(
            json.dumps(
                {
                    "card_type": "curse",
                    "source_pool": "curse",
                    "cards": [
                        {
                            "id": "spore_mind",
                            "name": "孢子心灵",
                            "description": "无法打出。",
                            "cost": "0",
                            "character_zh": "无色",
                            "type_zh": "诅咒",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "status" / "status.json").write_text(
            json.dumps(
                {
                    "card_type": "status",
                    "source_pool": "status",
                    "cards": [
                        {
                            "id": "beckon",
                            "name": "召引",
                            "description": "回合结束时消失。",
                            "cost": "0",
                            "character_zh": "无色",
                            "type_zh": "状态",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.root = root

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_cards_from_dir_filters_and_maps(self) -> None:
        cards = load_cards_from_dir(self.root)
        self.assertEqual(len(cards), 8)
        source_by_name = {card.name: card.source_label for card in cards}
        type_by_name = {card.name: card.type_label for card in cards}
        self.assertEqual(source_by_name["暴走"], "铁血战士")
        self.assertEqual(type_by_name["暴走"], "攻击")
        self.assertEqual(source_by_name["路人牌"], "铁血战士")
        self.assertEqual(source_by_name["电击"], "故障机器人")
        self.assertEqual(source_by_name["继承"], "储君")
        self.assertEqual(source_by_name["死缚"], "亡灵契约师")
        self.assertEqual(source_by_name["巨石"], "无色")
        self.assertEqual(source_by_name["孢子心灵"], "其他")
        self.assertEqual(source_by_name["召引"], "其他")

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
