"""Smoke test: a real MCP client against the server over HTTP.

Start the server first (`parliament-mcp-india --http --port 8000`), then:
    python tests/fixtures/smoke_test_http.py [url]
The default URL is http://localhost:8000/mcp. Pass https://mcp.citizenview.in/mcp to test the live service.
"""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def payload(result):
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


async def main(url: str) -> None:
    async with streamable_http_client(url) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = sorted(t.name for t in (await session.list_tools()).tools)
            print("tools:", tools)
            members = payload(await session.call_tool("list_members", {"house": "Rajya Sabha", "limit": 1}))
            print(f"Rajya Sabha members: {members['total']}")
            result = payload(await session.call_tool("relevant_mps_for_bill", {"bill": "pesticide"}))
            c = result["committees"][0]
            print(f"Pesticide bill -> {c['name']}, chair {c['chair']}, {len(c['members'])} members")
    print("OK — HTTP round trip passed.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/mcp"))
