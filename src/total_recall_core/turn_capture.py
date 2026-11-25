"""Encode the tool activity in a redacted Hermes turn without reading its session DB."""
from __future__ import annotations

import base64
import bz2
import hashlib
import json
from typing import Any

RESULT_EXCERPT_BYTES = 96
ARGUMENT_SEARCH_BYTES = 256
_COMPRESSION_MIN_BYTES = 1024
ToolActivity = list[dict[str, Any]] | dict[str, str]


def turn_text(user_content: str, assistant_content: str) -> str:
    return f"User: {user_content}\nAssistant: {assistant_content}"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def capture_tool_activity(messages: list[dict[str, Any]]) -> ToolActivity:
    # Hermes can pass the conversation, not just its last turn. Steers and
    # in-turn redirects must not hide tool calls made before they arrived.
    start = 0
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") != "user" or message.get("display_kind") == "steer":
            continue
        if str(message.get("api_content") or "").startswith("[Context from the interrupted assistant response]"):
            continue
        start = index + 1
        break

    activity = []
    for index in range(start, len(messages)):
        message = messages[index]
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or ():
                function = call.get("function") or {}
                if function.get("name"):
                    activity.append({
                        "kind": "call", "name": function["name"], "call_id": call.get("id"),
                        "arguments": function.get("arguments"),
                    })
        elif message.get("role") == "tool":
            content = message.get("content")
            raw = content.encode("utf-8") if isinstance(content, str) else _json_bytes(content)
            result = {
                "kind": "result", "call_id": message.get("tool_call_id"),
                "excerpt": raw[:RESULT_EXCERPT_BYTES].decode("utf-8", errors="ignore"),
                "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            }
            if message.get("message_uid"):
                result["message_uid"] = message["message_uid"]
            elif message.get("_row_id") is not None:
                result["row_id"] = message["_row_id"]
            elif message.get("timestamp") is not None:
                result["timestamp"] = message["timestamp"]
            else:
                result["message_index"] = index
            activity.append(result)
    raw = _json_bytes(activity)
    if len(raw) >= _COMPRESSION_MIN_BYTES:
        packed = {"bz2_base64": base64.b64encode(bz2.compress(raw)).decode("ascii")}
        if len(_json_bytes(packed)) < len(raw):
            return packed
    return activity


def tool_search_terms(event: dict[str, Any]) -> str:
    activity = event.get("tool_activity") or []
    if isinstance(activity, dict):
        activity = json.loads(bz2.decompress(base64.b64decode(activity["bz2_base64"])))
    terms = []
    for item in activity:
        if item["kind"] == "call":
            terms.append(item["name"])
            prefix = _json_bytes(item.get("arguments"))[:ARGUMENT_SEARCH_BYTES]
            terms.append(prefix.decode("utf-8", errors="ignore"))
        elif item["kind"] == "result":
            terms.append(item["excerpt"])
    return "\n".join(terms)
