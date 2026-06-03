import tempfile
import unittest
from pathlib import Path

from src.plugins.xilian_style.client import build_api_config
from src.plugins.xilian_style.store import (
    MUSICAL_NOTE,
    RollingWindowLimiter,
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
        self.assertFalse(
            is_valid_quoted_text(
                "这是一段为了测试昔涟改写长度限制而专门准备的超长文本需要明显超过四十个字符才可以正确触发校验"
            )
        )

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
        result = sanitize_xilian_output("a" * 120)
        self.assertLessEqual(len(result), 80)
        self.assertTrue(result.endswith(MUSICAL_NOTE))

    def test_rolling_window_limiter(self) -> None:
        limiter = RollingWindowLimiter(limit=3, window_seconds=60.0)
        self.assertTrue(limiter.allow_sync(now=0.0))
        self.assertTrue(limiter.allow_sync(now=1.0))
        self.assertTrue(limiter.allow_sync(now=2.0))
        self.assertFalse(limiter.allow_sync(now=3.0))
        self.assertTrue(limiter.allow_sync(now=61.0))

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
