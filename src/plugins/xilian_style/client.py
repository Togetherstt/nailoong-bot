from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .store import build_system_prompt, build_user_prompt, sanitize_xilian_output


class XiLianApiError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class XiLianApiConfig:
    url: str
    api_key: str
    model: str
    timeout_seconds: float = 20.0


async def generate_xilian_text(
    config: XiLianApiConfig,
    task: str,
    quoted_text: str,
) -> str:
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": build_system_prompt(task)},
            {"role": "user", "content": build_user_prompt(task, quoted_text)},
        ],
        "temperature": 0.9,
        "max_tokens": 160,
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(config.timeout_seconds, connect=min(config.timeout_seconds, 10.0))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(config.url, json=payload, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise XiLianApiError("request_failed") from exc

    content = _extract_content(response.json())
    if not content:
        raise XiLianApiError("empty_response")
    return sanitize_xilian_output(content)


def _extract_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts).strip()
    return ""


def build_api_config(
    xilian_api_url: object,
    xilian_api_key: object,
    xilian_api_model: object,
    fallback_url: object = "",
    fallback_key: object = "",
    timeout_seconds: object = 20.0,
) -> Optional[XiLianApiConfig]:
    url = str(xilian_api_url or fallback_url or "").strip()
    api_key = str(xilian_api_key or fallback_key or "").strip()
    model = str(xilian_api_model or "").strip()
    if not url or not api_key or not model:
        return None
    try:
        timeout_value = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout_value = 20.0
    return XiLianApiConfig(
        url=url,
        api_key=api_key,
        model=model,
        timeout_seconds=max(timeout_value, 1.0),
    )
