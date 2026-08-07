#!/usr/bin/env python3
"""Skill registry: discover, index and search skills across all local sources.

Subcommands:
  scan    [--refresh] [--json]        Build/refresh the registry index.
  search  "<query>" [--top N] [--json] [--source S]
  show    <name>                      Print full record for one skill.
  sources                             List detected roots and skill counts.
  add     --path P --source remote --origin URL   Register a freshly installed skill.

Registry file: $SKILL_ROUTER_HOME/registry.json (default <cwd>/.workbuddy/router).
Override builtin root with env SKILL_ROUTER_BUILTIN_DIR.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------- source tiers

TIER_WEIGHT = {
    "project": 1.00,
    "user": 0.96,
    "plugin": 0.92,
    "builtin": 0.90,
    "connector": 0.80,
    "remote": 0.60,  # known but not installed
}


def router_home() -> Path:
    env = os.environ.get("SKILL_ROUTER_HOME")
    if env:
        return Path(env)
    return Path.cwd() / ".workbuddy" / "router"


def _builtin_roots() -> list[Path]:
    env = os.environ.get("SKILL_ROUTER_BUILTIN_DIR")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    home = Path.home()
    candidates = [
        Path("/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/"
             "resources/builtin-skills"),
        Path("/Applications/CodeBuddy.app/Contents/Resources/app.asar.unpacked/"
             "resources/builtin-skills"),
    ]
    for base in (
        Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local"))),
        Path("C:/Users") / home.name / "AppData/Local",
        Path("D:/Users") / home.name / "AppData/Local",
    ):
        for app in ("WorkBuddy", "CodeBuddy"):
            candidates.append(
                base / "Programs" / app / "resources" / "app.asar.unpacked"
                / "resources" / "builtin-skills"
            )
    return candidates


def discover_roots() -> list[tuple[str, Path]]:
    """Return [(tier, root_dir)] for every existing skill container directory."""
    home = Path.home()
    roots: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def push(tier: str, p: Path) -> None:
        try:
            rp = p.resolve()
        except OSError:
            return
        key = str(rp).lower()
        if rp.is_dir() and key not in seen:
            seen.add(key)
            roots.append((tier, rp))

    push("project", Path.cwd() / ".workbuddy" / "skills")
    for agent_dir in (".workbuddy", ".codebuddy"):
        push("user", home / agent_dir / "skills")
        push("connector", home / agent_dir / "connectors" / "skills")
    for p in _builtin_roots():
        push("builtin", p)
    # plugin skills: ~/.workbuddy/plugins/cache/<vendor>/<plugin>/<ver>/skills
    for agent_dir in (".workbuddy", ".codebuddy"):
        cache = home / agent_dir / "plugins" / "cache"
        if cache.is_dir():
            for skills_dir in cache.glob("*/*/*/skills"):
                push("plugin", skills_dir)
    return roots


# ------------------------------------------------------------ frontmatter read

_SCALAR = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    return v.replace('\\"', '"').replace("\\'", "'").strip()


def parse_frontmatter(text: str) -> dict:
    """Minimal YAML frontmatter reader. Handles top-level scalars, block
    scalars (| and >) and inline lists. Nested maps/lists are skipped."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    body = text[text.find("\n") + 1:end]
    out: dict = {}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0] in (" ", "\t", "-"):  # nested content of a skipped key
            continue
        m = _SCALAR.match(raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in ("|", "|-", ">", ">-"):
            buf = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                buf.append(lines[i].strip())
                i += 1
            joiner = "\n" if val.startswith("|") else " "
            out[key] = joiner.join(x for x in buf if x)
            continue
        if val.startswith("[") and val.endswith("]"):
            out[key] = [_unquote(x) for x in val[1:-1].split(",") if x.strip()]
            continue
        if val == "":
            # possibly a nested block; capture inline list items if present
            items = []
            while i < len(lines) and lines[i][:1] in (" ", "\t"):
                s = lines[i].strip()
                if s.startswith("- "):
                    items.append(_unquote(s[2:]))
                i += 1
            if items:
                out[key] = items
            continue
        out[key] = _unquote(val)
    return out


def read_skill(skill_md: Path, tier: str) -> dict | None:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm = parse_frontmatter(text)
    name = str(fm.get("name") or skill_md.parent.name).strip()
    if not name:
        return None
    desc = str(fm.get("description") or fm.get("description_zh") or "").strip()
    tags: list[str] = []
    for k in ("xiaping_trigger", "xiaping_tags", "xiaping_category", "keywords", "tags"):
        v = fm.get(k)
        if isinstance(v, list):
            tags.extend(str(x) for x in v)
        elif isinstance(v, str) and v:
            tags.append(v)
    st = skill_md.stat()
    d = skill_md.parent
    return {
        "name": name,
        "dir_name": d.name,
        "tier": tier,
        "path": str(d),
        "skill_md": str(skill_md),
        "description": desc,
        "display_name": str(fm.get("displayName") or fm.get("display_name") or ""),
        "version": str(fm.get("version") or ""),
        "tags": sorted(set(t for t in tags if t)),
        "has_scripts": (d / "scripts").is_dir(),
        "has_references": (d / "references").is_dir(),
        "size_bytes": st.st_size,
        "mtime": int(st.st_mtime),
    }


def scan(extra: list[dict] | None = None) -> dict:
    records: list[dict] = []
    by_root: list[dict] = []
    for tier, root in discover_roots():
        n = 0
        for skill_md in sorted(root.glob("*/SKILL.md")):
            rec = read_skill(skill_md, tier)
            if rec:
                records.append(rec)
                n += 1
        by_root.append({"tier": tier, "root": str(root), "count": n})
    # de-dup by (name) keeping the highest tier weight
    best: dict[str, dict] = {}
    for r in records:
        k = r["name"].lower()
        cur = best.get(k)
        if cur is None or TIER_WEIGHT.get(r["tier"], 0) > TIER_WEIGHT.get(cur["tier"], 0):
            if cur is not None:
                r.setdefault("shadows", []).append(cur["path"])
            best[k] = r
        else:
            cur.setdefault("shadows", []).append(r["path"])
    for r in extra or []:
        best[str(r.get("name", "")).lower()] = r
    return {
        "generated_at": int(time.time()),
        "cwd": str(Path.cwd()),
        "roots": by_root,
        "skills": sorted(best.values(), key=lambda x: (x["tier"], x["name"].lower())),
    }


def registry_path() -> Path:
    return router_home() / "registry.json"


def load_registry(auto_scan: bool = True, max_age: int = 900) -> dict:
    p = registry_path()
    if p.is_file():
        try:
            reg = json.loads(p.read_text(encoding="utf-8"))
            fresh = (time.time() - reg.get("generated_at", 0)) < max_age
            if fresh or not auto_scan:
                return reg
        except (OSError, json.JSONDecodeError):
            pass
    reg = scan(extra=_remote_entries())
    save_registry(reg)
    return reg


def _remote_entries() -> list[dict]:
    """Manually registered remote/installed-elsewhere skills persist here."""
    p = router_home() / "registry_extra.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
    return []


def save_registry(reg: dict) -> None:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------- scoring

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "for", "to", "and", "or", "with", "use", "using",
    "when", "skill", "help", "me", "my", "please", "i", "it", "is", "on", "in",
    "帮", "我", "的", "了", "个", "一", "是", "要", "请", "把", "给", "用",
    # 纯功能字：代词/疑问/助词/程度副词。只在 n-gram 全由功能字组成时才整体丢弃，
    # 所以这里放宽是安全的；带路由信号的字（做/写/画/查/图/文/中/看）绝不入列。
    "你", "他", "她", "它", "们", "咱", "谁", "哪", "什", "么", "怎", "样",
    "呢", "吗", "吧", "啊", "呀", "嘛", "着", "地", "得", "有", "在", "和",
    "与", "及", "或", "但", "而", "且", "就", "都", "也", "还", "才", "只",
    "又", "很", "太", "更", "最", "这", "那", "此", "该", "些", "想", "让",
    "被", "从", "对", "向", "为", "以", "于", "之", "者", "其", "会", "能",
    "可", "二", "三",
}


def tokenize(text: str) -> dict:
    """Return {token: weight}. Latin words 1.0, CJK bigram 1.0, trigram 1.2,
    single CJK char 0.3 (too noisy to trust on its own)."""
    text = (text or "").lower()
    toks: dict = {}

    def bump(t: str, w: float) -> None:
        if not t or t in _STOP:
            return
        # 停用词经 n-gram 组合泄漏回来：帮/我 各自被过滤，"帮我" 却照样入池，
        # 于是 "帮我遛狗"/"今天天气怎么样" 靠纯语法碎片拿到高分。整串都是功能字就丢弃。
        if len(t) > 1 and _CJK.match(t) and all(c in _STOP for c in t):
            return
        toks[t] = max(toks.get(t, 0.0), w)

    for w in _WORD.findall(text):
        if len(w) > 1:
            bump(w, 1.0)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for ch in run:
            bump(ch, 0.3)
        for i in range(len(run) - 1):
            bump(run[i:i + 2], 1.0)
        for i in range(len(run) - 2):
            bump(run[i:i + 3], 1.2)
    return toks


@functools.lru_cache(maxsize=256)
def query_mass(text: str) -> float:
    """Semantic size of a query: one latin word = 1, CJK ≈ 1 per 2 chars.

    Used as the scoring denominator instead of the raw token sum. CJK expands
    into singles+bigrams+trigrams (6 chars -> 15 tokens, mass 11.6), which used
    to inflate the denominator and hand CJK queries a ~35% score penalty versus
    an identical latin query. Measuring the query in semantic units instead
    puts the two at parity.
    """
    text = (text or "").lower()
    latin = sum(1 for w in _WORD.findall(text) if len(w) > 1 and w not in _STOP)
    cjk = sum(len(r) for r in re.findall(r"[\u4e00-\u9fff]+", text))
    return latin + cjk / 2.0


def _stem_match(q: str, d: str) -> bool:
    """Prefix match that keeps inflections but rejects unrelated stems.

    Bare bidirectional prefix at len>=4 leaked badly: data~database,
    auto~automation, mark~marketplace, word~wordpress all matched. Requiring
    the shared prefix to cover >50% of the longer token keeps the intended
    debug~debugging / publish~publisher while dropping those.
    """
    if not (d.startswith(q) or q.startswith(d)):
        return False
    return min(len(q), len(d)) / max(len(q), len(d)) > 0.5


def _overlap(qt: dict, dt: dict, idf: dict) -> tuple[float, list[str]]:
    """Weighted overlap. Latin tokens also match by prefix (debug ~ debugging).
    Rare tokens count more (idf), so filler chars cannot dominate."""
    total = 0.0
    hits: list = []
    dkeys = [k for k in dt if k.isascii() and len(k) >= 4]
    for q, qw in qt.items():
        w = dt.get(q)
        if w is not None:
            gain = qw * w * idf.get(q, 1.0)
        elif len(q) >= 4 and q.isascii():
            gain = 0.0
            for d in dkeys:
                if _stem_match(q, d):
                    gain = max(gain, qw * dt[d] * idf.get(d, 1.0) * 0.7)
            if not gain:
                continue
        else:
            continue
        total += gain
        hits.append((gain, q))
    hits.sort(key=lambda x: -x[0])
    return total, [h[1] for h in hits]


# --------------------------------------------------- semantic (synonym) lift
# 同义词 / 中英对齐表。组内词视作等价：查询词与技能自身 token 落在同一组即语义命中。
# 纯数据驱动，无嵌入模型（ponytail: 预置词典覆盖常见同义即可）。
#
# 设计要点（防误判）：
#  - 单字 CJK（搜/写/画）作为组内成员可用于"文档侧链接"——技能描述里常只留单字，
#    多字查询同义词（检索/撰写/绘制）借此命中；但查询侧单字已被 _semantic_boost 跳过，
#    所以单字不会自触发 boost。
#  - 组内词都是动作词，不会出现在不相关技能的描述里形成误命中（boost 仅当技能自身
#    token 含同组成员才触发），因此无关技能拿不到语义加分。
_SYNONYMS: dict[str, list[str]] = {
    "search": ["查找", "搜索", "检索", "搜"],
    "draw":   ["画图", "绘制", "绘图", "画"],
    "write":  ["撰写", "创作", "写作", "写"],
}
_SYNONYMS_IDX: dict[str, set[str]] = {}
SEMANTIC_WEIGHT = 0.3  # 每个命中查询词（按 token 权重缩放）对 raw 的加成分（保守值，防误判）


def _build_syn_index() -> None:
    idx: dict[str, set[str]] = {}
    for _canon, _alts in _SYNONYMS.items():
        _grp = {_canon, *_alts}
        for _t in _grp:
            idx.setdefault(_t, set()).update(_grp)
    _SYNONYMS_IDX.clear()
    _SYNONYMS_IDX.update(idx)


_build_syn_index()


def _semantic_boost(qtoks, ntoks, dtoks, ttoks):
    """Cross-lingual / synonym lift. Returns (matched_query_terms, gain).

    A query term q boosts a skill only when q is NOT already a lexical hit
    (no double counting) AND q shares a synonym group with one of the
    skill's own tokens. Because the group is derived from the skill's own
    tokens, unrelated skills get no lift -> no false positives.
    """
    if not _SYNONYMS_IDX:
        return [], 0.0
    doc_all = set(ntoks) | set(dtoks) | set(ttoks)
    # 单字 CJK 噪声大（"查"/"画" 随处出现），语义匹配只用多字词，与 tokenize
    # 对单字的不信任一致。
    doc_sem = {d for d in doc_all if not (len(d) == 1 and _CJK.match(d))}
    matched = []
    for q in qtoks:
        if len(q) == 1 and _CJK.match(q):
            continue
        if q in doc_all:            # 已词法命中，避免重复计分
            continue
        grp = _SYNONYMS_IDX.get(q)
        if not grp:
            continue
        if any(d in grp and d != q for d in doc_sem):
            matched.append(q)
    if not matched:
        return [], 0.0
    gain = SEMANTIC_WEIGHT * sum(qtoks[q] for q in matched)
    return matched, gain


def doc_tokens(rec: dict) -> tuple[dict, dict, dict]:
    ntoks = tokenize(str(rec.get("name", "")) + " " + str(rec.get("dir_name", ""))
                     + " " + str(rec.get("display_name", "")))
    dtoks = tokenize(str(rec.get("description", "")))
    ttoks = tokenize(" ".join(rec.get("tags", [])))
    return ntoks, dtoks, ttoks


def build_idf(records: list) -> dict:
    import math
    n = max(1, len(records))
    df: dict = {}
    for rec in records:
        seen = set()
        for d in doc_tokens(rec):
            seen |= set(d)
        for t in seen:
            df[t] = df.get(t, 0) + 1
    return {t: math.log(1.0 + n / (1.0 + c)) for t, c in df.items()}


def score_skill(qtoks: dict, query_lc: str, rec: dict,
                idf: dict | None = None) -> tuple[float, list[str], dict]:
    """Return (score, why, detail).

    `detail` carries the lexical sub-scores plus the semantic-boost breakdown so
    callers such as `route --explain` can render a transparent score_breakdown.
    The return arity grows from 2 to 3; non-explain callers keep unpacking the
    first two values and ignore `detail`.
    """
    idf = idf or {}
    name = str(rec.get("name", "")).lower()
    ntoks, dtoks, ttoks = doc_tokens(rec)

    why: list[str] = []
    sn, hit_n = _overlap(qtoks, ntoks, idf)
    sd, hit_d = _overlap(qtoks, dtoks, idf)
    st, hit_t = _overlap(qtoks, ttoks, idf)
    raw = 3.0 * sn + 1.0 * sd + 1.5 * st
    name_bonus = 0.0
    if name and name in query_lc:
        name_bonus = 8.0
        raw += name_bonus
        why.append(f"名称直接出现在任务里: {name}")
    if hit_n:
        why.append("名称命中: " + ",".join(hit_n[:6]))
    if hit_t:
        why.append("标签命中: " + ",".join(hit_t[:6]))
    if hit_d:
        why.append("描述命中: " + ",".join(hit_d[:8]))
    matched, sem_gain = _semantic_boost(qtoks, ntoks, dtoks, ttoks)
    raw += sem_gain
    if matched:
        why.append("语义同义命中: " + ",".join(matched[:6]))
    detail = {
        "name_score": round(sn, 4),
        "desc_score": round(sd, 4),
        "tag_score": round(st, 4),
        "name_in_query_bonus": round(name_bonus, 4),
        "semantic_matched": matched,
        "semantic_gain": round(sem_gain, 4),
        "raw": round(raw, 4),
    }
    if raw <= 0:
        detail["final"] = 0.0
        return 0.0, why, detail
    # normalize against query size so long queries do not inflate scores.
    # query_mass, not sum(qtoks): CJK n-gram expansion otherwise deflates scores.
    norm = raw / (query_mass(query_lc) ** 0.5 + 3.0)
    tw = TIER_WEIGHT.get(rec.get("tier", "user"), 0.8)
    final = norm * tw
    detail["norm"] = round(norm, 4)
    detail["tier_weight"] = tw
    detail["final"] = round(final, 4)
    return round(final, 4), why, detail


def search(query: str, top: int = 5, source: str | None = None,
           reg: dict | None = None, with_detail: bool = False) -> list[dict]:
    reg = reg or load_registry()
    qtoks = tokenize(query)
    qlc = (query or "").lower()
    records = reg.get("skills", [])
    idf = build_idf(records)
    out = []
    for rec in records:
        if source and rec.get("tier") != source:
            continue
        s, why, detail = score_skill(qtoks, qlc, rec, idf)
        if s > 0:
            item = {
                "name": rec["name"], "tier": rec["tier"], "score": s,
                "path": rec["path"], "skill_md": rec["skill_md"],
                "description": rec.get("description", "")[:300],
                "why": why,
            }
            # 仅当要求解释时附带 score_breakdown，保持非 explain 输出不变
            if with_detail:
                item["score_breakdown"] = detail
            out.append(item)
    out.sort(key=lambda x: -x["score"])
    return out[:top]


# ------------------------------------------------------------------------ CLI

def _print(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return
    if isinstance(obj, list):
        for i, r in enumerate(obj, 1):
            print(f"{i}. [{r['tier']}] {r['name']}  score={r['score']}")
            if r.get("description"):
                print(f"   {r['description'][:160]}")
            for w in r.get("why", [])[:3]:
                print(f"   · {w}")
            print(f"   path: {r['path']}")
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(prog="registry.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="build/refresh index")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("search", help="rank skills against a task description")
    p.add_argument("query")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--source", choices=sorted(TIER_WEIGHT))
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("show", help="print one skill record")
    p.add_argument("name")

    sub.add_parser("sources", help="list detected roots")

    p = sub.add_parser("add", help="register an externally installed skill")
    p.add_argument("--path", required=True)
    p.add_argument("--origin", default="")
    p.add_argument("--tier", default="user", choices=sorted(TIER_WEIGHT))

    a = ap.parse_args()

    if a.cmd == "scan":
        reg = scan(extra=_remote_entries())
        save_registry(reg)
        summary = {"skills": len(reg["skills"]), "roots": reg["roots"],
                   "registry": str(registry_path())}
        _print(summary, a.json or True)
        return 0

    if a.cmd == "search":
        _print(search(a.query, a.top, a.source), a.json)
        return 0

    if a.cmd == "show":
        reg = load_registry()
        for r in reg["skills"]:
            if r["name"].lower() == a.name.lower() or r["dir_name"].lower() == a.name.lower():
                print(json.dumps(r, ensure_ascii=False, indent=2))
                return 0
        print(f"not found: {a.name}", file=sys.stderr)
        return 1

    if a.cmd == "sources":
        reg = load_registry()
        for r in reg["roots"]:
            print(f"[{r['tier']:9}] {r['count']:3}  {r['root']}")
        print(f"total indexed: {len(reg['skills'])}")
        return 0

    if a.cmd == "add":
        d = Path(a.path)
        md = d / "SKILL.md" if d.is_dir() else d
        rec = read_skill(md, a.tier)
        if not rec:
            print(f"cannot read skill at {a.path}", file=sys.stderr)
            return 1
        rec["origin"] = a.origin
        extra = [x for x in _remote_entries() if x.get("name") != rec["name"]]
        extra.append(rec)
        ep = router_home() / "registry_extra.json"
        ep.parent.mkdir(parents=True, exist_ok=True)
        ep.write_text(json.dumps(extra, ensure_ascii=False, indent=2), encoding="utf-8")
        save_registry(scan(extra=extra))
        print(json.dumps({"registered": rec["name"], "tier": rec["tier"],
                          "path": rec["path"]}, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
