#!/usr/bin/env python3
"""Search arXiv for papers on coding-agent context files (stdlib only)."""

from __future__ import annotations

import argparse
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

API = "https://export.arxiv.org/api/query"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

BASELINE_KEYWORDS = ["context", "instructions", "prompt"]


def build_query(*, categories: list[str], days: int, keywords: list[str]) -> str:
    end = datetime.now(UTC)
    start = end - timedelta(days=days)

    def fmt(d):
        return d.strftime("%Y%m%d%H%M")

    cat_part = "(" + " OR ".join(f"cat:{c}" for c in categories) + ")"
    date_part = f"submittedDate:[{fmt(start)} TO {fmt(end)}]"

    all_kw = BASELINE_KEYWORDS + [k for k in keywords if k not in BASELINE_KEYWORDS]
    kw_part = "(" + " OR ".join(f"all:{k}" for k in all_kw) + ")"

    return f"{cat_part} AND {date_part} AND {kw_part}"


def fetch(params: dict[str, str]) -> bytes:
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "crt-arxiv-scan/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def parse(xml_bytes: bytes) -> tuple[int, list[dict]]:
    root = ET.fromstring(xml_bytes)
    total_el = root.find("opensearch:totalResults", NS)
    total = int(total_el.text.strip()) if total_el is not None and total_el.text else 0
    entries = []
    for entry in root.findall("atom:entry", NS):

        def t(tag):
            return (
                (entry.find(f"atom:{tag}", NS).text or "").strip()  # noqa: B023
                if entry.find(f"atom:{tag}", NS) is not None  # noqa: B023
                else ""
            )

        prim = entry.find("arxiv:primary_category", NS)
        summary = " ".join(t("summary").split())
        entries.append(
            {
                "title": t("title"),
                "id": t("id"),
                "published": t("published"),
                "category": prim.get("term", "") if prim is not None else "",
                "summary": summary[:400] + ("…" if len(summary) > 400 else ""),
            }
        )
    return total, entries


def main() -> int:
    p = argparse.ArgumentParser(description="arXiv search for context-file research.")
    p.add_argument("--days", type=int, default=14, help="Look back N days (default: 14)")
    p.add_argument(
        "--max", type=int, default=20, dest="max_results", help="Max results (default: 20)"
    )
    p.add_argument("keywords", nargs="*", help="Additional keywords (OR'd with baseline terms)")
    args = p.parse_args()

    categories = ["cs.SE", "cs.AI", "cs.CL"]
    query = build_query(categories=categories, days=args.days, keywords=args.keywords)
    params = {
        "search_query": query,
        "start": "0",
        "max_results": str(min(max(1, args.max_results), 500)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    try:
        raw = fetch(params)
    except urllib.error.HTTPError as e:
        print(f"error: HTTP {e.code} from arXiv API", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"error: {e.reason}", file=sys.stderr)
        return 1

    total, entries = parse(raw)
    print(f"Query: {query}")
    print(f"Total: {total}  |  Showing: {len(entries)}")
    print("-" * 72)
    for i, e in enumerate(entries, 1):
        print(f"{i}. [{e['category']}] {e['title']}")
        print(f"   {e['id']}")
        print(f"   published: {e['published']}")
        if e["summary"]:
            for line in textwrap.wrap(e["summary"], width=100):
                print(f"   {line}")
        print()
    if not entries:
        print("(No results — widen --days or drop keywords.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
