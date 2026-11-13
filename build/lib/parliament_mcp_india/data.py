"""Loads the parliamentary dataset and enforces the redistribution gate.

Every record carries `permissions.redistribution`. Only `"allowed"` passes;
`"denied"` and `"unknown"` are withheld, because this server's job is handing
records to third parties.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DATASET = Path(__file__).resolve().parent / "dataset" / "parliament.json"
HOUSES = ("Lok Sabha", "Rajya Sabha")


def _allowed(record: dict[str, Any]) -> bool:
    return record.get("permissions", {}).get("redistribution") == "allowed"


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", value.lower())).strip()


def _public(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in ("permissions", "prs_image_id")}


class ParliamentDataset:
    def __init__(self, raw: dict):
        self.retrieved_on = raw.get("retrieved_on")
        self.attribution = raw.get("attribution", "")
        self.members = [m for m in raw.get("members", []) if _allowed(m)]
        self.committees = [c for c in raw.get("committees", []) if _allowed(c)]
        self.bills = [b for b in raw.get("bills", []) if _allowed(b)]
        self._members = {m["id"]: m for m in self.members}
        self._committees = {c["id"]: c for c in self.committees}

    @classmethod
    def load(cls, path: Path = DATASET) -> "ParliamentDataset":
        if not path.exists():
            raise FileNotFoundError(f"Dataset not found at {path}. Run parliament_mcp_india.ingest first.")
        return cls(json.loads(path.read_text(encoding="utf-8")))

    # ---- members ----

    def list_members(self, house: str | None = None, state: str | None = None, party: str | None = None,
                     limit: int = 50, offset: int = 0) -> dict:
        if house is not None and house not in HOUSES:
            raise ValueError(f"house must be one of {HOUSES}, got {house!r}")
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit must be 1–200 and offset >= 0")
        rows = [m for m in self.members
                if (house is None or m["house"] == house)
                and (state is None or _norm(m.get("state") or "") == _norm(state))
                and (party is None or _norm(party) in _norm(m.get("party") or ""))]
        return {"total": len(rows), "offset": offset, "members": [_public(m) for m in rows[offset:offset + limit]]}

    def search_members(self, query: str) -> list[dict]:
        q = _norm(query)
        if len(q) < 2:
            raise ValueError("query must be at least 2 characters")
        return [_public(m) for m in self.members
                if q in _norm(m["name"]) or q in _norm(m.get("constituency") or "")][:50]

    def member_committees(self, member_id: str) -> list[dict]:
        out = []
        for c in self.committees:
            if not c.get("current", True):
                continue
            chair = (c.get("chair") or {}).get("member_id") == member_id
            if chair or any(p.get("member_id") == member_id for p in c["members"]):
                out.append({"committee_id": c["id"], "name": c["name"], "role": c["chair"]["role"] if chair else "Member"})
        return out

    def get_member(self, member_id: str) -> dict | None:
        m = self._members.get(member_id)
        return None if m is None else {**_public(m), "committees": self.member_committees(member_id)}

    # ---- committees ----

    def list_committees(self) -> list[dict]:
        return [{"id": c["id"], "name": c["name"], "type": c["type"], "current": c.get("current", True),
                 "chair": (c.get("chair") or {}).get("name"), "member_count": len(c["members"])}
                for c in self.committees]

    def get_committee(self, committee_id: str) -> dict | None:
        c = self._committees.get(committee_id)
        return None if c is None else _public({**c, "chair": _public(c["chair"]) if c.get("chair") else None,
                                               "members": [_public(p) for p in c["members"]]})

    # ---- bills ----

    def list_bills(self) -> list[dict]:
        return [_public(b) for b in self.bills]

    def find_bill(self, bill: str) -> dict | None:
        q = _norm(bill)
        for b in self.bills:
            if bill in (b["id"], b.get("citizenview_id")) or q == _norm(b["title"]):
                return b
        hits = [b for b in self.bills if q and q in _norm(b["title"])]
        return hits[0] if len(hits) == 1 else None

    def relevant_mps_for_bill(self, bill: str) -> dict | None:
        """MPs on the committee(s) examining a bill. Relevance means a documented role, not support."""
        b = self.find_bill(bill)
        if b is None:
            return None
        committees = []
        for link in b["committees"]:
            c = self._committees.get(link["committee_id"])
            if c is None:
                continue
            chair = c.get("chair")
            members, former = [], []
            for p in c["members"]:
                roster = self._members.get(p.get("member_id") or "")
                if roster is None:
                    # Listed on the committee page but not on the current MP roster (term ended, PRS page lagging).
                    former.append(p["name"])
                    continue
                members.append({
                    "name": roster["name"], "house": roster["house"], "member_id": roster["id"],
                    "party": roster.get("party"), "state": roster.get("state"),
                    "constituency": roster.get("constituency"), "profile_url": roster.get("profile_url"),
                    "is_chair": bool(chair) and chair.get("member_id") == roster["id"],
                })
            members.sort(key=lambda m: (not m["is_chair"], m["house"], m["name"]))
            committees.append({
                "committee_id": c["id"], "name": c["name"], "type": c["type"],
                "chair": chair["name"] if chair else None,
                "last_mentioned_by_committee": link.get("last_mentioned"),
                "current": c.get("current", True),
                "source_url": c["source_url"], "members": members,
                "listed_but_no_longer_mps": former,
            })
        return {
            "bill": {"id": b["id"], "title": b["title"], "prs_url": b["prs_url"]},
            "reason": "These MPs sit on the parliamentary committee examining this bill. "
                      "A committee role shows they work on the bill, not whether they support it.",
            "committees": committees,
            "retrieved_on": self.retrieved_on,
            "attribution": self.attribution,
        }
