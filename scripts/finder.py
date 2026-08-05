#!/usr/bin/env python3
"""Remote skill finder: query SkillHub and return normalized candidates.

Part of skill-radoute v1.1 remote-acquisition chain.
Step 1 of: find -> audit -> confirm -> install -> register.

Design notes:
  * Pure module. No side effects: it does NOT touch trace.jsonl, the
    filesystem, or the registry. The acquire.py orchestrator owns those
    so this stays trivially testable and replayable.
  * Only SkillHub is wired up. GitHub is a declared backup source but
    intentionally NOT implemented yet (dispatcher raises
    NotImplementedError) so the call contract stays fixed for later stages.
  * No third-party deps: stdlib urllib only.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

API_BASE = "https://lightmake.site/api/v1"
SEARCH_URL = API_BASE + "/search"
DEFAULT_MIN_SCORE = 0.05  # SkillHub noise floor (see find-skills reference)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


class FinderError(RuntimeError):
    """Recoverable caller error (empty query, bad arg)."""


def _http_json(url: str, timeout: int = 20):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "skill-radoute/1.1", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _as_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def normalize_skillhub(item: dict) -> dict:
    """Map one SkillHub result item into the shared candidate shape.

    The normalized record is the contract every later stage (audit, install,
    register) consumes, so it must carry enough to display, download and
    register without re-calling the API.
    """
    ns = item.get("namespace") or {}
    labels = item.get("labels") or {}
    desc = (
        item.get("description_zh")
        or item.get("description")
        or item.get("summary")
        or ""
    ).strip()
    slug = item.get("slug", "")
    return {
        "name": item.get("name", ""),
        "slug": slug,
        "display_name": item.get("displayName") or item.get("name", ""),
        "description": desc,
        "version": item.get("version", ""),
        "source": "skillhub",
        "source_raw": item.get("source", ""),
        "category": item.get("category", ""),
        "owner": ns.get("handle") or item.get("owner_name", ""),
        "score": _as_float(item.get("score")),
        "downloads": _as_int(item.get("downloads")),
        "installs": _as_int(item.get("installs")),
        "stars": _as_int(item.get("stars")),
        "requires_api_key": str(labels.get("requires_api_key", "false")).lower() == "true",
        "homepage": item.get("homepage", ""),
        "download_url": f"{API_BASE}/download?slug={urllib.parse.quote(slug)}",
        "updated_at": _as_int(item.get("updated_at") or item.get("updatedAt") or 0),
    }


def search_skillhub(query: str, limit: int = 10,
                    min_score: float = DEFAULT_MIN_SCORE, timeout: int = 20) -> list[dict]:
    q = (query or "").strip()
    if not q:
        raise FinderError("empty query")
    url = f"{SEARCH_URL}?q={urllib.parse.quote(q)}&limit={int(limit)}"
    try:
        data = _http_json(url, timeout=timeout)
    except Exception as e:  # network/timeout/decode — degrade to empty, log to stderr
        sys.stderr.write(f"[finder] skillhub search failed: {e}\n")
        return []
    results = (data or {}).get("results") or []
    out = [normalize_skillhub(it) for it in results if normalize_skillhub(it)["score"] >= min_score]
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


# Backup source, reserved slot. Not implemented in v1.1 step 1.
_PROVIDERS = {
    "skillhub": search_skillhub,
    # "github": search_github,  # TODO: backup source, wire up later
}


def search(query: str, source: str = "skillhub", limit: int = 10,
           min_score: float = DEFAULT_MIN_SCORE) -> list[dict]:
    fn = _PROVIDERS.get(source)
    if fn is None:
        raise NotImplementedError(
            f"source '{source}' is a declared backup but not implemented yet"
        )
    return fn(query, limit=limit, min_score=min_score)


# --------------------------------------------------------------------- CLI

def _print(cands: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(cands, ensure_ascii=False, indent=2))
        return
    if not cands:
        print("(no candidates)")
        return
    for i, c in enumerate(cands, 1):
        flag = " [needs API key]" if c["requires_api_key"] else ""
        print(f"{i}. {c['display_name']} ({c['name']})  score={c['score']:.3f}{flag}")
        if c["description"]:
            print(f"   {c['description'][:160]}")
        print(f"   source={c['source']}/{c['source_raw']}  downloads={c['downloads']}"
              f"  installs={c['installs']}  stars={c['stars']}")
        print(f"   slug={c['slug']}  origin={c['homepage']}")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="finder.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="search remote skills by keyword")
    p.add_argument("query")
    p.add_argument("--source", default="skillhub", choices=["skillhub"],
                   help="source market (skillhub only for now)")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                   help="drop results below this relevance score")
    p.add_argument("--json", action="store_true")

    a = ap.parse_args()
    if a.cmd == "search":
        try:
            cands = search(a.query, source=a.source, limit=a.limit, min_score=a.min_score)
        except FinderError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        _print(cands, a.json)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
