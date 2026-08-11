#!/usr/bin/env python3
"""Remote skill finder: resolve a trusted, version-pinned download URL.

Part of skill-radoute v1.5 remote-acquisition chain.
Step 1 of: find -> audit -> confirm -> install -> register.

v1.5 (云鼎安全修复):
  * Removed the untrusted `lightmake.site` CDN (no signature, no pinning).
  * Discovery is now a *trusted release table* lookup, not keyword scraping
    of an unverified API. The download URL is built from an explicit version
    + a known-good GitHub repo, so nothing is resolved dynamically by slug
    alone (云鼎 fix ③: version pinning).
  * GitHub Releases is the trusted, signed source. SkillHub's official
    registry endpoint is not available yet, so it is the fallback.

Design notes:
  * Pure module. No side effects: it does NOT touch trace.jsonl, the
    filesystem, or the registry. acquire.py owns those.
  * No third-party deps: stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


class FinderError(RuntimeError):
    """Recoverable caller error (empty query, unknown slug, no version)."""


# Trusted, signed GitHub Releases. SkillHub official registry endpoint not
# yet available -> this is the trusted fallback source (云鼎 fix ①).
# 下载 URL 由 build_release_url() 根据 TRUSTED_RELEASES 表拼接，此处不再保留
# 独立模板常量（此前 GITHUB_RELEASE 为死代码，从未被引用）。

# slug -> trusted release coordinates. A skill must be listed here (and its
# hash preset in acquire.KNOWN_SKILLS) before it can be acquired. Version is
# supplied at call time via --version; never resolved as `latest`.
# To onboard a skill: author fills repo/asset, computes the zip SHA256, and
# adds both entries. Until then acquisition of that slug is refused.
TRUSTED_RELEASES: dict[str, dict] = {
    "skill-radoute": {
        "repo": "aysmaa1978/skill-radoute",
        # 发布包文件名带版本号（skill-radoute-v1.5.0.skill.zip），
        # 用 {version} 占位让 URL 随版本自动生成，无需每版改表。
        "asset": "skill-radoute-{version}.skill.zip",
    },
    # 示例（待作者补全受信 repo 与锁定版本）：
    # "tavily": {"repo": "<owner>/tavily-skill", "asset": "tavily-{version}.skill.zip"},
    # "poster": {"repo": "<owner>/poster-skill", "asset": "poster-{version}.skill.zip"},
}


def build_release_url(slug: str, version: str) -> str:
    """Build a signed GitHub Releases download URL. Never resolves `latest`."""
    rel = TRUSTED_RELEASES.get(slug)
    if not rel:
        raise FinderError(
            f"技能 '{slug}' 不在受信发布表，请联系作者补充（无法自动获取）")
    if not version:
        raise FinderError(f"获取 '{slug}' 必须显式锁定版本号（禁止动态 latest）")
    repo = rel["repo"]
    asset = rel.get("asset") or f"{slug}.skill.zip"
    if "{version}" in asset:                     # 文件名带版本号时自动填充
        asset = asset.format(version=version)
    return f"https://github.com/{repo}/releases/download/{version}/{asset}"


def search(query: str, source: str = "github", limit: int = 10,
           version: str = "") -> list[dict]:
    """Resolve a slug to a single trusted candidate.

    `query` is the skill slug (explicit, no keyword scraping of an unverified
    API). A version must be supplied so the download is pinned. Returns []
    for an unknown slug so the caller can fall back to no_match.
    """
    slug = (query or "").strip()
    if not slug:
        raise FinderError("empty query")
    try:
        url = build_release_url(slug, version)
    except FinderError:
        return []
    owner = TRUSTED_RELEASES[slug]["repo"].split("/")[0]
    cand = {
        "name": slug, "slug": slug, "display_name": slug,
        "description": f"trusted GitHub Release {url}",
        "version": version, "source": "github", "source_raw": "github-releases",
        "category": "", "owner": owner,
        "score": 1.0, "downloads": 0, "installs": 0, "stars": 0,
        "requires_api_key": False,
        "homepage": f"https://github.com/{TRUSTED_RELEASES[slug]['repo']}",
        "download_url": url, "updated_at": 0,
    }
    return [cand][:limit]


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

    p = sub.add_parser("search", help="resolve a trusted skill release by slug")
    p.add_argument("query", help="skill slug, e.g. skill-radoute")
    p.add_argument("--source", default="github", choices=["github"],
                   help="trusted source (github releases only)")
    p.add_argument("--version", required=True,
                   help="pinned version tag, e.g. v1.5.0 (no dynamic latest)")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")

    a = ap.parse_args()
    if a.cmd == "search":
        try:
            cands = search(a.query, source=a.source, limit=a.limit, version=a.version)
        except FinderError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        _print(cands, a.json)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
