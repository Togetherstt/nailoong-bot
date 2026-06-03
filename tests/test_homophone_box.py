import importlib
import tempfile
import unittest
from pathlib import Path
from random import Random

homophone_plugin = importlib.import_module("src.plugins.homophone_box")
from src.plugins.homophone_box.store import (  # noqa: E402
    InitialsEntry,
    InitialsStore,
    extract_chinese_text,
    extract_homophone_tokens,
    find_homophone_matches,
    find_homophone_results,
    is_valid_quote_text,
    limit_homophone_results,
    normalize_initials,
    rotate_homophone_results,
)


class InitialsStoreTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index_path = Path(self.temp_dir.name) / "initials.json"
        self.store = InitialsStore(index_path=self.index_path)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_add_initials_persists_data(self) -> None:
        inserted = await self.store.add_initials("yzh")
        self.assertTrue(inserted)
        self.assertEqual(await self.store.list_initials(), ["yzh"])

    async def test_add_initials_rejects_duplicate(self) -> None:
        await self.store.add_initials("yzh")
        inserted = await self.store.add_initials("yzh")
        self.assertFalse(inserted)
        self.assertEqual(await self.store.count(), 1)

    async def test_delete_initials(self) -> None:
        await self.store.add_initials("yzh")
        deleted = await self.store.delete_initials("yzh")
        self.assertTrue(deleted)
        self.assertEqual(await self.store.list_initials(), [])

    async def test_add_initials_with_bound_member(self) -> None:
        inserted = await self.store.add_initials("yzh", member_qq="123456")
        self.assertTrue(inserted)
        self.assertEqual(
            await self.store.list_entries(),
            [InitialsEntry(initials="yzh", member_qq="123456")],
        )

    async def test_bind_member_updates_existing_entry(self) -> None:
        await self.store.add_initials("yzh")
        updated = await self.store.bind_member("yzh", "123456")
        self.assertTrue(updated)
        self.assertEqual(
            await self.store.list_entries(),
            [InitialsEntry(initials="yzh", member_qq="123456")],
        )

    async def test_unbind_member_keeps_initials(self) -> None:
        await self.store.add_initials("yzh", member_qq="123456")
        updated = await self.store.unbind_member("yzh")
        self.assertTrue(updated)
        self.assertEqual(
            await self.store.list_entries(),
            [InitialsEntry(initials="yzh", member_qq=None)],
        )


class HomophoneHelperTestCase(unittest.TestCase):
    def test_normalize_initials(self) -> None:
        self.assertEqual(normalize_initials(" YZH "), "yzh")
        self.assertEqual(normalize_initials(" yz "), "yz")
        self.assertEqual(normalize_initials(" yzzhh "), "yzzhh")
        self.assertIsNone(normalize_initials("y"))
        self.assertIsNone(normalize_initials("yzh1"))
        self.assertIsNone(normalize_initials("yzzhhh"))

    def test_extract_chinese_text(self) -> None:
        self.assertEqual(extract_chinese_text("abc杨知寒123"), "杨知寒")

    def test_extract_homophone_tokens_supports_mixed_text(self) -> None:
        tokens = extract_homophone_tokens("杨abc之魂")
        self.assertEqual([token.text for token in tokens], ["杨", "abc", "之", "魂"])
        self.assertEqual([token.initial for token in tokens], ["y", "a", "z", "h"])

    def test_is_valid_quote_text(self) -> None:
        self.assertTrue(is_valid_quote_text("杨知寒"))
        self.assertFalse(is_valid_quote_text(""))
        self.assertTrue(is_valid_quote_text("这是一个三十个字以内的测试文本用于放宽长度限制"))
        self.assertFalse(
            is_valid_quote_text(
                "这是一个超过四十个中文字符长度限制的测试文本内容用于验证现在的新长度边界确实已经生效"
            )
        )

    def test_find_homophone_results(self) -> None:
        results = find_homophone_results("杨州话真的好", ["yzh"])
        self.assertIn("杨州话", results)

    def test_find_homophone_results_supports_two_letters(self) -> None:
        results = find_homophone_results("杨魂真好", ["yh"])
        self.assertIn("杨魂", results)

    def test_find_homophone_results_returns_all_matches(self) -> None:
        results = find_homophone_results("杨枝花与银之魂", ["yzh"])
        self.assertIn("杨枝花", results)
        self.assertIn("银之魂", results)

    def test_find_homophone_results_supports_four_letters(self) -> None:
        results = find_homophone_results("杨枝花魂真不错", ["yzhh"])
        self.assertIn("杨枝花魂", results)

    def test_find_homophone_results_supports_five_letters(self) -> None:
        results = find_homophone_results("杨枝花魂龙真的好", ["yzhhl"])
        self.assertIn("杨枝花魂龙", results)

    def test_find_homophone_results_preserves_order(self) -> None:
        results = find_homophone_results("魂之杨", ["yzh"])
        self.assertEqual(results, [])

    def test_find_homophone_results_supports_mixed_english_and_chinese(self) -> None:
        results = find_homophone_results("杨abc魂", ["yah"])
        self.assertIn("杨abc魂", results)

    def test_find_homophone_matches_keep_bound_member(self) -> None:
        matches = find_homophone_matches(
            "杨州话真的好",
            [InitialsEntry(initials="yzh", member_qq="123456")],
        )
        self.assertTrue(matches)
        self.assertIn("杨州话", [match.candidate for match in matches])
        self.assertTrue(all(match.member_qq == "123456" for match in matches))

    def test_limit_homophone_results(self) -> None:
        results = [f"result{i}" for i in range(12)]
        limited = limit_homophone_results(results, rng=Random(123))
        self.assertEqual(len(limited), 10)
        self.assertEqual(len(set(limited)), 10)
        self.assertTrue(set(limited).issubset(set(results)))
        self.assertEqual(limited, limit_homophone_results(results, rng=Random(123)))
        self.assertEqual(limit_homophone_results(results[:3], limit=10), results[:3])
        self.assertEqual(limit_homophone_results(results, limit=0), [])

    def test_rotate_homophone_results_avoids_same_batch_when_possible(self) -> None:
        state_map = {}
        results = [f"result{i}" for i in range(12)]
        first = rotate_homophone_results("same-key", results, state_map, rng=Random(1))
        second = rotate_homophone_results("same-key", results, state_map, rng=Random(1))
        self.assertEqual(len(first), 10)
        self.assertEqual(len(second), 10)
        self.assertNotEqual(first, second)
        self.assertEqual(len(set(first) | set(second)), 12)

    def test_quote_cooldown_detection(self) -> None:
        homophone_plugin.recent_quote_hits.clear()
        homophone_plugin.recent_quote_hits["quote-1"] = 100.0
        self.assertTrue(homophone_plugin._is_quote_in_cooldown("quote-1", now=104.0))
        self.assertFalse(homophone_plugin._is_quote_in_cooldown("quote-1", now=105.1))

    def test_prune_recent_quote_hits(self) -> None:
        homophone_plugin.recent_quote_hits.clear()
        homophone_plugin.recent_quote_hits.update(
            {
                "expired": 100.0,
                "fresh": 103.0,
            }
        )
        homophone_plugin._prune_recent_quote_hits(now=105.1)
        self.assertNotIn("expired", homophone_plugin.recent_quote_hits)
        self.assertIn("fresh", homophone_plugin.recent_quote_hits)

    def test_allowed_group_ids_parser(self) -> None:
        class DummyConfig:
            homophone_group_ids = "123, 456 ,789"

        original_get_driver = homophone_plugin.get_driver
        homophone_plugin.get_driver = lambda: type("DummyDriver", (), {"config": DummyConfig()})()
        try:
            self.assertEqual(
                homophone_plugin._get_allowed_homophone_group_ids(),
                {"123", "456", "789"},
            )
        finally:
            homophone_plugin.get_driver = original_get_driver
