#!/usr/bin/env python3
"""Skill registry: discover, index and search skills across all local sources.

Subcommands:
  scan    [--force] [--json]        Build/refresh the registry index.
  search  "<query>" [--top N] [--json] [--source S]
  show    <name>                      Print full record for one skill.
  sources                             List detected roots and skill counts.
  add     --path P --source remote --origin URL   Register a freshly installed skill.

Registry file: $SKILL_ROUTER_HOME/registry.json (default <cwd>/.workbuddy/router).
Scan cache:    $SKILL_ROUTER_REGISTRY_CACHE (default ~/.workbuddy/registry_cache.json).
Override builtin root with env SKILL_ROUTER_BUILTIN_DIR.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import OrderedDict
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
    try:
        st = skill_md.stat()
    except OSError:
        return None
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


def _scan_cache_path() -> Path:
    """扫描缓存文件：$SKILL_ROUTER_REGISTRY_CACHE，默认 ~/.workbuddy/registry_cache.json。"""
    env = os.environ.get("SKILL_ROUTER_REGISTRY_CACHE")
    if env:
        return Path(env)
    return Path.home() / ".workbuddy" / "registry_cache.json"


def _load_scan_cache() -> dict:
    p = _scan_cache_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("skills", {}) or {}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_scan_cache(cache: dict) -> None:
    p = _scan_cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"skills": cache}, ensure_ascii=False),
                     encoding="utf-8")
    except OSError:
        pass  # 缓存写失败不致命：下次扫描重新全量构建即可


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for blk in iter(lambda: f.read(65536), b""):
                h.update(blk)
    except OSError:
        return ""
    return h.hexdigest()


_FP_CACHE: str = ""
_FP_CACHE_TS: float = 0.0
FP_TTL = 1.0  # 指纹缓存 TTL（秒）：连续路由复用缓存指纹，O(1) 构建缓存 key


def skills_fingerprint() -> str:
    """v1.8: 已安装技能集版本指纹（path + mtime + size 的 stat 级哈希）。

    任何技能安装/卸载/内容修改都会改变指纹，用作 router 路由决策缓存的
    失效信号。纯 stat、不读文件内容。结果带 1s TTL 缓存：连续路由（间隔
    <1s）零成本复用；修改 SKILL.md 后超过 1s 的首次路由必然重算并发现变更。
    """
    global _FP_CACHE, _FP_CACHE_TS
    now = time.time()
    if _FP_CACHE and (now - _FP_CACHE_TS) < FP_TTL:
        return _FP_CACHE
    h = hashlib.sha256()
    for _tier, root in discover_roots():
        for skill_md in sorted(root.glob("*/SKILL.md")):
            try:
                st = skill_md.stat()
                sig = f"{skill_md}|{int(st.st_mtime)}|{st.st_size}"
            except OSError:
                sig = f"{skill_md}|gone"
            h.update(sig.encode("utf-8"))
    _FP_CACHE, _FP_CACHE_TS = h.hexdigest()[:16], now
    return _FP_CACHE


def _read_skill_cached(skill_md: Path, tier: str, cache: dict) -> dict | None:
    """v1.8 增量读：SKILL.md 的 mtime 未变 -> 直接复用缓存记录（不重读文件）。

    缓存条目：{"mtime": int, "sha256": str, "record": {...}}。mtime 是快速变更
    信号；sha256 摘要一并入库，供核对/调试（不参与每次变更判定，避免全量重读）。
    返回的记录是缓存记录的浅拷贝，且剥离 shadows（去重阶段现算），
    防止调用方 mutate 污染缓存。
    """
    key = str(skill_md)
    try:
        st = skill_md.stat()
    except OSError:
        return None
    mtime = int(st.st_mtime)
    ent = cache.get(key)
    if ent and ent.get("mtime") == mtime and isinstance(ent.get("record"), dict):
        rec = dict(ent["record"])
        rec.pop("shadows", None)
        return rec
    rec = read_skill(skill_md, tier)
    if not rec:
        return None
    cache[key] = {"mtime": mtime, "sha256": _sha256_file(skill_md),
                  "record": {k: v for k, v in rec.items()}}
    return rec


def scan(extra: list[dict] | None = None, force: bool = False) -> dict:
    """Build the registry index.

    v1.8: incremental. SKILL.md files whose mtime is unchanged are reused
    from the scan cache (~/.workbuddy/registry_cache.json) instead of being
    re-read, so an unchanged registry rescan is stat()-only. `force=True`
    bypasses the cache and rebuilds everything from disk (registry.py scan --force).
    """
    cache = {} if force else _load_scan_cache()
    cache = dict(cache)
    records: list[dict] = []
    by_root: list[dict] = []
    seen: set[str] = set()
    for tier, root in discover_roots():
        n = 0
        for skill_md in sorted(root.glob("*/SKILL.md")):
            key = str(skill_md)
            seen.add(key)
            rec = _read_skill_cached(skill_md, tier, cache)
            if rec:
                records.append(rec)
                n += 1
        by_root.append({"tier": tier, "root": str(root), "count": n})
    # 缓存清理：已不存在（被删除/移动）的技能条目剔除
    for key in [k for k in cache if k not in seen]:
        cache.pop(key, None)
    _save_scan_cache(cache)
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


def load_registry(auto_scan: bool = True, max_age: int = 900,
                  force: bool = False) -> dict:
    """v1.8: 内存驻留优先。内存索引新鲜则直接返回（无磁盘内容读取）；
    否则回退 registry.json 缓存 / 增量扫描，并回填内存索引。force=True 强制全量重建。"""
    if not force:
        try:
            return get_index()
        except Exception:
            pass
    p = registry_path()
    if p.is_file() and not force:
        try:
            reg = json.loads(p.read_text(encoding="utf-8"))
            fresh = (time.time() - reg.get("generated_at", 0)) < max_age
            if fresh or not auto_scan:
                return reg
        except (OSError, json.JSONDecodeError):
            pass
    reg = scan(extra=_remote_entries(), force=force)
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


# --------------------------------------------------------- memory residency
# v1.8 内存驻留：首次加载后索引常驻内存，无变更时路由/搜索零磁盘内容读取。
# 后台 watcher 线程每 30s 轮询 skills_fingerprint()（stat 级，不读文件内容），
# 检测到技能集变更时自动增量刷新内存索引，无需等待下次路由触发。
WATCH_INTERVAL = 30.0
_MEM_INDEX: dict | None = None   # 内存驻留的索引
_MEM_TS: float = 0.0             # 上次刷新时间戳
_MEM_FP: str = ""                # 上次指纹
_watcher_started = False


def _start_watcher() -> None:
    global _watcher_started
    if _watcher_started:
        return
    _watcher_started = True

    def _loop() -> None:
        while True:
            time.sleep(WATCH_INTERVAL)
            try:
                refresh_index()
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True, name="registry-watcher").start()


def refresh_index(force: bool = False) -> dict:
    """内存索引刷新。

    - 每次调用做 stat 级指纹比对（skills_fingerprint 带 1s TTL：连续路由
      零成本，间隔 >1s 才重算）；未变更直接复用内存索引（零磁盘内容读取），
      变更立即增量扫描 —— 保证「改完 SKILL.md / 装完新技能后，下次路由
      立刻用新内容」，无需等 30s 轮询窗口。
    - 冷启动 / force=True：全量扫描并回填内存与 registry.json。
    - 后台 watcher 线程每 30s 主动调用一次，变更无需路由触发也能自动刷新。
    """
    global _MEM_INDEX, _MEM_TS, _MEM_FP
    now = time.time()
    if _MEM_INDEX is not None and not force:
        fp = skills_fingerprint()                 # 1s TTL 缓存，O(1)
        if fp == _MEM_FP:
            _MEM_TS = now
            return _MEM_INDEX
        reg = scan(extra=_remote_entries())
        save_registry(reg)
    else:
        reg = scan(extra=_remote_entries(), force=force)
        save_registry(reg)
    _MEM_INDEX, _MEM_TS, _MEM_FP = reg, now, skills_fingerprint()
    return _MEM_INDEX


def get_index(force: bool = False) -> dict:
    """v1.8 内存驻留索引入口：无变更时路由直接从内存读取，无 read() 系统调用。
    首次调用自动启动 30s 轮询 watcher。force=True 强制全量重建。"""
    _start_watcher()
    return refresh_index(force=force)


# --------------------------------------------------------- dynamic loading
# v2.0 按需加载：路由决策只加载候选 top3 的轻量元数据（索引已驻留，零额外
# 读取）；执行具体技能时才加载完整内容（SKILL.md 全文 + scripts 依赖），
# 完成后进入 LRU（保留最近 5 个），超出自动卸载最近最少使用项，控制常驻内存。
DYNAMIC_CACHE_MAX = 5
_loaded_skills: "OrderedDict[str, dict]" = OrderedDict()


def load_skill_meta(slug: str) -> dict | None:
    """按需取单个技能元数据（来自内存驻留索引，零磁盘读取）。"""
    slug = (slug or "").lower()
    reg = get_index()
    for rec in reg["skills"]:
        if rec["name"].lower() == slug or rec["dir_name"].lower() == slug:
            return rec
    return None


def load_skill_full(slug: str) -> dict | None:
    """执行技能时才调用：加载完整内容（SKILL.md 全文 + scripts/*.py 依赖），
    入 LRU 缓存（保留最近 5 个，超出淘汰最久未用项）。"""
    rec = load_skill_meta(slug)
    if not rec:
        return None
    if slug in _loaded_skills:
        _loaded_skills.move_to_end(slug)
        return _loaded_skills[slug]
    payload = dict(rec)
    md = Path(rec["skill_md"])
    try:
        payload["skill_md_content"] = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        payload["skill_md_content"] = ""
    scripts: list[dict] = []
    scripts_dir = Path(rec["path"]) / "scripts"
    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.glob("*.py")):
            try:
                scripts.append({"file": f.name,
                                "content": f.read_text(encoding="utf-8", errors="replace")})
            except OSError:
                pass
    payload["scripts"] = scripts
    payload["loaded_at"] = int(time.time())
    _loaded_skills[slug] = payload
    _loaded_skills.move_to_end(slug)
    while len(_loaded_skills) > DYNAMIC_CACHE_MAX:
        _loaded_skills.popitem(last=False)   # LRU：淘汰最久未用
    return payload


def unload_skill(slug: str) -> bool:
    """手动卸载一个技能（LRU 之外的双保险）。"""
    if slug in _loaded_skills:
        del _loaded_skills[slug]
        return True
    return False


def ensure_top_loaded(cands: list, top: int = 3) -> list[str]:
    """路由决策时只保证候选 top3 的元数据可查（索引驻留，零磁盘读取）；
    完整脚本/依赖留给执行阶段按需 load_skill_full。"""
    loaded = []
    for c in (cands or [])[:top]:
        name = c.get("name") or c.get("slug")
        if name and load_skill_meta(name):
            loaded.append(name)
    return loaded


def cache_stats() -> dict:
    """v2.0: 当前内存中已加载（完整内容）的技能列表与字节量估算。"""
    items, total = [], 0
    for slug, payload in _loaded_skills.items():
        size = len(payload.get("skill_md_content", "").encode("utf-8"))
        size += sum(len(s.get("content", "").encode("utf-8"))
                    for s in payload.get("scripts", []))
        total += size
        items.append({"name": slug, "loaded_at": payload.get("loaded_at"),
                      "bytes": size})
    return {"loaded": items, "count": len(items), "max": DYNAMIC_CACHE_MAX,
            "approx_bytes": total,
            "index_skills": len(get_index()["skills"])}


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
    p.add_argument("--force", action="store_true",
                   help="强制全量扫描，忽略增量缓存（调试用）")

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

    cache = sub.add_parser("cache", help="v2.0: 动态技能加载缓存管理")
    csub = cache.add_subparsers(dest="sub", required=True)
    csub.add_parser("stats", help="查看当前内存中已加载（完整内容）的技能列表")
    p = csub.add_parser("load", help="按需加载一个技能的完整内容")
    p.add_argument("slug")
    p = csub.add_parser("evict", help="手动卸载一个技能（LRU 之外的双保险）")
    p.add_argument("slug")

    a = ap.parse_args()

    if a.cmd == "cache":
        if a.sub == "stats":
            _print(cache_stats(), getattr(a, "json", False) or True)
            return 0
        if a.sub == "load":
            payload = load_skill_full(a.slug)
            if not payload:
                print(f"未找到技能：{a.slug}", file=sys.stderr)
                return 1
            print(json.dumps({"loaded": payload["name"],
                              "scripts": [s["file"] for s in payload.get("scripts", [])],
                              "loaded_at": payload["loaded_at"]}, ensure_ascii=False))
            return 0
        if a.sub == "evict":
            ok = unload_skill(a.slug)
            print(json.dumps({"evicted": a.slug, "ok": ok}, ensure_ascii=False))
            return 0 if ok else 1

    if a.cmd == "scan":
        reg = get_index(force=a.force)  # v1.8: CLI 扫描同样回填内存驻留索引
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
