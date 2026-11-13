"""Export the MPs on each tracked bill's committee for the CitizenView website.

  PYTHONPATH=src python3 -m parliament_mcp_india.export_site \
      --out ../citizenview/src/data/bill-mps.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data import ParliamentDataset

KEEP = ("name", "house", "party", "state", "constituency", "profile_url", "is_chair")


def export(dataset: ParliamentDataset) -> dict:
    bills = {}
    for bill in dataset.bills:
        if not bill.get("citizenview_id"):
            continue
        result = dataset.relevant_mps_for_bill(bill["id"])
        committees = [{
            "name": c["name"],
            "type": c["type"],
            "source_url": c["source_url"],
            "last_mentioned": c["last_mentioned_by_committee"],
            "members": [{k: m[k] for k in KEEP} for m in c["members"]],
            "former_mps_listed": len(c["listed_but_no_longer_mps"]),
        } for c in result["committees"] if c["members"]]
        if committees:
            bills[bill["citizenview_id"]] = {"prs_url": bill["prs_url"], "committees": committees}
    return {"retrieved_on": dataset.retrieved_on, "attribution": dataset.attribution, "bills": bills}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    data = export(ParliamentDataset.load())
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(data['bills'])} bills to {args.out}")


if __name__ == "__main__":
    main()
