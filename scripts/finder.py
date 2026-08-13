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
import os
import re
import sys
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# v2.1 (P0 国内镜像源适配): 下载失败时按顺序尝试的镜像源列表。
# 环境变量 SKILL_RADOUTE_MIRROR 可覆盖默认列表（逗号/空白分隔多个地址）。
MIRRORS = [
    "https://github.com",
    "https://hub.fastgit.xyz",
    "https://gitclone.com",
]
MIRROR_ENV = "SKILL_RADOUTE_MIRROR"


def mirrors() -> list[str]:
    """返回镜像源列表：优先读 SKILL_RADOUTE_MIRROR 环境变量，否则用内置默认。

    环境变量值可用逗号、分号或空白分隔多个镜像地址（如
    `SKILL_RADOUTE_MIRROR="https://github.com,https://gitclone.com"`）。
    返回的是副本，不会污染模块级 MIRRORS。
    """
    env = os.environ.get(MIRROR_ENV, "").strip()
    if env:
        parts = [p.strip() for p in re.split(r"[,;\s]+", env) if p.strip()]
        if parts:
            return parts
    return list(MIRRORS)


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


def build_release_url(slug: str, version: str,
                      mirror: str = "https://github.com") -> str:
    """Build a signed GitHub Releases download URL. Never resolves `latest`.

    `mirror` 可指定镜像源基址（v2.1），默认 GitHub 官方，向后兼容。
    """
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
    return f"{mirror.rstrip('/')}/{repo}/releases/download/{version}/{asset}"


def download(slug: str, version: str, timeout: int = 30,
             user_agent: str = "skill-radoute/2.1") -> tuple[bytes, str]:
    """按镜像顺序下载技能包，失败自动切换，返回 (内容字节, 最终 URL)。

    v2.1 (P0): 依次尝试 mirrors() 中每个镜像源；某个源连接失败/超时即自动
    切换到下一个，并在 stderr 打印中文提示。全部失败抛 FinderError。
    HTTP_PROXY / HTTPS_PROXY 环境变量由 urllib 默认代理处理器自动生效。

    注意：本函数只负责下载，不校验哈希（校验在 acquire._verify_hash）。
    """
    sources = mirrors()
    last_err: Exception | None = None
    first_url = ""
    for i, base in enumerate(sources):
        url = build_release_url(slug, version, mirror=base)
        first_url = first_url or url
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), url
        except Exception as e:  # 网络超时/不可达/HTTP 错误 -> 切换下一个
            last_err = e
            if i < len(sources) - 1:
                if base == "https://github.com":
                    print(f"⚠️ GitHub 连接超时，切换至国内镜像源 {sources[i + 1]} ...",
                          file=sys.stderr)
                else:
                    print(f"⚠️ 镜像源 {base} 连接失败（{e}），切换至 {sources[i + 1]} ...",
                          file=sys.stderr)
    raise FinderError(
        f"❌ 下载失败：已尝试全部 {len(sources)} 个镜像源（{last_err}）。"
        f"可设置 SKILL_RADOUTE_MIRROR 或 HTTP_PROXY 后重试。首个地址：{first_url}")


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
