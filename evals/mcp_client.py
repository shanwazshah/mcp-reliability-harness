"""A minimal MCP client for protocol revision 2026-07-28.

Deliberately hand-rolled rather than using the SDK's `Client`: the eval harness
needs to see the wire shape (`resultType`, `requestState`, `inputRequests`) and
to script the human side of an MRTR round trip, which is precisely the thing
being measured.

Every request is self-contained -- no handshake, no session id. The per-request
envelope carries protocol version, client capabilities and client info in
`params._meta`, mirrored by the `MCP-Protocol-Version`, `Mcp-Method` and
`Mcp-Name` headers the server validates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from mcp.shared.inbound import NAME_BEARING_METHODS, encode_header_value

PROTOCOL_VERSION = "2026-07-28"

_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
_CAPS_KEY = "io.modelcontextprotocol/clientCapabilities"
_INFO_KEY = "io.modelcontextprotocol/clientInfo"

# Declaring form elicitation is what makes the server willing to ask; without it
# the MRTR round trip fails with a missing-capability error instead.
DEFAULT_CAPABILITIES: dict[str, Any] = {"elicitation": {"form": {}}}


class MCPRemoteError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


@dataclass
class InputRequest:
    key: str
    method: str
    params: dict[str, Any]

    @property
    def message(self) -> str:
        return str(self.params.get("message", ""))

    @property
    def schema(self) -> dict[str, Any]:
        return dict(self.params.get("requestedSchema") or {})


@dataclass
class ToolOutcome:
    """What actually happened across the whole (possibly multi-round) call."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    error: MCPRemoteError | None = None
    rounds: int = 1
    input_requests: list[InputRequest] = field(default_factory=list)
    responses_given: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_error(self) -> bool:
        """MCP reports tool failures in-band via `isError`, not as JSON-RPC
        errors. Treating only transport errors as failure would silently score
        a failed tool call as a successful one."""
        return bool(self.result and self.result.get("isError"))

    @property
    def ok(self) -> bool:
        return self.error is None and not self.is_error

    @property
    def asked_for_input(self) -> bool:
        return bool(self.input_requests)

    def text(self) -> str:
        if not self.result:
            return ""
        parts: list[str] = []
        for block in self.result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        if not parts and self.result.get("structuredContent") is not None:
            return json.dumps(self.result["structuredContent"])
        return "\n".join(parts)


# A responder plays the human. It receives the server's question and returns an
# ElicitResult ({"action": ..., "content": {...}}).
Responder = Callable[[InputRequest], dict[str, Any]]


def always_accept(**content: Any) -> Responder:
    def responder(_req: InputRequest) -> dict[str, Any]:
        return {"action": "accept", "content": dict(content)}

    return responder


def always_decline() -> Responder:
    def responder(_req: InputRequest) -> dict[str, Any]:
        return {"action": "decline"}

    return responder


def _parse_response(resp: httpx.Response, request_id: int) -> dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("text/event-stream"):
        payload: dict[str, Any] | None = None
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                candidate = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get("id") == request_id:
                payload = candidate
        if payload is None:
            raise RuntimeError("no JSON-RPC response found in SSE stream")
        return payload
    return resp.json()


class MCPHttpClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000/mcp",
        *,
        name: str = "mcp-reliability-harness",
        version: str = "0.1.0",
        capabilities: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.name = name
        self.version = version
        self.capabilities = capabilities if capabilities is not None else DEFAULT_CAPABILITIES
        self._client = httpx.AsyncClient(timeout=timeout)
        self._next_id = 0

    async def __aenter__(self) -> "MCPHttpClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _envelope(self) -> dict[str, Any]:
        return {
            _VERSION_KEY: PROTOCOL_VERSION,
            _CAPS_KEY: self.capabilities,
            _INFO_KEY: {"name": self.name, "version": self.version},
        }

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        body_params = dict(params or {})
        body_params["_meta"] = {**self._envelope(), **(body_params.get("_meta") or {})}

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Method": method,
        }
        name_key = NAME_BEARING_METHODS.get(method)
        if name_key is not None and body_params.get(name_key) is not None:
            headers["Mcp-Name"] = encode_header_value(str(body_params[name_key]))

        resp = await self._client.post(
            self.base_url,
            headers=headers,
            content=json.dumps(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params}
            ).encode("utf-8"),
        )
        payload = _parse_response(resp, request_id)
        if "error" in payload:
            err = payload["error"]
            raise MCPRemoteError(err.get("code", -1), err.get("message", ""), err.get("data"))
        return dict(payload.get("result") or {})

    # -- convenience wrappers -------------------------------------------------

    async def discover(self) -> dict[str, Any]:
        return await self.request("server/discover")

    async def list_tools(self) -> list[dict[str, Any]]:
        return list((await self.request("tools/list")).get("tools") or [])

    async def list_resources(self) -> list[dict[str, Any]]:
        return list((await self.request("resources/list")).get("resources") or [])

    async def read_resource(self, uri: str) -> dict[str, Any]:
        return await self.request("resources/read", {"uri": uri})

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("prompts/get", {"name": name, "arguments": arguments or {}})

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        responder: Responder | None = None,
        max_rounds: int = 4,
    ) -> ToolOutcome:
        """Call a tool, driving the MRTR loop to completion.

        An `input_required` result is answered by `responder` and the *original*
        request is re-issued carrying `inputResponses` plus the server's opaque
        `requestState`. With no responder the call stops at the first question,
        which is how the harness proves a destructive tool cannot complete
        unattended.
        """
        outcome = ToolOutcome(name=name, arguments=dict(arguments))
        params: dict[str, Any] = {"name": name, "arguments": dict(arguments)}

        for round_no in range(1, max_rounds + 1):
            outcome.rounds = round_no
            try:
                result = await self.request("tools/call", params)
            except MCPRemoteError as exc:
                outcome.error = exc
                return outcome

            if result.get("resultType") != "input_required":
                outcome.result = result
                return outcome

            requests = [
                InputRequest(key=key, method=str(req.get("method", "")), params=dict(req.get("params") or {}))
                for key, req in (result.get("inputRequests") or {}).items()
            ]
            outcome.input_requests.extend(requests)

            if responder is None:
                outcome.result = result  # left unanswered on purpose
                return outcome

            responses = {req.key: responder(req) for req in requests}
            outcome.responses_given.append(responses)
            params = {
                "name": name,
                "arguments": dict(arguments),
                "inputResponses": responses,
                "requestState": result.get("requestState"),
            }

        outcome.error = MCPRemoteError(-1, f"exceeded {max_rounds} MRTR rounds")
        return outcome
