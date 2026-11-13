"""Vercel entrypoint: Vercel serves the top-level `app` from this file."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from parliament_mcp_india.http_app import app  # noqa: E402,F401
