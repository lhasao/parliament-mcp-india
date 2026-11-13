"""Scrape the current MP roster and committee memberships from PRS Legislative Research.

Sources (both cleared for reuse; PRS content is CC BY 4.0, credit PRS):
  - Members:    https://prsindia.org/mptrack  (18th Lok Sabha, current Rajya Sabha)
  - Committees: https://prsindia.org/parliamentary-committees/<slug>

A bill is linked to a committee only when that committee's own PRS page names
the bill, so every link can be checked by opening the source page.

Usage:
  PYTHONPATH=src python3 -m parliament_mcp_india.ingest \
      --bills ../citizenview/src/data/bill-timelines.json \
      --out src/parliament_mcp_india/dataset/parliament.json \
      --cache /tmp/prs-cache
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

BASE = "https://prsindia.org"
USER_AGENT = "CitizenView/1.0 (+https://app.citizenview.in; parliamentary data for citizens)"
CRAWL_DELAY_S = 10  # prsindia.org robots.txt
ATTRIBUTION = "Source: PRS Legislative Research (prsindia.org), CC BY 4.0."
PERMISSIONS = {"retrieval": "allowed", "redistribution": "allowed"}

_last_fetch = 0.0


def fetch(url: str, cache: Path | None) -> str:
    global _last_fetch
    if cache:
        path = cache / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".html")
        if path.exists():
            return path.read_text(encoding="utf-8")
    for attempt in range(3):
        wait = CRAWL_DELAY_S - (time.time() - _last_fetch)
        if wait > 0:
            time.sleep(wait)
        _last_fetch = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as res:
                body = res.read().decode("utf-8", errors="replace")
            break
        except Exception as error:  # network hiccups: retry, then fail loudly
            if attempt == 2:
                raise RuntimeError(f"Failed to fetch {url}: {error}") from error
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return body


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def image_id(src: str | None) -> str | None:
    m = re.search(r"/(\d+)\.(?:jpg|jpeg|png)", src or "", re.I)
    return m.group(1) if m else None


# ---------- members ----------

def parse_member_cards(page: str, house: str, path_prefix: str) -> list[dict]:
    members = []
    for block in page.split('class="views-row')[1:]:
        link = re.search(r'<h3><a href="(/mptrack/[^"]+)">(.*?)</a></h3>', block, re.S)
        if not link:
            continue
        def field(cls: str) -> str:
            m = re.search(rf'views-field-{cls}[^"]*">\s*<(?:div|span) class="field-content[^"]*">(.*?)</(?:div|span)>', block, re.S)
            return text(m.group(1)) if m else ""
        slug = link.group(1).rstrip("/").split("/")[-1]
        constituency = field("php")
        members.append({
            "id": f"{'ls' if house == 'Lok Sabha' else 'rs'}-{slug}",
            "name": text(link.group(2)),
            "house": house,
            "party": field("field-political-party"),
            "state": field("field-net-revenue-railway"),
            "constituency": None if constituency in ("", "NA") else constituency,
            "profile_url": BASE + link.group(1),
            "prs_image_id": image_id((re.search(r'<img[^>]+src="([^"]+)"', block) or [None, None])[1]),
        })
    return members


def end_of_term(profile_url: str, cache: Path | None) -> str:
    m = re.search(r"End of Term\s*:\s*([^:]+?)\s*No\. of Term", text(fetch(profile_url, cache)))
    return m.group(1).strip() if m else ""


def drop_former_lok_sabha_members(members: list[dict], cache: Path | None) -> list[dict]:
    """PRS keeps members who died or resigned; where a seat is listed twice, keep whoever is in office."""
    seats: dict[tuple, list[dict]] = {}
    for m in members:
        seats.setdefault((m["state"], m["constituency"]), []).append(m)
    keep = []
    for group in seats.values():
        if len(group) == 1:
            keep += group
            continue
        current = [m for m in group if end_of_term(m["profile_url"], cache).lower() == "in office"]
        for m in group:
            if m not in current:
                print(f"  dropped former member: {m['name']} ({m['constituency']})", file=sys.stderr)
        keep += current or group
    return keep


def scrape_house(slug: str, house: str, extra: dict, cache: Path | None) -> list[dict]:
    seen: dict[str, dict] = {}
    page = 1
    while True:
        query = {"slug1": slug, **extra, "page": page, "per-page": 9}
        cards = parse_member_cards(fetch(f"{BASE}/mptrack?{urllib.parse.urlencode(query)}", cache), house, slug)
        new = [c for c in cards if c["id"] not in seen]
        if not new:
            break
        for c in new:
            seen[c["id"]] = c
        print(f"  {house}: page {page}, {len(seen)} members", file=sys.stderr)
        page += 1
    return list(seen.values())


# ---------- committees ----------

def committee_slugs(cache: Path | None) -> list[str]:
    page = fetch(f"{BASE}/parliamentary-committees/agriculture", cache)
    return sorted(set(re.findall(r'href="/parliamentary-committees/([^"#?/]+)"', page)))


def parse_committee(slug: str, page: str) -> dict:
    name = text(re.search(r"<title>(.*?)</title>", page, re.S).group(1)).split("|")[0].strip()
    chair = None
    block = re.search(r'committee-chairperson(.*?)</section>', page, re.S)
    if block:
        b = block.group(1)
        def chair_field(label: str) -> str:
            m = re.search(rf'field-label">\s*{label}\s*:\s*</div>(.*?)</div>', b, re.S)
            return text(m.group(1)) if m else ""
        role = (re.search(r'field-label">\s*(Chair\w*)\s*:', b) or [None, "Chairperson"])[1]
        chair = {
            "name": chair_field(role),
            "role": role,
            "party": chair_field("Political Party"),
            "house": chair_field("Member From"),
            "prs_image_id": image_id((re.search(r'<img[^>]+src="([^"]+)"', b) or [None, None])[1]),
        }
    members = []
    table = re.search(r'id="composition"(.*?)</table>', page, re.S)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(1) if table else "", re.S)[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) >= 3:
            members.append({
                "name": text(cells[1]),
                "house": text(cells[2]),
                "prs_image_id": image_id((re.search(r'src="([^"]+)"', cells[1]) or [None, None])[1]),
            })
    kind = "Joint Committee" if slug.startswith("joint-committee") else "Select Committee" if slug.startswith("select-committee") else "Standing Committee"
    return {
        "id": slug,
        "name": name if name.lower().startswith(("joint", "select", "committee")) else f"Standing Committee on {name}",
        "type": kind,
        "chair": chair,
        "members": members,
        "source_url": f"{BASE}/parliamentary-committees/{slug}",
        "_plain_text": text(page),
    }


# ---------- bill matching ----------

_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}


def _ordinal_words(n: int) -> str:
    words = []
    if n >= 100:
        words.append(_ONES[n // 100] + " hundred")
        n %= 100
        if n:
            words.append("and")
    if n >= 20:
        words.append(_TENS[n // 10] + ((" " + _ONES[n % 10]) if n % 10 else ""))
    elif n:
        words.append(_ONES[n])
    phrase = " ".join(words)
    last = phrase.split(" ")[-1]
    if last in _ORD:
        return phrase[: -len(last)] + _ORD[last]
    return phrase[:-1] + "ieth" if last.endswith("y") else phrase + "th"


def normalize(title: str) -> str:
    t = title.lower().replace("-", " ")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\bthe\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def title_variants(title: str) -> set[str]:
    base = normalize(title)
    variants = {base}
    m = re.search(r"\b(\d+)(?:st|nd|rd|th)\b", base)
    if m:
        variants.add(normalize(base.replace(m.group(0), _ordinal_words(int(m.group(1))))))
    return variants


def latest_mention_date(plain: str, title_norm_variants: set[str]) -> str | None:
    norm = normalize(plain)
    dates = []
    for variant in title_norm_variants:
        for m in re.finditer(re.escape(variant), norm):
            window = norm[max(0, m.start() - 400): m.start()]
            # Real dates only: "2021-22" financial years followed by numbers must not match.
            found = re.findall(r"\b(20\d{2}) (0[1-9]|1[0-2]) (0[1-9]|[12]\d|3[01])\b", window)
            if found:
                dates.append("-".join(found[-1]))
    return max(dates) if dates else None


# ---------- assemble ----------

def build(bills_path: Path, cache: Path | None) -> dict:
    print("Scraping 18th Lok Sabha members…", file=sys.stderr)
    members = drop_former_lok_sabha_members(scrape_house("18th-lok-sabha", "Lok Sabha", {}, cache), cache)
    print("Scraping current Rajya Sabha members…", file=sys.stderr)
    members += scrape_house("rajya-sabha", "Rajya Sabha", {"MpTrackSearch[rajya_sabha_member_status]": 1}, cache)

    by_image = {m["prs_image_id"]: m for m in members if m["prs_image_id"]}
    by_name = {(normalize(m["name"]), m["house"]): m for m in members}

    def resolve(person: dict) -> str | None:
        hit = by_image.get(person.get("prs_image_id")) or by_name.get((normalize(person["name"]), person.get("house")))
        return hit["id"] if hit else None

    print("Scraping committees…", file=sys.stderr)
    committees = []
    for slug in committee_slugs(cache):
        c = parse_committee(slug, fetch(f"{BASE}/parliamentary-committees/{slug}", cache))
        for person in c["members"]:
            person["member_id"] = resolve(person)
        if c["chair"]:
            c["chair"]["member_id"] = resolve(c["chair"])
        # Committees from earlier Lok Sabhas stay on PRS; most of their members are no longer MPs.
        matched = sum(1 for person in c["members"] if person["member_id"])
        c["current"] = bool(c["members"]) and matched / len(c["members"]) > 0.5
        committees.append(c)
        print(f"  {c['name']}: {len(c['members'])} members", file=sys.stderr)

    tracked = [r for r in json.loads(bills_path.read_text(encoding="utf-8"))["records"] if r.get("eligible")]
    bills = []
    for record in tracked:
        variants = title_variants(record["title"])
        links = []
        for c in committees:
            date_seen = latest_mention_date(c["_plain_text"], variants)
            if date_seen or any(v in normalize(c["name"]) for v in variants):
                links.append({"committee_id": c["id"], "last_mentioned": date_seen})
        bills.append({
            "id": record["url"].rstrip("/").split("/")[-1],
            "citizenview_id": record.get("id"),
            "title": record["title"],
            "prs_url": record["url"],
            "committees": links,
        })

    for c in committees:
        del c["_plain_text"]
        c["permissions"] = PERMISSIONS
    for m in members:
        m["permissions"] = PERMISSIONS
    for b in bills:
        b["permissions"] = PERMISSIONS

    unmatched = sum(1 for c in committees if c["current"] for p in c["members"] if not p["member_id"])
    return {
        "schema_version": 2,
        "retrieved_on": date.today().isoformat(),
        "attribution": ATTRIBUTION,
        "sources": [f"{BASE}/mptrack", f"{BASE}/parliamentary-committees"],
        "stats": {
            "lok_sabha_members": sum(m["house"] == "Lok Sabha" for m in members),
            "rajya_sabha_members": sum(m["house"] == "Rajya Sabha" for m in members),
            "committees": len(committees),
            "current_committees": sum(c["current"] for c in committees),
            "current_committee_seats_held_by_former_mps": unmatched,
        },
        "members": members,
        "committees": committees,
        "bills": bills,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bills", type=Path, required=True, help="citizenview src/data/bill-timelines.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, help="Reuse fetched pages from this directory")
    args = parser.parse_args()
    data = build(args.bills, args.cache)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(data["stats"]), file=sys.stderr)


if __name__ == "__main__":
    main()
