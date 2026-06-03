import json
import tempfile
import unittest
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message

from src.plugins.nailoong_memory.store import (
    NailoongStore,
    build_record_dict,
    extract_command_name,
    find_image_url,
    find_storable_segment,
    format_record_line,
    guess_extension_from_url,
)


class NailoongStoreTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.store = NailoongStore(
            index_path=self.base_path / "index.json",
            image_dir=self.base_path / "images",
            trash_dir=self.base_path / "trash",
            deleted_index_path=self.base_path / "deleted.json",
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_add_record_persists_metadata_and_image(self) -> None:
        record = await self.store.add_record(
            b"fake-image",
            added_by="123456",
            original_name="开心奶龙",
            file_extension=".png",
        )

        self.assertEqual(record.display_name, "开心奶龙")
        self.assertTrue(self.store.message_for_record(record))
        self.assertTrue((self.base_path / "images" / record.image_filename).exists())

        payload = json.loads(self.store.index_path.read_text("utf-8"))
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["added_by"], "123456")
        self.assertEqual(payload[0]["original_name"], "开心奶龙")

    async def test_random_record_returns_none_when_empty(self) -> None:
        self.assertIsNone(await self.store.random_record())

    async def test_random_record_returns_existing_entry(self) -> None:
        await self.store.add_record(
            b"img1",
            added_by="1",
            original_name=None,
            file_extension=".jpg",
        )
        record = await self.store.random_record()
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.added_by, "1")

    async def test_add_segment_record_persists_face_segment(self) -> None:
        segment = find_storable_segment(Message("[CQ:face,id=123]"))
        assert segment is not None

        record = await self.store.add_segment_record(
            segment,
            added_by="8888",
            original_name="奶龙表情",
        )

        self.assertEqual(record.media_kind, "segment")
        self.assertEqual(record.segment_type, "face")
        payload = json.loads(self.store.index_path.read_text("utf-8"))
        self.assertEqual(payload[0]["segment_type"], "face")
        self.assertEqual(payload[0]["segment_data"]["id"], "123")

    async def test_delete_by_index_removes_record_and_file(self) -> None:
        record = await self.store.add_record(
            b"img-delete",
            added_by="10001",
            original_name="待删除奶龙",
            file_extension=".png",
        )
        file_path = self.base_path / "images" / record.image_filename
        self.assertTrue(file_path.exists())

        deleted = await self.store.delete_by_index(1)
        self.assertIsNotNone(deleted)
        self.assertFalse(file_path.exists())
        self.assertEqual(await self.store.count(), 0)

    async def test_delete_by_name_removes_all_matching_records(self) -> None:
        segment_one = find_storable_segment(Message("[CQ:face,id=1]"))
        segment_two = find_storable_segment(Message("[CQ:face,id=2]"))
        segment_three = find_storable_segment(Message("[CQ:face,id=3]"))
        assert segment_one is not None
        assert segment_two is not None
        assert segment_three is not None

        await self.store.add_segment_record(
            segment_one,
            added_by="1",
            original_name="同名奶龙",
        )
        await self.store.add_segment_record(
            segment_two,
            added_by="2",
            original_name="同名奶龙",
        )
        await self.store.add_segment_record(
            segment_three,
            added_by="3",
            original_name="别的奶龙",
        )

        deleted = await self.store.delete_by_name("同名奶龙")
        self.assertEqual(len(deleted), 2)

        records = await self.store.list_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].display_name, "别的奶龙")

    async def test_delete_latest_removes_last_record(self) -> None:
        first_segment = find_storable_segment(Message("[CQ:face,id=10]"))
        second_segment = find_storable_segment(Message("[CQ:face,id=20]"))
        assert first_segment is not None
        assert second_segment is not None

        await self.store.add_segment_record(
            first_segment,
            added_by="1",
            original_name="第一个",
        )
        await self.store.add_segment_record(
            second_segment,
            added_by="2",
            original_name="第二个",
        )

        deleted = await self.store.delete_latest()
        self.assertIsNotNone(deleted)
        assert deleted is not None
        self.assertEqual(deleted.display_name, "第二个")

    async def test_undo_last_delete_restores_image_record_and_file(self) -> None:
        record = await self.store.add_record(
            b"img-restore",
            added_by="10002",
            original_name="可恢复奶龙",
            file_extension=".png",
        )
        original_path = self.base_path / "images" / record.image_filename
        trash_path = self.base_path / "trash" / record.image_filename

        deleted = await self.store.delete_by_index(1)
        self.assertIsNotNone(deleted)
        self.assertFalse(original_path.exists())
        self.assertTrue(trash_path.exists())

        restored = await self.store.undo_last_delete()
        self.assertIsNotNone(restored)
        self.assertTrue(original_path.exists())
        self.assertFalse(trash_path.exists())
        self.assertEqual(await self.store.count(), 1)

    async def test_undo_last_delete_restores_segment_record(self) -> None:
        segment = find_storable_segment(Message("[CQ:face,id=555]"))
        assert segment is not None
        await self.store.add_segment_record(
            segment,
            added_by="30003",
            original_name="段恢复奶龙",
        )

        await self.store.delete_latest()
        restored = await self.store.undo_last_delete()
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.display_name, "段恢复奶龙")
        self.assertEqual(await self.store.count(), 1)


class NailoongHelperTestCase(unittest.TestCase):
    def test_extract_command_name_with_argument(self) -> None:
        command, argument = extract_command_name(Message("/添加奶龙 大笑龙"))
        self.assertEqual(command, "/添加奶龙")
        self.assertEqual(argument, "大笑龙")

    def test_extract_command_name_without_argument(self) -> None:
        command, argument = extract_command_name(Message("/随机奶龙"))
        self.assertEqual(command, "/随机奶龙")
        self.assertIsNone(argument)

    def test_find_image_url_from_reply_message(self) -> None:
        message = Message("[CQ:image,file=https://example.com/nailoong.png]")
        self.assertEqual(find_image_url(message), "https://example.com/nailoong.png")

    def test_find_storable_segment_from_image(self) -> None:
        segment = find_storable_segment(
            Message("[CQ:image,file=https://example.com/nailoong.png]")
        )
        self.assertIsNotNone(segment)
        assert segment is not None
        self.assertEqual(segment.type, "image")

    def test_find_storable_segment_from_face(self) -> None:
        segment = find_storable_segment(Message("[CQ:face,id=321]"))
        self.assertIsNotNone(segment)
        assert segment is not None
        self.assertEqual(segment.type, "face")

    def test_find_storable_segment_from_mface(self) -> None:
        segment = find_storable_segment(
            Message("[CQ:mface,summary=奶龙,emoji_id=12345,url=https://example.com/a.png]")
        )
        self.assertIsNotNone(segment)
        assert segment is not None
        self.assertEqual(segment.type, "mface")

    def test_guess_extension_from_url(self) -> None:
        self.assertEqual(
            guess_extension_from_url("https://example.com/foo/bar.gif?x=1"),
            ".gif",
        )

    def test_build_record_dict_is_serializable(self) -> None:
        record = self._make_record()
        payload = build_record_dict(record)
        self.assertEqual(payload["added_by"], "42")

    def test_format_record_line(self) -> None:
        record = self._make_record()
        line = format_record_line(3, record)
        self.assertIn("3.", line)
        self.assertIn("测试奶龙", line)
        self.assertIn("添加者:42", line)

    @staticmethod
    def _make_record():
        from src.plugins.nailoong_memory.store import NailoongRecord

        return NailoongRecord(
            id="abc",
            image_filename="abc.png",
            added_by="42",
            added_at="2026-06-03 02:31:00",
            original_name="测试奶龙",
        )
