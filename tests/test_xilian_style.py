import tempfile
import unittest
from pathlib import Path

import httpx

from src.plugins.xilian_style.client import (
    XiLianApiConfig,
    XiLianApiError,
    _extract_response_content,
    build_api_config,
    build_request_payload,
)
from src.plugins.xilian_style.store import (
    MUSICAL_NOTE,
    XiLianModeStore,
    build_system_prompt,
    build_user_prompt,
    extract_command,
    is_valid_quoted_text,
    sanitize_xilian_output,
)


class XiLianModeStoreTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "mode_state.json"
        self.store = XiLianModeStore(state_path=self.state_path)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_mode_state_defaults_to_disabled(self) -> None:
        self.assertFalse(await self.store.is_enabled())

    async def test_mode_state_persists(self) -> None:
        await self.store.set_enabled(True)
        self.assertTrue(await self.store.is_enabled())


class XiLianHelperTestCase(unittest.TestCase):
    def test_extract_command(self) -> None:
        self.assertEqual(extract_command("/昔涟改写"), ("/昔涟改写", None))
        self.assertEqual(extract_command("/昔涟回复 test"), ("/昔涟回复", "test"))

    def test_is_valid_quoted_text(self) -> None:
        self.assertTrue(is_valid_quoted_text("我喜欢你"))
        self.assertTrue(is_valid_quoted_text("hello world"))
        self.assertFalse(is_valid_quoted_text(""))
        self.assertFalse(is_valid_quoted_text("a" * 201))

    def test_system_prompt_mentions_u266a_rules(self) -> None:
        prompt = build_system_prompt("rewrite")
        self.assertIn("U+266A", prompt)
        self.assertIn(MUSICAL_NOTE, prompt)
        self.assertIn("不能使用🎵", prompt)

    def test_build_user_prompt(self) -> None:
        self.assertIn("改写", build_user_prompt("rewrite", "我喜欢你"))
        self.assertIn("回复", build_user_prompt("reply", "我喜欢你"))

    def test_sanitize_xilian_output_enforces_single_final_note(self) -> None:
        result = sanitize_xilian_output("人家觉得这样也很温柔呢🎵。")
        self.assertTrue(result.endswith(MUSICAL_NOTE))
        self.assertNotIn("🎵", result)
        self.assertFalse(result.endswith(f"{MUSICAL_NOTE}。"))

    def test_sanitize_xilian_output_truncates_to_max_chars(self) -> None:
        result = sanitize_xilian_output("a" * 260)
        self.assertLessEqual(len(result), 200)
        self.assertTrue(result.endswith(MUSICAL_NOTE))

    def test_build_api_config(self) -> None:
        config = build_api_config(
            xilian_api_url="https://example.com/v1/chat/completions",
            xilian_api_key="sk-test",
            xilian_api_model="test-model",
            timeout_seconds="15",
        )
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.model, "test-model")
        self.assertEqual(config.timeout_seconds, 15.0)

    def test_build_api_config_falls_back_to_default_model(self) -> None:
        config = build_api_config(
            xilian_api_url="https://example.com/v1/chat/completions",
            xilian_api_key="sk-test",
            xilian_api_model="",
        )
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.model, "gpt-5-mini")

    def test_build_request_payload_uses_responses_shape(self) -> None:
        payload = build_request_payload(
            XiLianApiConfig(
                url="https://example.com/v1/responses",
                api_key="sk-test",
                model="gpt-5.4",
            ),
            task="rewrite",
            quoted_text="我喜欢你",
        )
        self.assertEqual(payload["model"], "gpt-5.4")
        self.assertIn("instructions", payload)
        self.assertEqual(payload["input"], "请把这句话改写成昔涟口吻：我喜欢你")
        self.assertEqual(payload["text"]["format"]["type"], "text")
        self.assertEqual(payload["store"], False)
        self.assertEqual(payload["stream"], False)

    def test_extract_content_supports_output_text(self) -> None:
        response = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"output_text": "人家把晚风装进心事里啦♪"},
        )
        self.assertEqual(_extract_response_content(response), "人家把晚风装进心事里啦♪")

    def test_extract_content_supports_response_field(self) -> None:
        response = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"response": "人家把心事轻轻放进晚风里呢♪"},
        )
        self.assertEqual(_extract_response_content(response), "人家把心事轻轻放进晚风里呢♪")

    def test_extract_content_supports_output_item_text(self) -> None:
        response = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"output": [{"text": "人家也会把思念说给星星听呀♪"}]},
        )
        self.assertEqual(_extract_response_content(response), "人家也会把思念说给星星听呀♪")

    def test_extract_response_content_raises_for_empty_json_content(self) -> None:
        response = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"choices": [{"message": {"role": "assistant"}}]},
        )
        with self.assertRaises(XiLianApiError) as ctx:
            _extract_response_content(response)
        self.assertEqual(ctx.exception.code, "empty_json_content")

    def test_extract_response_content_supports_plain_text(self) -> None:
        response = httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8"},
            text="人家把晚风装进心事里啦♪",
        )
        self.assertEqual(_extract_response_content(response), "人家把晚风装进心事里啦♪")

    def test_extract_response_content_supports_sse(self) -> None:
        response = httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='data: {"choices":[{"delta":{"content":"人家"}}]}\n'
            'data: {"choices":[{"delta":{"content":"喜欢你♪"}}]}\n'
            "data: [DONE]\n",
        )
        self.assertEqual(_extract_response_content(response), "人家喜欢你♪")
