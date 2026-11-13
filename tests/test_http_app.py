import asyncio
import json
import unittest

from parliament_mcp_india import http_app
from parliament_mcp_india.http_app import MAX_BODY_BYTES, RateLimiter

MCP_HEADERS = {"host": "localhost:8000", "content-type": "application/json",
               "accept": "application/json, text/event-stream"}


def call(method, path, body=b"", headers=None, client=("10.0.0.1", 5000), chunks=None):
    """Run one request through the ASGI app; return (status, headers, body)."""
    headers = dict(MCP_HEADERS if headers is None else headers)
    if chunks is None:
        chunks = [body]
        if body:
            headers.setdefault("content-length", str(len(body)))
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "scheme": "http", "http_version": "1.1",
             "server": ("localhost", 8000), "client": client,
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    pending = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]
    sent = []

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(http_app.app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body_out = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, body_out


def rpc(method, params=None, id_=1):
    return json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}).encode()


class HttpAppTests(unittest.TestCase):
    def setUp(self):
        http_app.limiter = RateLimiter(per_minute=6000, burst=1000)  # don't let tests trip the limit

    def test_info_page_and_security_headers(self):
        status, headers, body = call("GET", "/", headers={"host": "localhost:8000"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mcp_endpoint"], "https://mcp.citizenview.in/mcp")
        self.assertEqual(headers["x-content-type-options"], "nosniff")

    def test_unknown_path_and_wrong_methods(self):
        self.assertEqual(call("GET", "/admin", headers={"host": "localhost"})[0], 404)
        self.assertEqual(call("GET", "/mcp")[0], 405)
        self.assertEqual(call("DELETE", "/mcp")[0], 405)
        self.assertEqual(call("POST", "/")[0], 405)

    def test_tool_call_over_http(self):
        status, _, body = call("POST", "/mcp", rpc("tools/call", {"name": "list_members", "arguments": {"house": "Lok Sabha", "limit": 2}}))
        self.assertEqual(status, 200, body)
        text = json.loads(body)["result"]["content"][0]["text"]
        self.assertEqual(json.loads(text)["total"], 543)

    def test_tools_list_over_http(self):
        status, _, body = call("POST", "/mcp", rpc("tools/list"))
        self.assertEqual(status, 200, body)
        names = {t["name"] for t in json.loads(body)["result"]["tools"]}
        self.assertIn("relevant_mps_for_bill", names)

    def test_foreign_host_is_refused(self):
        status, _, _ = call("POST", "/mcp", rpc("tools/list"), headers={**MCP_HEADERS, "host": "evil.example"})
        self.assertGreaterEqual(status, 400)

    def test_foreign_origin_is_refused(self):
        status, _, _ = call("POST", "/mcp", rpc("tools/list"), headers={**MCP_HEADERS, "origin": "https://evil.example"})
        self.assertGreaterEqual(status, 400)

    def test_allowed_origin_passes(self):
        status, _, _ = call("POST", "/mcp", rpc("tools/list"), headers={**MCP_HEADERS, "origin": "https://app.citizenview.in"})
        self.assertEqual(status, 200)

    def test_oversized_body_refused_by_content_length(self):
        status, _, _ = call("POST", "/mcp", b"x" * (MAX_BODY_BYTES + 1))
        self.assertEqual(status, 413)

    def test_oversized_body_refused_without_content_length(self):
        chunk = b"x" * 40_000
        status, _, _ = call("POST", "/mcp", chunks=[chunk, chunk, chunk])
        self.assertEqual(status, 413)

    def test_oversized_tool_input_refused(self):
        status, _, body = call("POST", "/mcp", rpc("tools/call", {"name": "search_members", "arguments": {"query": "a" * 101}}))
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["result"]["isError"])

    def test_rate_limit_returns_429_with_retry_after(self):
        http_app.limiter = RateLimiter(per_minute=60, burst=2)
        codes = [call("POST", "/mcp", rpc("tools/list"), client=("10.9.9.9", 1))[0] for _ in range(3)]
        self.assertEqual(codes[:2], [200, 200])
        self.assertEqual(codes[2], 429)
        _, headers, _ = call("POST", "/mcp", rpc("tools/list"), client=("10.9.9.9", 1))
        self.assertIn("retry-after", headers)
        self.assertEqual(call("POST", "/mcp", rpc("tools/list"), client=("10.8.8.8", 1))[0], 200)


class RateLimiterTests(unittest.TestCase):
    def test_refills_over_time(self):
        limiter = RateLimiter(per_minute=60, burst=1)
        self.assertTrue(limiter.check("a", now=0)[0])
        self.assertFalse(limiter.check("a", now=0.5)[0])
        self.assertTrue(limiter.check("a", now=1.6)[0])

    def test_tracks_a_bounded_number_of_clients(self):
        limiter = RateLimiter(per_minute=60, burst=1, max_clients=3)
        for i in range(10):
            limiter.check(f"client-{i}", now=0)
        self.assertEqual(len(limiter._clients), 3)


if __name__ == "__main__":
    unittest.main()
