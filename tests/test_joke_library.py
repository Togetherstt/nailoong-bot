import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins.joke_library.store import (
    JokeContentSegment,
    JokeStore,
    PreparedImageFile,
    PreparedJoke,
    detect_auto_tag,
    extract_command,
    is_valid_fuzzy_query,
    normalize_tag,
    parse_joke_id,
    prepare_joke_from_message,
    prepare_joke_from_reply,
)


class FixedChoiceRandom:
    @staticmethod
    def choice(items):
        return items[-1]


class JokeLibraryHelperTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_joke_from_text_reply(self) -> None:
        prepared = await prepare_joke_from_reply(Message("你好呀 世界"), _fake_image_downloader)
        self.assertEqual(prepared.plain_text, "你好呀 世界")
        self.assertEqual(len(prepared.segments), 1)
        self.assertEqual(prepared.segments[0].segment_type, "text")
        self.assertEqual(prepared.image_files, [])

    async def test_prepare_joke_from_image_reply_builds_digest(self) -> None:
        reply = Message([MessageSegment.image("https://example.com/test.png"), MessageSegment.text("看这个")])
        prepared = await prepare_joke_from_reply(reply, _fake_image_downloader)
        self.assertEqual(len(prepared.image_files), 1)
        self.assertEqual(prepared.segments[0].kind, "local_image")
        self.assertEqual(prepared.segments[1].segment_type, "text")

    async def test_prepare_joke_from_message_works_for_direct_message(self) -> None:
        message = Message([MessageSegment.text("张老师今天又来了"), MessageSegment.face(123)])
        prepared = await prepare_joke_from_message(message, _fake_image_downloader)
        self.assertEqual(prepared.plain_text, "张老师今天又来了")
        self.assertEqual(len(prepared.segments), 2)
        self.assertEqual(prepared.segments[0].segment_type, "text")
        self.assertEqual(prepared.segments[1].segment_type, "face")

    def test_extract_command(self) -> None:
        self.assertEqual(extract_command("/随机笑话"), ("/随机笑话", None))
        self.assertEqual(extract_command("/随机笑话 校园"), ("/随机笑话", "校园"))

    def test_normalize_tag(self) -> None:
        self.assertEqual(normalize_tag("  校园  梗 "), "校园 梗")
        self.assertIsNone(normalize_tag("   "))

    def test_is_valid_fuzzy_query(self) -> None:
        self.assertTrue(is_valid_fuzzy_query("晚风"))
        self.assertFalse(is_valid_fuzzy_query(""))
        self.assertFalse(is_valid_fuzzy_query("a" * 11))

    def test_parse_joke_id(self) -> None:
        self.assertEqual(parse_joke_id("12"), 12)
        self.assertIsNone(parse_joke_id("0"))
        self.assertIsNone(parse_joke_id("abc"))

    def test_detect_auto_tag(self) -> None:
        self.assertEqual(detect_auto_tag("今天张老师又来了"), "张雪峰")
        self.assertEqual(detect_auto_tag("这也太巧乐兹了"), "张雪峰")
        self.assertEqual(detect_auto_tag("普通文本"), None)


class JokeStoreTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = JokeStore(
            index_path=base / "index.json",
            image_dir=base / "images",
            rng=FixedChoiceRandom(),
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_add_prepared_joke_assigns_incremental_id(self) -> None:
        first, created_first = await self.store.add_prepared_joke(
            _build_text_joke("今天天气热"),
            provided_by="10001",
            tag="天气",
            provided_at=datetime(2026, 6, 4, 12, 0, 0),
        )
        second, created_second = await self.store.add_prepared_joke(
            _build_text_joke("明天更热"),
            provided_by="10002",
            tag=None,
            provided_at=datetime(2026, 6, 4, 12, 1, 0),
        )
        self.assertTrue(created_first)
        self.assertTrue(created_second)
        self.assertEqual(first.id, 1)
        self.assertEqual(second.id, 2)
        self.assertEqual(first.display_tag, "天气")
        self.assertEqual(second.display_tag, "通用")

    async def test_add_prepared_joke_deduplicates_by_fingerprint(self) -> None:
        first, created_first = await self.store.add_prepared_joke(
            _build_text_joke("重复笑话"),
            provided_by="10001",
            tag="甲",
        )
        second, created_second = await self.store.add_prepared_joke(
            _build_text_joke("重复笑话"),
            provided_by="10002",
            tag="乙",
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.tag, second.tag)

    async def test_add_prepared_joke_can_store_auto_detected_tag(self) -> None:
        record, created = await self.store.add_prepared_joke(
            _build_text_joke("张雪峰和雪碧都在这里"),
            provided_by="10001",
            tag=detect_auto_tag("张雪峰和雪碧都在这里"),
        )
        self.assertTrue(created)
        self.assertEqual(record.tag, "张雪峰")

    async def test_random_record_supports_tag_filter(self) -> None:
        await self.store.add_prepared_joke(_build_text_joke("通用一"), provided_by="1", tag=None)
        await self.store.add_prepared_joke(_build_text_joke("标签一"), provided_by="2", tag="校园")
        await self.store.add_prepared_joke(_build_text_joke("标签二"), provided_by="3", tag="校园")
        record = await self.store.random_record(tag="校园")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.display_tag, "校园")
        self.assertEqual(record.plain_text, "标签二")

    async def test_find_by_id_returns_expected_record(self) -> None:
        await self.store.add_prepared_joke(_build_text_joke("第一条"), provided_by="1", tag=None)
        target, _ = await self.store.add_prepared_joke(_build_text_joke("第二条"), provided_by="2", tag="标签")
        found = await self.store.find_by_id(target.id)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.plain_text, "第二条")

    async def test_fuzzy_find_prefers_substring_match(self) -> None:
        await self.store.add_prepared_joke(_build_text_joke("今晚的晚风很温柔"), provided_by="1", tag=None)
        await self.store.add_prepared_joke(_build_text_joke("今天天气很好"), provided_by="2", tag=None)
        found = await self.store.fuzzy_find("晚风")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.plain_text, "今晚的晚风很温柔")

    async def test_delete_by_id_removes_image_files(self) -> None:
        prepared = PreparedJoke(
            fingerprint="fp-image",
            plain_text="图片笑话",
            segments=[JokeContentSegment(kind="local_image", image_filename="a.png")],
            image_files=[PreparedImageFile(filename="a.png", content=b"abc")],
        )
        record, _ = await self.store.add_prepared_joke(prepared, provided_by="1", tag=None)
        self.assertTrue((self.store.image_dir / "a.png").exists())
        deleted = await self.store.delete_by_id(record.id)
        self.assertIsNotNone(deleted)
        self.assertFalse((self.store.image_dir / "a.png").exists())

    async def test_random_message_only_keeps_id_metadata(self) -> None:
        record, _ = await self.store.add_prepared_joke(
            _build_text_joke("只看编号"),
            provided_by="1",
            tag="测试",
        )
        rendered = str(record.to_message(metadata_mode="id_only"))
        self.assertIn("笑话编号", rendered)
        self.assertNotIn("归属标签", rendered)
        self.assertNotIn("提供人QQ", rendered)


def _build_text_joke(text: str) -> PreparedJoke:
    return PreparedJoke(
        fingerprint=f"fp:{text}",
        plain_text=text,
        segments=[JokeContentSegment(kind="segment", segment_type="text", segment_data={"text": text})],
    )


async def _fake_image_downloader(url: str) -> bytes:
    return f"image:{url}".encode("utf-8")
