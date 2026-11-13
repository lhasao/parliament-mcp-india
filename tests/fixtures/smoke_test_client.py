"""Smoke test: a real MCP client talking to the real server over stdio, using the real dataset.

Not part of the unittest suite (spawns a subprocess). Run directly:
    python tests/fixtures/smoke_test_client.py
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parents[2]


def payload(result):
    """Tools returning lists come back structured; plain dicts come back as JSON text. Accept both."""
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


EXPECTED_TOOLS = ["get_committee", "get_member", "list_bills", "list_committees",
                  "list_members", "relevant_mps_for_bill", "search_members"]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "parliament_mcp_india"],
        env={"PYTHONPATH": str(REPO_ROOT / "src")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            names = sorted(t.name for t in (await session.list_tools()).tools)
            assert names == EXPECTED_TOOLS, names

            ls = payload(await session.call_tool("list_members", {"house": "Lok Sabha", "limit": 3}))
            print(f"Lok Sabha members: {ls['total']} (first: {ls['members'][0]['name']})")

            bills = payload(await session.call_tool("list_bills", {}))
            bill = next(b for b in bills if b["committees"])
            result = payload(await session.call_tool("relevant_mps_for_bill", {"bill": bill["id"]}))
            committee = result["committees"][0]
            print(f"{bill['title']} -> {committee['name']}, chair {committee['chair']}, {len(committee['members'])} members")

            bad = await session.call_tool("list_members", {"house": "nowhere"})
            assert bad.is_error, "invalid house should be refused"

    print("OK — real MCP client/server round trip passed.")


if __name__ == "__main__":
    asyncio.run(main())
