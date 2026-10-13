import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from agent_trace_api.config import RuntimePolicy

AsgiMessage = dict[str, Any]
Receive = Callable[[], Awaitable[AsgiMessage]]
Send = Callable[[AsgiMessage], Awaitable[None]]


async def bounded_public_stream(
    stream: AsyncIterator[str], *, max_lifetime_seconds: float
) -> AsyncIterator[str]:
    """Close a public follow stream at a hard monotonic deadline."""

    iterator = stream.__aiter__()
    deadline = asyncio.get_running_loop().time() + max_lifetime_seconds
    try:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            try:
                item = await asyncio.wait_for(anext(iterator), timeout=remaining)
            except StopAsyncIteration:
                return
            except TimeoutError:
                return
            yield item
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


class PublicPreviewPolicyMiddleware:
    """Enforce the public-preview deny policy before route handlers parse input."""

    def __init__(self, app: Callable[..., Awaitable[None]], policy: RuntimePolicy) -> None:
        self.app = app
        self.policy = policy
        self._requests: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    @staticmethod
    def _blocked(path: str, method: str) -> str | None:
        if path.startswith("/api/security/codex/"):
            return "Codex CLI is disabled in public preview."
        if path == "/api/protocols/mcp/runs":
            return "Process-backed MCP execution is disabled in public preview."
        if path in {"/api/security/imports", "/api/scenario-builder/imports"}:
            return "Archive import is disabled in public preview."
        if path.startswith("/api/scenario-builder/drafts"):
            return "Persistent scenario authoring and export are disabled in public preview."
        if method == "POST" and path.startswith("/api/retrieval/memory/preferences"):
            return "Persistent preference writes are disabled in public preview."
        if method == "POST" and path.startswith("/api/learning/modules/"):
            return "Anonymous learning-progress writes are disabled in public preview."
        return None

    @staticmethod
    async def _json_response(
        scope: dict[str, Any], receive: Receive, send: Send, status: int, detail: str
    ) -> None:
        body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _bounded_body(
        self, scope: dict[str, Any], receive: Receive, send: Send
    ) -> tuple[Receive | None, bool]:
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None, False
            body.extend(message.get("body", b""))
            if len(body) > self.policy.max_request_body_bytes:
                await self._json_response(
                    scope,
                    receive,
                    send,
                    413,
                    "Request body exceeds the public-preview limit.",
                )
                return None, False
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> AsgiMessage:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        return replay, True

    def _rate_limited(self, scope: dict[str, Any], path: str) -> bool:
        expensive = path in {"/api/evals/batches", "/api/security/suite"}
        bucket = "expensive" if expensive else "write"
        limit = (
            self.policy.expensive_write_limit_per_minute
            if expensive
            else self.policy.general_write_limit_per_minute
        )
        client = scope.get("client")
        client_host = str(client[0]) if client else "unknown"
        now = time.monotonic()
        requests = self._requests[(client_host, bucket)]
        while requests and requests[0] <= now - 60:
            requests.popleft()
        if len(requests) >= limit:
            return True
        requests.append(now)
        return False

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.policy.public_preview:
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        blocked = self._blocked(path, method)
        if blocked is not None:
            await self._json_response(scope, receive, send, 403, blocked)
            return
        if method in {"POST", "PUT", "PATCH"}:
            if self._rate_limited(scope, path):
                await self._json_response(
                    scope, receive, send, 429, "Public-preview request rate exceeded."
                )
                return
            bounded_receive, accepted = await self._bounded_body(scope, receive, send)
            if not accepted or bounded_receive is None:
                return
            receive = bounded_receive
        await self.app(scope, receive, send)
