"""Bounded local MCP teaching server used by the E10 stdio adapter.

The module deliberately exposes two fictional, read-only tools plus one
resource and prompt. It has no filesystem or network input and accepts only
JSON-RPC lines.
"""

from __future__ import annotations

import json
import sys
from typing import Any

MCP_VERSION = "2025-11-25"
MAX_LINE_BYTES = 32_768


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _error(request_id, -32600, "Invalid JSON-RPC request")
    if request_id is None:
        return None
    if not isinstance(params, dict):
        params = {}
    if method == "initialize":
        if params.get("protocolVersion") != MCP_VERSION:
            return _error(request_id, -32001, f"VersionNotSupported: expected {MCP_VERSION}")
        return _result(
            request_id,
            {
                "protocolVersion": MCP_VERSION,
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "agent-trace-local-mcp", "version": "1.0.0"},
                "instructions": "Local fictional fixtures only; no writes and no network.",
            },
        )
    if method == "tools/list":
        return _result(
            request_id,
            {
                "tools": [
                    {
                        "name": "analytics.query_metric",
                        "description": "Read a deterministic fictional metric series.",
                        "inputSchema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["metric", "periods"],
                            "properties": {
                                "metric": {"enum": ["revenue", "tickets"]},
                                "periods": {
                                    "type": "array",
                                    "items": {"enum": ["2025-Q3", "2025-Q4"]},
                                    "minItems": 2,
                                    "maxItems": 2,
                                },
                            },
                        },
                    },
                    {
                        "name": "orders.get_status",
                        "description": "Read one bounded fictional order status.",
                        "inputSchema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["order_id"],
                            "properties": {
                                "order_id": {
                                    "type": "string",
                                    "pattern": "^ORD-[0-9]{4}$",
                                },
                            },
                        },
                    },
                ]
            },
        )
    if method == "tools/call":
        tool_name = params.get("name")
        expected_by_tool: dict[str, dict[str, Any]] = {
            "analytics.query_metric": {
                "metric": "tickets",
                "periods": ["2025-Q3", "2025-Q4"],
            },
            "orders.get_status": {"order_id": "ORD-1042"},
        }
        if not isinstance(tool_name, str):
            return _error(request_id, -32602, "A bounded read-only tool name is required")
        expected = expected_by_tool.get(tool_name)
        if expected is None or params.get("arguments") != expected:
            return _error(
                request_id, -32602, "Only bounded read-only fixture arguments are allowed"
            )
        response_text = {
            "analytics.query_metric": (
                "Validated request proposal for fictional ticket counts, 2025-Q3 to 2025-Q4."
            ),
            "orders.get_status": (
                "Validated request proposal for fictional order ORD-1042 status."
            ),
        }[tool_name]
        return _result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": response_text,
                    }
                ],
                "structuredContent": {"tool": tool_name, "arguments": expected},
                "isError": False,
            },
        )
    if method == "resources/list":
        return _result(
            request_id,
            {
                "resources": [
                    {
                        "uri": "scenario://incident-response/delegation-brief",
                        "name": "Incident delegation brief",
                        "description": "Fictional local scenario boundary.",
                        "mimeType": "application/json",
                    }
                ]
            },
        )
    if method == "resources/read":
        if params.get("uri") != "scenario://incident-response/delegation-brief":
            return _error(request_id, -32602, "Unknown local resource")
        return _result(
            request_id,
            {
                "contents": [
                    {
                        "uri": "scenario://incident-response/delegation-brief",
                        "mimeType": "application/json",
                        "text": json.dumps(
                            {
                                "classification": "public_fixture",
                                "question": "Did fictional ticket volume decline from Q3 to Q4?",
                                "allowed_capability": "analytics.query_metric",
                            },
                            separators=(",", ":"),
                        ),
                    }
                ]
            },
        )
    if method == "prompts/list":
        return _result(
            request_id,
            {
                "prompts": [
                    {
                        "name": "delegate-research",
                        "description": (
                            "Prepare a bounded evidence request for an isolated research agent."
                        ),
                        "arguments": [],
                    }
                ]
            },
        )
    if method == "prompts/get":
        if params.get("name") != "delegate-research":
            return _error(request_id, -32602, "Unknown local prompt")
        return _result(
            request_id,
            {
                "description": "Bounded fictional research delegation",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                "Return a cited artifact; do not expose memory, tools, "
                                "or hidden reasoning."
                            ),
                        },
                    }
                ],
            },
        )
    return _error(request_id, -32601, "Method not found")


def main() -> int:
    for line in sys.stdin.buffer:
        response: dict[str, Any] | None
        if len(line) > MAX_LINE_BYTES:
            response = _error(None, -32600, "Request exceeds the local size limit")
        else:
            try:
                value = json.loads(line)
                response = (
                    handle(value)
                    if isinstance(value, dict)
                    else _error(None, -32600, "Invalid request")
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                response = _error(None, -32700, "Parse error")
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
