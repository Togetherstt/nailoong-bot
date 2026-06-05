import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins.group_daily_stats import (
    _filter_excluded_member_interaction,
    _seconds_until_next_daily_flush,
)
from src.plugins.group_daily_stats.store import (
    InteractionStats,
    GroupDailyStatsStore,
    extract_interaction_stats,
)


class GroupDailyStatsStoreTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self.store = GroupDailyStatsStore(state_path=self.state_path)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_enable_and_record_snapshot(self) -> None:
        changed = await self.store.enable_group("10001", today="2026-06-05")
        self.assertTrue(changed)

        await self.store.record_interaction(
            group_id="10001",
            sender_id="20001",
            interaction=extract_interaction_stats(
                Message(
                    [
                        MessageSegment.at("30001"),
                        MessageSegment.face(123),
                    ]
                ),
                self_id="99999",
            ),
            today="2026-06-05",
        )

        snapshot = await self.store.get_group_snapshot("10001", today="2026-06-05")
        self.assertTrue(snapshot.enabled)
        self.assertEqual(snapshot.mentions_received, {"30001": 1})
        self.assertEqual(snapshot.stickers_sent, {"20001": 1})
        self.assertEqual(snapshot.sticker_targets, {"30001": 1})

    async def test_record_homophone_usage(self) -> None:
        await self.store.enable_group("10001", today="2026-06-05")
        await self.store.record_homophone_usage(
            group_id="10001",
            user_id="20001",
            boxed_member_ids=["30001", "30002", "30001"],
            today="2026-06-05",
        )

        snapshot = await self.store.get_group_snapshot("10001", today="2026-06-05")
        self.assertEqual(snapshot.homophone_used, {"20001": 1})
        self.assertEqual(snapshot.homophone_boxed, {"30001": 2, "30002": 1})

    async def test_flush_resets_day_but_keeps_enabled_groups(self) -> None:
        await self.store.enable_group("10001", today="2026-06-05")
        await self.store.record_homophone_usage(
            group_id="10001",
            user_id="20001",
            boxed_member_ids=["30001"],
            today="2026-06-05",
        )

        flush_result = await self.store.flush_daily_snapshots(
            today="2026-06-05",
            next_day="2026-06-06",
        )
        self.assertEqual(flush_result.date, "2026-06-05")
        self.assertIn("10001", flush_result.snapshots)
        self.assertEqual(flush_result.snapshots["10001"].homophone_boxed, {"30001": 1})

        snapshot = await self.store.get_group_snapshot("10001", today="2026-06-06")
        self.assertTrue(snapshot.enabled)
        self.assertEqual(snapshot.date, "2026-06-06")
        self.assertEqual(snapshot.homophone_boxed, {})

    async def test_disable_group_removes_current_stats(self) -> None:
        await self.store.enable_group("10001", today="2026-06-05")
        await self.store.record_homophone_usage(
            group_id="10001",
            user_id="20001",
            boxed_member_ids=["30001"],
            today="2026-06-05",
        )

        changed = await self.store.disable_group("10001", today="2026-06-05")
        self.assertTrue(changed)

        snapshot = await self.store.get_group_snapshot("10001", today="2026-06-05")
        self.assertFalse(snapshot.enabled)
        self.assertEqual(snapshot.homophone_used, {})


class GroupDailyStatsHelperTestCase(unittest.TestCase):
    def test_extract_interaction_stats_ignores_bot_and_counts_stickers(self) -> None:
        message = Message(
            [
                MessageSegment.at("10086"),
                MessageSegment.at("all"),
                MessageSegment.at("123456"),
                MessageSegment.face(14),
                MessageSegment("marketface", {"id": "77"}),
            ]
        )
        stats = extract_interaction_stats(message, self_id="10086")
        self.assertEqual(stats.mention_targets, ["123456"])
        self.assertEqual(stats.sticker_count, 2)
        self.assertEqual(stats.sticker_targets, ["123456", "123456"])

    def test_seconds_until_next_daily_flush_is_positive(self) -> None:
        from datetime import datetime

        seconds = _seconds_until_next_daily_flush(
            datetime.fromisoformat("2026-06-05T23:59:58+08:00")
        )
        self.assertGreaterEqual(seconds, 1.0)


class GroupDailyStatsExcludeMemberTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_filter_excluded_member_interaction_drops_excluded_targets(self) -> None:
        bot = _FakeStatsBot(
            {
                "20001": {"card": "", "nickname": "普通人"},
                "30001": {"card": "ARClass", "nickname": ""},
                "30002": {"card": "", "nickname": "Yurisaki"},
                "30003": {"card": "", "nickname": "保留对象"},
            }
        )
        interaction = InteractionStats(
            mention_targets=["30001", "30002", "30003"],
            sticker_count=2,
            sticker_targets=["30001", "30001", "30002", "30003", "30003"],
        )

        filtered = await _filter_excluded_member_interaction(
            bot=bot,
            group_id="10001",
            sender_id="20001",
            interaction=interaction,
        )

        self.assertEqual(filtered.mention_targets, ["30003"])
        self.assertEqual(filtered.sticker_count, 2)
        self.assertEqual(filtered.sticker_targets, ["30003", "30003"])

    async def test_filter_excluded_member_interaction_drops_excluded_sender_stats(self) -> None:
        bot = _FakeStatsBot(
            {
                "20001": {"card": "ARClass", "nickname": ""},
                "30003": {"card": "", "nickname": "保留对象"},
            }
        )
        interaction = InteractionStats(
            mention_targets=["30003"],
            sticker_count=1,
            sticker_targets=["30003"],
        )

        filtered = await _filter_excluded_member_interaction(
            bot=bot,
            group_id="10001",
            sender_id="20001",
            interaction=interaction,
        )

        self.assertEqual(filtered.mention_targets, [])
        self.assertEqual(filtered.sticker_count, 0)
        self.assertEqual(filtered.sticker_targets, [])
        self.assertTrue(filtered.is_empty())


class _FakeStatsBot:
    def __init__(self, members: dict[str, dict[str, str]]) -> None:
        self._members = members

    async def get_group_member_info(self, group_id: int, user_id: int, no_cache: bool = False):
        _ = group_id, no_cache
        return SimpleNamespace(**self._members.get(str(user_id), {"card": "", "nickname": ""})).__dict__
