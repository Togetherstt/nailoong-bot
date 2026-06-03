from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Optional

import httpx
from nonebot import logger

from .store import build_system_prompt, build_user_prompt, sanitize_xilian_output


class XiLianApiError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


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
    payload = build_request_payload(config, task=task, quoted_text=quoted_text)
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
        raise XiLianApiError("request_failed", str(exc)) from exc

    content = _extract_response_content(response)
    if not content:
        raise XiLianApiError("empty_response")
    return sanitize_xilian_output(content)


def build_request_payload(
    config: XiLianApiConfig,
    task: str,
    quoted_text: str,
) -> dict[str, Any]:
    return {
        "model": config.model,
        "instructions": build_system_prompt(task),
        "input": build_user_prompt(task, quoted_text),
        "max_output_tokens": 220,
        "temperature": 0.9,
        "stream": False,
        "store": False,
        "text": {
            "format": {
                "type": "text",
            }
        },
    }


def _extract_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            logger.warning(f"XiLian API returned error payload: {message.strip()}")
        return ""
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    response_text = payload.get("response")
    if isinstance(response_text, str) and response_text.strip():
        return response_text.strip()
    text_field = payload.get("text")
    if isinstance(text_field, str) and text_field.strip():
        return text_field.strip()
    output = payload.get("output")
    if isinstance(output, list):
        output_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_text = item.get("text")
            if isinstance(item_text, str):
                output_parts.append(item_text)
            content_items = item.get("content")
            if not isinstance(content_items, list):
                continue
            for content_item in content_items:
                if not isinstance(content_item, dict):
                    continue
                text = content_item.get("text")
                if isinstance(text, str):
                    output_parts.append(text)
        if output_parts:
            return "".join(output_parts).strip()
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    direct_text = first.get("text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()
    delta = first.get("delta")
    if isinstance(delta, dict):
        delta_content = delta.get("content")
        if isinstance(delta_content, str) and delta_content.strip():
            return delta_content.strip()
        if isinstance(delta_content, list):
            parts: list[str] = []
            for item in delta_content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            if parts:
                return "".join(parts).strip()
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


def _extract_response_content(response: httpx.Response) -> str:
    body = response.text.strip()
    if not body:
        raise XiLianApiError("empty_http_body")

    content_type = response.headers.get("content-type", "").lower()

    if "application/json" in content_type or body.startswith(("{", "[")):
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            preview = body[:300]
            logger.warning(
                f"XiLian API returned invalid JSON. status={response.status_code} "
                f"content_type={content_type} body_preview={preview!r}"
            )
            raise XiLianApiError("invalid_json_response", preview) from exc
        content = _extract_content(payload)
        if content:
            return content
        try:
            payload_preview = json.dumps(payload, ensure_ascii=False)[:500]
        except Exception:
            payload_preview = str(payload)[:500]
        logger.warning(
            f"XiLian API returned JSON without usable content. "
            f"status={response.status_code} body_preview={payload_preview!r}"
        )
        raise XiLianApiError("empty_json_content", payload_preview)

    if "text/event-stream" in content_type or body.startswith("data:"):
        content = _extract_sse_content(body)
        if content:
            return content
        preview = body[:500]
        logger.warning(
            f"XiLian API returned SSE without usable content. "
            f"status={response.status_code} body_preview={preview!r}"
        )
        raise XiLianApiError("empty_sse_content", preview)

    if content_type.startswith("text/plain"):
        return body

    preview = body[:300]
    logger.warning(
        f"XiLian API returned unsupported content type. status={response.status_code} "
        f"content_type={content_type} body_preview={preview!r}"
    )
    raise XiLianApiError("unsupported_response_type", preview)


def _extract_sse_content(body: str) -> str:
    parts: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload_text = line[5:].strip()
        if not payload_text or payload_text == "[DONE]":
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue

        text = _extract_content(payload)
        if text:
            parts.append(text)
            continue

        choices = payload.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str):
                parts.append(content)
    return "".join(parts).strip()


def build_api_config(
    xilian_api_url: object,
    xilian_api_key: object,
    xilian_api_model: object,
    fallback_url: object = "",
    fallback_key: object = "",
    fallback_model: object = "gpt-5-mini",
    timeout_seconds: object = 20.0,
) -> Optional[XiLianApiConfig]:
    url = str(xilian_api_url or fallback_url or "").strip()
    api_key = str(xilian_api_key or fallback_key or "").strip()
    model = str(xilian_api_model or fallback_model or "").strip()
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
