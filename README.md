# Parliament MCP (India)

<!-- mcp-name: in.citizenview/parliament-mcp-india -->

Lets AI assistants (Claude, ChatGPT and other tools that support MCP) look up accurate,
current facts about India's Parliament instead of guessing from memory: who the MPs are,
which committees they sit on, and which committee is examining a bill.

Built by [CitizenView](https://app.citizenview.in). Data from
[PRS Legislative Research](https://prsindia.org), CC BY 4.0 — credit PRS when you use it.

## Connect

Run it with [uv](https://docs.astral.sh/uv/), one command, no cloning:

```sh
uvx --from git+https://github.com/lhasao/parliament-mcp-india parliament-mcp-india
```

Claude Desktop (`claude_desktop_config.json`):

```json
{ "mcpServers": { "parliament-india": { "command": "uvx", "args": ["--from", "git+https://github.com/lhasao/parliament-mcp-india", "parliament-mcp-india"] } } }
```

Claude Code: `claude mcp add parliament-india -- uvx --from git+https://github.com/lhasao/parliament-mcp-india parliament-mcp-india`

## What it answers

| Tool | What it does |
| --- | --- |
| `list_members` | Current MPs, filtered by house, state or party (paginated) |
| `search_members` | Find MPs by name or constituency |
| `get_member` | One MP, with the committees they sit on |
| `list_committees` / `get_committee` | Committees, chairs and full membership |
| `list_bills` | Tracked bills and the committees examining them |
| `relevant_mps_for_bill` | The MPs on the committee examining a bill |

Covers the 18th Lok Sabha, current Rajya Sabha members and all parliamentary committees. A bill is
linked to a committee only when that committee's own PRS page names it. "Relevant" means the MP
sits on the committee examining the bill — not that they support it. No contact details are included.

## Running it as a web service

`parliament-mcp-india --http --port 8000` serves MCP at `/mcp` with these protections:


- Only `POST /mcp` serves MCP; `GET /` and `/health` return a short info page; everything else is refused.
- Per-client rate limit: 60 requests a minute with bursts of 20 (`RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_BURST`).
  It's held in memory, per running instance. Behind a trusted reverse proxy, set `TRUST_PROXY_HEADERS=1` so it keys on the real client IP.
- Request bodies capped at 64 KB; tool inputs length-limited.
- The MCP SDK's host and origin checks (DNS-rebinding protection). Only localhost is allowed by default;
  add your domain with `MCP_ALLOWED_HOSTS` (and browser origins with `MCP_ALLOWED_ORIGINS`).
- Security headers on every response. The service is read-only and holds no secrets.

## Develop

```sh
uv venv --python 3.12 && source .venv/bin/activate && uv pip install -e .
python -m unittest discover -s tests          # unit and HTTP-protection tests
python tests/fixtures/smoke_test_client.py    # real client over stdio
parliament-mcp-india --http --port 8000 &     # local web server with its protections
python tests/fixtures/smoke_test_http.py      # real client over HTTP
```

## Refresh the data

Run around parliamentary sessions. It waits 10 seconds between requests, as PRS's robots.txt asks,
so a full run takes about 25 minutes.

```sh
python -m parliament_mcp_india.ingest --bills ../citizenview/src/data/bill-timelines.json \
    --out src/parliament_mcp_india/dataset/parliament.json --cache /tmp/prs-cache
python -m parliament_mcp_india.export_site --out ../citizenview/src/data/bill-mps.json   # MPs on CitizenView bill pages
```

## License

Code: MIT (see `LICENSE`). Data: PRS Legislative Research, CC BY 4.0.
