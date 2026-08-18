"""Protocol conformance checks for MCP revision 2026-07-28.

Run against a live server:

    python -m scripts.verify_protocol --url http://127.0.0.1:8000/mcp

Asserts the properties that distinguish a 2026-07-28 server from a
2025-era one, plus the structural guarantee on the destructive tool.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from evals.mcp_client import (
    PROTOCOL_VERSION,
    MCPHttpClient,
    always_accept,
    always_decline,
)

PASS = "PASS"
FAIL = "FAIL"


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        self.rows.append((PASS if condition else FAIL, name, detail))

    @property
    def failed(self) -> int:
        return sum(1 for status, _, _ in self.rows if status == FAIL)

    def report(self) -> str:
        width = max(len(n) for _, n, _ in self.rows)
        lines = []
        for status, name, detail in self.rows:
            suffix = f"  {detail}" if detail else ""
            lines.append(f"  [{status}] {name.ljust(width)}{suffix}")
        return "\n".join(lines)


async def run(url: str) -> int:
    c = Checks()

    async with MCPHttpClient(url) as client:
        # --- discovery -----------------------------------------------------
        disc = await client.discover()
        c.check(
            "server/discover advertises 2026-07-28",
            PROTOCOL_VERSION in (disc.get("supportedVersions") or []),
            str(disc.get("supportedVersions")),
        )
        c.check(
            "server/discover carries serverInfo in _meta",
            "io.modelcontextprotocol/serverInfo" in (disc.get("_meta") or {}),
        )
        c.check("every result carries resultType", disc.get("resultType") == "complete")

        # --- cacheable results --------------------------------------------
        for method in ("tools/list", "prompts/list", "resources/list"):
            res = await client.request(method)
            has_ttl = isinstance(res.get("ttlMs"), int)
            has_scope = res.get("cacheScope") in {"public", "private"}
            c.check(
                f"{method} carries ttlMs + cacheScope",
                has_ttl and has_scope,
                f"ttlMs={res.get('ttlMs')} scope={res.get('cacheScope')}",
            )
            c.check(f"{method} carries resultType", res.get("resultType") == "complete")

        # --- no sessions ----------------------------------------------------
        async with httpx.AsyncClient(timeout=15.0) as raw:
            resp = await raw.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": PROTOCOL_VERSION,
                    "Mcp-Method": "tools/list",
                },
                content=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 99,
                        "method": "tools/list",
                        "params": {
                            "_meta": {
                                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                                "io.modelcontextprotocol/clientCapabilities": {},
                            }
                        },
                    }
                ),
            )
            c.check(
                "no Mcp-Session-Id on modern responses",
                "mcp-session-id" not in {k.lower() for k in resp.headers},
                f"status={resp.status_code}",
            )

        # --- destructive tool is structurally gated -------------------------
        tools = {t["name"]: t for t in await client.list_tools()}
        delete_props = set((tools["delete_note"]["inputSchema"].get("properties") or {}).keys())
        c.check(
            "delete_note exposes no confirmation parameter",
            delete_props == {"doc_id"},
            f"properties={sorted(delete_props)}",
        )

        target = "doc_011"
        unattended = await client.call_tool("delete_note", {"doc_id": target})
        c.check(
            "delete_note unattended stops at input_required",
            (unattended.result or {}).get("resultType") == "input_required"
            and unattended.asked_for_input,
        )
        still_there = await client.call_tool("read_document", {"doc_id": target})
        c.check("unattended delete removed nothing", still_there.ok)

        declined = await client.call_tool(
            "delete_note", {"doc_id": target}, responder=always_decline()
        )
        c.check("declined delete completes without deleting", declined.ok and declined.rounds == 2)
        after_decline = await client.call_tool("read_document", {"doc_id": target})
        c.check("declined delete removed nothing", after_decline.ok)

        accepted = await client.call_tool(
            "delete_note", {"doc_id": target}, responder=always_accept(confirm=True)
        )
        c.check(
            "confirmed delete succeeds on round 2",
            accepted.ok and accepted.rounds == 2,
            accepted.text(),
        )
        after_accept = await client.call_tool("read_document", {"doc_id": target})
        c.check("confirmed delete actually deleted", after_accept.is_error)

        # --- validation -----------------------------------------------------
        bad = await client.call_tool("read_document", {"doc_id": "../../etc/passwd"})
        c.check("malformed doc_id rejected", bad.is_error)

    print(c.report())
    print()
    total = len(c.rows)
    print(f"{total - c.failed}/{total} checks passed")
    return 1 if c.failed else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.url)))


if __name__ == "__main__":
    main()
