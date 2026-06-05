import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message
from PIL import Image

from src.plugins.nailoong_memory.store import (
    NailoongRecord,
    NailoongStore,
    build_dhash,
    build_record_dict,
    build_segment_fingerprint,
    build_sha256,
    extract_command_name,
    find_image_url,
    find_segment_image_url,
    find_storable_segment,
    format_record_line,
    guess_extension_from_url,
    hamming_distance,
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
        record, created = await self.store.add_record(
            _build_png_bytes("red"),
            added_by="123456",
            original_name="开心奶龙",
            file_extension=".png",
        )

        self.assertTrue(created)
        self.assertEqual(record.display_name, "开心奶龙")
        self.assertTrue(self.store.message_for_record(record))
        self.assertTrue((self.base_path / "images" / record.image_filename).exists())
        self.assertIsNotNone(record.image_sha256)
        self.assertIsNotNone(record.image_dhash)

        payload = json.loads(self.store.index_path.read_text("utf-8"))
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["added_by"], "123456")
        self.assertEqual(payload[0]["original_name"], "开心奶龙")

    async def test_add_record_deduplicates_by_sha256(self) -> None:
        first, created_first = await self.store.add_record(
            _build_png_bytes("red"),
            added_by="1",
            original_name="奶龙一号",
            file_extension=".png",
        )
        second, created_second = await self.store.add_record(
            _build_png_bytes("red"),
            added_by="2",
            original_name="奶龙二号",
            file_extension=".png",
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(await self.store.count(), 1)

    async def test_add_record_deduplicates_by_dhash_when_encoding_differs(self) -> None:
        first, created_first = await self.store.add_record(
            _build_png_bytes("blue"),
            added_by="1",
            original_name="蓝龙",
            file_extension=".png",
        )
        second, created_second = await self.store.add_record(
            _build_bmp_bytes("blue"),
            added_by="2",
            original_name="蓝龙复制",
            file_extension=".bmp",
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(await self.store.count(), 1)

    async def test_add_record_deduplicates_against_legacy_record_without_hash_fields(self) -> None:
        legacy_record = {
            "id": "legacy-red",
            "added_by": "10001",
            "added_at": "2026-06-04 01:40:00",
            "media_kind": "local_image",
            "image_filename": "legacy-red.png",
            "segment_type": None,
            "segment_data": None,
            "original_name": "旧奶龙",
        }
        image_path = self.base_path / "images" / "legacy-red.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(_build_png_bytes("red"))
        self.store.index_path.write_text(json.dumps([legacy_record], ensure_ascii=False, indent=2), "utf-8")

        record, created = await self.store.add_record(
            _build_png_bytes("red"),
            added_by="20002",
            original_name="新名字奶龙",
            file_extension=".png",
        )

        self.assertFalse(created)
        self.assertEqual(record.id, "legacy-red")
        payload = json.loads(self.store.index_path.read_text("utf-8"))
        self.assertEqual(payload[0]["id"], "legacy-red")
        self.assertIn("image_sha256", payload[0])
        self.assertIn("image_dhash", payload[0])

    async def test_random_record_returns_none_when_empty(self) -> None:
        self.assertIsNone(await self.store.random_record())

    async def test_random_record_returns_existing_entry(self) -> None:
        await self.store.add_record(
            _build_png_bytes("yellow"),
            added_by="1",
            original_name=None,
            file_extension=".jpg",
        )
        record = await self.store.random_record()
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.added_by, "1")

    async def test_find_by_index_returns_expected_record(self) -> None:
        await self.store.add_record(
            _build_png_bytes("yellow"),
            added_by="1",
            original_name="第一个奶龙",
            file_extension=".png",
        )
        await self.store.add_record(
            _build_split_png_bytes("green", "black"),
            added_by="2",
            original_name="第二个奶龙",
            file_extension=".png",
        )

        record = await self.store.find_by_index(2)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.display_name, "第二个奶龙")

    async def test_fuzzy_find_by_name_prefers_best_match(self) -> None:
        await self.store.add_record(
            _build_png_bytes("yellow"),
            added_by="1",
            original_name="开心奶龙",
            file_extension=".png",
        )
        await self.store.add_record(
            _build_png_bytes("green"),
            added_by="2",
            original_name="生气奶龙",
            file_extension=".png",
        )

        matched = await self.store.fuzzy_find_by_name("开心")
        self.assertIsNotNone(matched)
        assert matched is not None
        index, record = matched
        self.assertEqual(index, 1)
        self.assertEqual(record.display_name, "开心奶龙")

    async def test_forward_mode_setting_persists(self) -> None:
        self.assertFalse(await self.store.is_forward_mode_enabled("10001"))
        self.assertTrue(await self.store.set_forward_mode("10001", True))
        self.assertTrue(await self.store.is_forward_mode_enabled("10001"))
        self.assertFalse(await self.store.set_forward_mode("10001", True))
        self.assertTrue(await self.store.set_forward_mode("10001", False))
        self.assertFalse(await self.store.is_forward_mode_enabled("10001"))

    async def test_add_segment_record_persists_face_segment(self) -> None:
        segment = find_storable_segment(Message("[CQ:face,id=123]"))
        assert segment is not None

        record, created = await self.store.add_segment_record(
            segment,
            added_by="8888",
            original_name="奶龙表情",
        )

        self.assertTrue(created)
        self.assertEqual(record.media_kind, "segment")
        self.assertEqual(record.segment_type, "face")
        self.assertIsNotNone(record.content_fingerprint)
        payload = json.loads(self.store.index_path.read_text("utf-8"))
        self.assertEqual(payload[0]["segment_type"], "face")
        self.assertEqual(payload[0]["segment_data"]["id"], "123")

    async def test_add_segment_record_deduplicates_same_segment(self) -> None:
        segment = find_storable_segment(Message("[CQ:face,id=456]"))
        assert segment is not None

        first, created_first = await self.store.add_segment_record(
            segment,
            added_by="1",
            original_name="表情一",
        )
        second, created_second = await self.store.add_segment_record(
            segment,
            added_by="2",
            original_name="表情二",
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)

    async def test_delete_by_index_removes_record_and_file(self) -> None:
        record, _ = await self.store.add_record(
            _build_png_bytes("purple"),
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
        record, _ = await self.store.add_record(
            _build_png_bytes("orange"),
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

    def test_find_segment_image_url(self) -> None:
        segment = find_storable_segment(
            Message("[CQ:mface,summary=奶龙,emoji_id=12345,url=https://example.com/a.png]")
        )
        assert segment is not None
        self.assertEqual(find_segment_image_url(segment), "https://example.com/a.png")

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

    def test_build_sha256(self) -> None:
        self.assertEqual(build_sha256(b"abc"), build_sha256(b"abc"))
        self.assertNotEqual(build_sha256(b"abc"), build_sha256(b"abcd"))

    def test_build_dhash(self) -> None:
        image_bytes = _build_png_bytes("red")
        dhash, width, height = build_dhash(image_bytes)
        self.assertIsNotNone(dhash)
        self.assertEqual(width, 16)
        self.assertEqual(height, 16)

    def test_hamming_distance(self) -> None:
        self.assertEqual(hamming_distance("ff", "ff"), 0)
        self.assertEqual(hamming_distance("0f", "00"), 1)

    def test_build_segment_fingerprint(self) -> None:
        segment = find_storable_segment(Message("[CQ:face,id=123]"))
        assert segment is not None
        self.assertEqual(build_segment_fingerprint(segment), build_segment_fingerprint(segment))

    @staticmethod
    def _make_record() -> NailoongRecord:
        return NailoongRecord(
            id="abc",
            image_filename="abc.png",
            added_by="42",
            added_at="2026-06-03 02:31:00",
            original_name="测试奶龙",
        )


def _build_png_bytes(color: str) -> bytes:
    return _build_image_bytes(color, "PNG")


def _build_bmp_bytes(color: str) -> bytes:
    return _build_image_bytes(color, "BMP")


def _build_split_png_bytes(left_color: str, right_color: str) -> bytes:
    image = Image.new("RGB", (16, 16), color=left_color)
    right_pixel = Image.new("RGB", (1, 1), color=right_color).getpixel((0, 0))
    for x in range(8, 16):
        for y in range(16):
            image.putpixel((x, y), right_pixel)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _build_image_bytes(color: str, image_format: str) -> bytes:
    image = Image.new("RGB", (16, 16), color=color)
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()
