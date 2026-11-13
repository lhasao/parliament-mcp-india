"""MCP server exposing Indian parliamentary members, committees and bill-committee links.

Run locally (stdio):    parliament-mcp-india
Run as a web service:   parliament-mcp-india --http --port 8000   (see http_app.py)
"""

from __future__ import annotations

import argparse

from mcp.server.mcpserver import MCPServer

from .data import ParliamentDataset

server = MCPServer(
    name="parliament-mcp-india",
    title="Parliament MCP (India)",
    instructions=(
        "Read-only data on India's current MPs (18th Lok Sabha and Rajya Sabha), "
        "parliamentary committees, and which committee is examining a bill. "
        "Data comes from PRS Legislative Research (CC BY 4.0) and must be credited as such. "
        "A committee role shows an MP works on a bill, not whether they support it. "
        "Each response states the date the data was retrieved."
    ),
)

_dataset: ParliamentDataset | None = None


def _data() -> ParliamentDataset:
    global _dataset
    if _dataset is None:
        _dataset = ParliamentDataset.load()
    return _dataset


def _text(value: str | None, field: str, max_len: int) -> str | None:
    """Refuse oversized inputs before they reach the dataset."""
    if value is not None and len(value) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters")
    return value


def _not_found(kind: str, key: str) -> ValueError:
    return ValueError(f"No {kind} found for {key[:80]!r}")


@server.tool()
def list_members(house: str | None = None, state: str | None = None, party: str | None = None,
                 limit: int = 50, offset: int = 0) -> dict:
    """List current MPs. Filter by house ("Lok Sabha" or "Rajya Sabha"), state, or party. Paginated (limit 1–200)."""
    return _data().list_members(_text(house, "house", 20), _text(state, "state", 60),
                                _text(party, "party", 80), limit, offset)


@server.tool()
def search_members(query: str) -> list[dict]:
    """Find MPs by name or constituency (partial match, max 50 results)."""
    return _data().search_members(_text(query, "query", 100))


@server.tool()
def get_member(member_id: str) -> dict:
    """One MP's details, including the committees they sit on."""
    member = _data().get_member(_text(member_id, "member_id", 120))
    if member is None:
        raise _not_found("member", member_id)
    return member


@server.tool()
def list_committees() -> list[dict]:
    """All parliamentary committees with their chair and size."""
    return _data().list_committees()


@server.tool()
def get_committee(committee_id: str) -> dict:
    """A committee's chair and full membership."""
    committee = _data().get_committee(_text(committee_id, "committee_id", 200))
    if committee is None:
        raise _not_found("committee", committee_id)
    return committee


@server.tool()
def list_bills() -> list[dict]:
    """Bills currently tracked, with the committees examining them."""
    return _data().list_bills()


@server.tool()
def relevant_mps_for_bill(bill: str) -> dict:
    """The MPs on the committee examining a bill. Accepts a PRS bill slug or part of the bill's title."""
    result = _data().relevant_mps_for_bill(_text(bill, "bill", 200))
    if result is None:
        raise _not_found("tracked bill (or the title matched more than one)", bill)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Parliament MCP (India)")
    parser.add_argument("--http", action="store_true", help="Serve over HTTP, with the same protections as the hosted version")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.http:
        import uvicorn

        from .http_app import app

        uvicorn.run(app, host=args.host, port=args.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
