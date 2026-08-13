#!/usr/bin/env python3
"""Skill router runtime: sessions, routing decisions, context bus, call chain.

All state is plain files under $SKILL_ROUTER_HOME (default <cwd>/.workbuddy/router),
so switching skills never requires restarting or re-initialising anything.

  sessions/<sid>/session.json   meta + current skill + call stack
  sessions/<sid>/context.json   shared context bus (namespaced slots)
  sessions/<sid>/trace.jsonl    append-only event log (the audit trail)

Run `router.py -h` for the command list.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry  # noqa: E402
import intent  # noqa: E402
import sentinel  # noqa: E402
import learning  # noqa: E402  (v2.1 路由反馈学习)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

AUTO_THRESHOLD = 1.2   # top1 score must clear this to auto-execute
AUTO_MARGIN = 1.30     # top1 must beat top2 by this ratio
CALL_STATES = ("open", "suspended", "ok", "failed", "partial", "skipped")

__version__ = "3.0.0"  # skill-radoute v3.0.0（自然语言解析引擎 + 交互式工作流构建）

# ----------------------------------------------------------------- route cache
# v1.8 路由决策缓存：相同查询 + 技能集版本未变 -> 直接复用打分结果，跳过
# registry.search 全量打分（66 技能 ~300ms 的主要来源）。LRU，最多 128 条。
# key = sha256(task + 决策参数 + 技能集指纹)。技能安装/卸载/修改使指纹变化，
# 旧 key 自动失效；显式 invalidate_route_cache() 双保险。
# 安全护栏：registry.search 被替换（测试/调试 monkeypatch）时自动关闭缓存，
# 保证契约测试每次都走真实打分路径。
ROUTE_CACHE_MAX = 128
_route_cache: "OrderedDict[str, tuple]" = OrderedDict()
_ORIG_SEARCH = registry.search


def _route_cache_enabled() -> bool:
    return registry.search is _ORIG_SEARCH


def _route_cache_key(a, mode: str) -> str:
    parts = [
        "task=" + (a.task or "").strip().lower(),
        "mode=" + mode,
        "top=" + str(a.top),
        "threshold=" + str(a.threshold),
        "margin=" + str(a.margin),
        "exclude=" + ",".join(sorted(e.lower() for e in (a.exclude or []))),
        "explain=" + str(bool(getattr(a, "explain", False))),
        "skills=" + registry.skills_fingerprint(),
        # v2.1: 反馈学习数据变化时旧路由缓存自动失效（文件 mtime+size 指纹）
        "fb=" + learning.feedback_fingerprint(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _route_cache_get(key: str):
    if not _route_cache_enabled():
        return None
    val = _route_cache.get(key)
    if val is not None:
        _route_cache.move_to_end(key)
    return val


def _route_cache_put(key: str, val: tuple) -> None:
    _route_cache[key] = val
    _route_cache.move_to_end(key)
    while len(_route_cache) > ROUTE_CACHE_MAX:
        _route_cache.popitem(last=False)


def invalidate_route_cache() -> None:
    """技能安装/卸载后清空路由决策缓存（指纹失效之外的双保险）。"""
    _route_cache.clear()


# ----------------------------------------------------------------- parallel exec
# v2.0 并行执行引擎：无依赖的子任务并发执行（ThreadPoolExecutor），
# 总耗时约等于最慢子任务而非之和；有依赖的子任务保持串行（由调用方分层）。
PARALLEL_MAX_WORKERS = 8


def run_parallel(tasks: list) -> list:
    """并行执行互不依赖的任务。

    `tasks`: [(name, callable)]，callable 无参。返回 [(name, ok: bool, result)]
    保序返回；任一任务抛异常不阻断其它任务（各自捕获）。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: dict = {}
    with ThreadPoolExecutor(max_workers=min(PARALLEL_MAX_WORKERS, max(1, len(tasks)))) as ex:
        futures = {ex.submit(fn): name for name, fn in tasks}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                results[name] = (True, fut.result())
            except Exception as e:
                results[name] = (False, str(e))
    return [(name,) + results[name] for name, _ in tasks]


# --------------------------------------------------------------------- helpers

def home() -> Path:
    return registry.router_home()


def sessions_dir() -> Path:
    return home() / "sessions"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_json(p: Path, default):
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default
    return default


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def current_sid() -> str | None:
    p = home() / "current_session"
    if p.is_file():
        sid = p.read_text(encoding="utf-8").strip()
        if sid and (sessions_dir() / sid / "session.json").is_file():
            return sid
    return None


def require_sid(explicit: str | None = None) -> str:
    sid = explicit or current_sid()
    if not sid:
        die("❌ 没有活跃会话。请先运行：router.py session new --goal \"...\" 创建新会话。")
    if not (sessions_dir() / sid / "session.json").is_file():
        die(f"❌ 未知会话：{sid}。请先运行 router.py session new 创建会话。")
    return sid


def sdir(sid: str) -> Path:
    return sessions_dir() / sid


def load_session(sid: str) -> dict:
    return _read_json(sdir(sid) / "session.json", {})


def save_session(sid: str, s: dict) -> None:
    s["updated_at"] = now_iso()
    _write_json(sdir(sid) / "session.json", s)


def load_ctx(sid: str) -> dict:
    return _read_json(sdir(sid) / "context.json", {"slots": {}})


def save_ctx(sid: str, c: dict) -> None:
    _write_json(sdir(sid) / "context.json", c)


def trace_append(sid: str, event: str, **fields) -> dict:
    rec = {"ts": now_iso(), "seq": _next_seq(sid), "event": event}
    rec.update(fields)
    p = sdir(sid) / "trace.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# 进程内 seq 缓存：避免每次 trace_append 都重读整个 trace.jsonl 数行数
# （v1.8 性能优化：二次路由命中缓存路径时 trace 写入从 ~10ms 降至亚毫秒）。
_seq_cache: dict[str, int] = {}


def _next_seq(sid: str) -> int:
    p = sdir(sid) / "trace.jsonl"
    if not p.is_file():
        _seq_cache[sid] = 1
        return 1
    cached = _seq_cache.get(sid)
    if cached is not None:
        nxt = cached + 1
        _seq_cache[sid] = nxt
        return nxt
    n = 0
    with p.open("r", encoding="utf-8") as f:
        for _ in f:
            n += 1
    _seq_cache[sid] = n + 1
    return n + 1


def read_trace(sid: str) -> list:
    p = sdir(sid) / "trace.jsonl"
    if not p.is_file():
        return []
    out = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def die(msg: str, code: int = 2):
    print(json.dumps({"error": msg}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def kv_pairs(items: list | None) -> dict:
    out = {}
    for it in items or []:
        if not it:
            continue
        if "=" not in it:
            die(f"❌ 参数格式错误：应为 key=value，实际为：{it}")
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = json.loads(v)
        except json.JSONDecodeError:
            out[k.strip()] = v
    return out


# -------------------------------------------------------------------- sessions

def cmd_session_new(a) -> int:
    sid = a.id or ("s-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                   + "-" + f"{random.randint(0, 0xfff):03x}")
    s = {
        "id": sid,
        "goal": a.goal or "",
        "mode": a.mode,
        "status": "active",
        "created_at": now_iso(),
        "current_skill": None,
        "stack": [],
        "call_seq": 0,
        "switches": 0,
    }
    save_session(sid, s)
    save_ctx(sid, {"slots": {}})
    (home() / "current_session").parent.mkdir(parents=True, exist_ok=True)
    (home() / "current_session").write_text(sid, encoding="utf-8")
    trace_append(sid, "session_new", goal=s["goal"], mode=s["mode"])
    emit({"session": sid, "mode": s["mode"], "state_dir": str(sdir(sid))})
    return 0


def cmd_session_list(a) -> int:
    cur = current_sid()
    rows = []
    if sessions_dir().is_dir():
        for d in sorted(sessions_dir().iterdir()):
            s = _read_json(d / "session.json", None)
            if s:
                rows.append({"id": s["id"], "status": s.get("status"),
                             "mode": s.get("mode"), "goal": s.get("goal", "")[:60],
                             "current_skill": s.get("current_skill"),
                             "calls": s.get("call_seq", 0),
                             "active": s["id"] == cur})
    emit(rows)
    return 0


def cmd_session_use(a) -> int:
    sid = require_sid(a.id)
    (home() / "current_session").write_text(sid, encoding="utf-8")
    emit({"active_session": sid})
    return 0


def cmd_session_end(a) -> int:
    sid = require_sid(a.id)
    s = load_session(sid)
    s["status"] = "ended"
    s["summary"] = a.summary or ""
    save_session(sid, s)
    trace_append(sid, "session_end", summary=s["summary"])
    emit({"session": sid, "status": "ended"})
    return 0


def cmd_status(a) -> int:
    sid = current_sid()
    if not sid:
        emit({"active_session": None,
              "hint": "router.py session new --goal \"...\""})
        return 0
    s = load_session(sid)
    ctx = load_ctx(sid)
    calls = _calls(sid)
    open_calls = [c for c in calls.values() if c["status"] in ("open", "suspended")]
    emit({
        "session": sid, "goal": s.get("goal"), "mode": s.get("mode"),
        "status": s.get("status"), "current_skill": s.get("current_skill"),
        "switches": s.get("switches", 0), "calls_total": len(calls),
        "open_calls": [{"id": c["id"], "skill": c["skill"], "status": c["status"],
                        "intent": c["intent"]} for c in open_calls],
        "context_keys": sorted(ctx.get("slots", {})),
        "state_dir": str(sdir(sid)),
    })
    return 0


# --------------------------------------------------------------------- routing

# ----------------------------------------------------------------- weak-match guard (no_match 复活)
# 越界输入若只命中单字/停用词或分数过低，直接判 no_match，让调用方走远程获取链路。

_STOPWORDS = {
    # 仅保留无语义价值的闭类功能词（冠词/代词/介词）。动作动词（create/new/
    # make/build/run/use/go 等及其变体）已移出——否则含正确技能名（如
    # skill-creator）的查询会被误判为弱匹配而丢弃有效本地候选。
    "a", "an", "the", "of", "for", "to", "with", "on", "at",
    "from", "by", "in", "it", "this", "that", "these", "those",
}


def _why_tokens(top1: dict) -> list[str]:
    toks = []
    for w in top1.get("why", []):
        body = w.split(":", 1)[1] if ":" in w else w
        for t in body.split(","):
            t = t.strip()
            if t:
                toks.append(t)
    return toks


def _is_single_or_stop(tok: str) -> bool:
    if tok.lower() in _STOPWORDS:
        return True
    if len(tok) == 1 and 0x4E00 <= ord(tok[0]) <= 0x9FFF:
        return True
    return False


def _is_weak_match(top1: dict) -> bool:
    toks = _why_tokens(top1)
    if not toks:
        return False
    return all(_is_single_or_stop(t) for t in toks)


def _is_sibling_conflict(candidates: list) -> bool:
    """检测 top1 与 top2 是否为同族技能（同 tier，且命名空间重叠）。

    命中两类同族：
    1. 子技能包含型：一个名字是另一个的名字前缀（如 tencent-docs ⊂ tencent-docs-routing）；
    2. 并列兄弟型：共享同一 vendor/模块段（首个 '-' 之前的命名空间相同，如 weixinpay-pay / weixinpay-register）。
    """
    if len(candidates) < 2:
        return False
    top1, top2 = candidates[0], candidates[1]
    if top1.get("tier") != top2.get("tier"):
        return False
    name1, name2 = top1["name"].lower(), top2["name"].lower()
    if name1.startswith(name2) or name2.startswith(name1):
        return True
    ns1, ns2 = name1.split("-")[0], name2.split("-")[0]
    return bool(ns1) and ns1 == ns2


def _is_multi_intent(spec: dict) -> bool:
    """多意图检测：>=2 个不同任务类型，应拆步路由而非取单技能。"""
    if not spec:
        return False
    return len(spec.get("sub_tasks", [])) >= 2


def _decompose(spec: dict):
    """构造多意图拆步计划与原因，供 decompose 决策复用。"""
    plan = [{"type": st["type"], "target": st.get("target"),
             "suggested_skills": intent.TYPE_SKILLS.get(st["type"], [])}
            for st in spec["sub_tasks"]]
    reason = (f"[MULTI] 检测到 {len(spec['sub_tasks'])} 个意图"
              f"（{spec['intent']}），建议拆为多步路由")
    return plan, reason


def _missing_triggers(top1: dict | None, top2: dict | None, margin: float,
                      mode: str, weak: bool, a) -> list[str]:
    """解释为何没有自动选取（仅 explain + confirm 决策时使用）。"""
    gaps: list[str] = []
    if weak and top1:
        if top1["score"] < 0.5:
            gaps.append(f"top1 分数 {top1['score']:.3f} < 0.5，匹配越界")
        else:
            gaps.append("top1 匹配全部来自单字/停用词，视为越界")
        return gaps
    if top1 and mode == "manual":
        gaps.append("mode=manual：禁止自动选取，需人工确认")
    if top1 and top1["score"] < a.threshold:
        gaps.append(f"top1 分数 {top1['score']} < 自动阈值 {a.threshold}")
    if top1 and top2 and margin < a.margin:
        gaps.append(f"领先幅度 {margin:.2f}x < 要求 {a.margin}x")
    if top1 and top2 and _is_sibling_conflict([top1, top2]):
        gaps.append(f"top1 与 top2 同族（{top1['name']} / {top2['name']}），需人工消歧")
    return gaps


def cmd_route(a) -> int:
    sid = require_sid(a.session)
    s = load_session(sid)
    mode = a.mode or s.get("mode", "auto")

    # --- pre-route guard: security boundary is always checked (hard block) ---
    sec = sentinel.check(a.task)
    if not sec["proceed"]:
        trace_append(sid, "route_blocked", task=a.task, reason=sec["reason"],
                     matched=sec.get("matched"), suggestion=sec.get("suggestion"))
        emit({"proceed": False, "reason": sec["reason"],
              "suggestion": sec.get("suggestion")})
        return 2

    # --- intent parse: 始终运行（纯标准库，零 I/O），供多意图检测消费 ---
    intent_spec = intent.parse(a.task)
    if a.guard:
        trace_append(sid, "route_intent", task=a.task, **intent_spec)
        avail = [r["name"] for r in registry.scan()["skills"]]
        cov = sentinel.check(a.task, sub_tasks=intent_spec["sub_tasks"],
                             available_skills=avail)
        for w in cov.get("warnings", []):
            trace_append(sid, "route_warning", task=a.task, warning=w)

    # --- v1.8 路由决策缓存：相同查询（task+参数+技能集指纹）直接返回 ---
    use_cache = not getattr(a, "no_cache", False)
    key = _route_cache_key(a, mode) if use_cache else None
    hit = _route_cache_get(key) if key else None
    if hit is not None:
        cands, decision, chosen, reason, sub_plan = hit
    else:
        cands = registry.search(a.task, top=a.top,
                                 with_detail=getattr(a, "explain", False))
        exclude = {e.lower() for e in (a.exclude or [])}   # 大小写不敏感
        cands = [c for c in cands if c["name"].lower() not in exclude]

        decision, chosen, reason = "no_match", None, ""
        sub_plan = None
        top1 = cands[0] if cands else None
        top2 = cands[1] if len(cands) > 1 else None
        margin = (top1["score"] / top2["score"]) if (top1 and top2 and top2["score"]) else 99.0
        weak = bool(top1) and (top1["score"] < 0.5 or _is_weak_match(top1))
        multi = _is_multi_intent(intent_spec)

        if cands and not weak:
            if _is_sibling_conflict(cands):
                decision, reason = "confirm", (
                    f"[SIBLING] top1={top1['name']} 与 top2={top2['name']} "
                    f"为同族技能（同 tier 且名称前缀重叠），强制人工确认")
            elif mode == "manual":
                decision, reason = "confirm", "mode=manual：始终由人确认"
            elif mode == "always":
                decision, chosen = "auto", top1
                reason = "mode=always：无条件取 top1"
            elif top1["score"] >= a.threshold and margin >= a.margin:
                decision, chosen = "auto", top1
                reason = (f"top1 分数 {top1['score']} ≥ 阈值 {a.threshold}，"
                          f"且领先 top2 {margin:.2f}x ≥ {a.margin}")
            else:
                decision = "confirm"
                reason = (f"top1 分数 {top1['score']}，领先幅度 {margin:.2f}x，"
                          f"未同时满足阈值 {a.threshold} 与领先 {a.margin}x")
        elif weak:
            if top1["score"] < 0.5:
                reason = f"top1 分数 {top1['score']:.3f} < 0.5，视为越界"
            else:
                reason = "top1 匹配全部来自单字/停用词，视为越界"
        else:
            reason = "本地注册表无匹配，需走远程获取流程（见 references/remote-acquisition.md）"

        # 多意图：覆盖 auto / no_match / weak（同族冲突已优先为 confirm，不覆盖）
        if multi and decision != "confirm":
            sub_plan, reason = _decompose(intent_spec)
            decision = "decompose"

        if key:
            _route_cache_put(key, (cands, decision, chosen, reason, sub_plan))

    ev = trace_append(sid, "route", task=a.task, mode=mode, decision=decision,
                      chosen=(chosen or {}).get("name"), reason=reason,
                      candidates=[{"name": c["name"], "tier": c["tier"],
                                   "score": c["score"]} for c in cands])

    # v2.0 动态加载：路由决策只保证候选 top3 的元数据可查（索引驻留，零磁盘
    # 读取）；完整脚本/依赖留到真正执行技能时按需 load_skill_full。
    registry.ensure_top_loaded(cands, top=3)

    # v2.0 并行执行信息：intent.parallelizable 自动检测，--parallel 强制启用。
    # 无依赖子任务 -> 可并行（parallel_groups 给出分层执行计划）；有依赖 -> 串行。
    force_parallel = bool(getattr(a, "parallel", False))
    auto_parallel = bool(intent_spec.get("parallelizable"))
    parallel_enabled = force_parallel or auto_parallel

    # --- explain 模式：输出透明报告，不影响正常路由路径 ---
    if getattr(a, "explain", False):
        top_candidates = []
        for c in cands:
            entry = {
                "name": c["name"], "tier": c["tier"], "score": c["score"],
                "why": c.get("why", []),
            }
            sb = c.get("score_breakdown")
            if sb:
                entry["score_breakdown"] = sb
            top_candidates.append(entry)
        report = {
            "decision_reason": reason,
            "decision": decision,
            "top_candidates": top_candidates,
            "chosen": (chosen or {}).get("name"),
        }
        if decision == "confirm":
            report["missing_trigger"] = _missing_triggers(
                top1, top2, margin, mode, weak, a)
        emit(report)
        return 0

    emit({
        "session": sid, "decision": decision, "reason": reason,
        "chosen": chosen, "candidates": cands, "trace_seq": ev["seq"],
        "intent": intent_spec,
        "multi_intent": _is_multi_intent(intent_spec),
        "sub_task_plan": sub_plan,
        # v2.0 并行执行：parallelizable=子任务是否互不依赖（自动检测），
        # parallel_groups=分层执行计划（同层可并行，层间串行），
        # parallel_enabled=最终是否并行（--parallel 强制或自动检测为真）。
        "parallelizable": auto_parallel,
        "parallel_groups": intent_spec.get("parallel_groups", []),
        "parallel_enabled": parallel_enabled,
        "note": "分数只是词法先验，最终由模型做语义判定；分数接近时优先看 why 与 description",
    })
    return 0


# ----------------------------------------------------------------------- calls

def _calls(sid: str) -> dict:
    """Rebuild call table from the append-only trace."""
    calls: dict = {}
    for r in read_trace(sid):
        e = r.get("event")
        if e == "call_open":
            calls[r["call_id"]] = {
                "id": r["call_id"], "skill": r["skill"], "intent": r.get("intent", ""),
                "parent": r.get("parent"), "inputs": r.get("inputs", {}),
                "reads": r.get("reads", []), "writes": r.get("writes", []),
                "status": "open", "opened_at": r["ts"], "closed_at": None,
                "outputs": {}, "artifacts": [], "note": "",
            }
        elif e == "call_close" and r["call_id"] in calls:
            c = calls[r["call_id"]]
            c["status"] = r["status"]
            c["closed_at"] = r["ts"]
            c["outputs"] = r.get("outputs", {})
            c["artifacts"] = r.get("artifacts", [])
            c["note"] = r.get("note", "")
        elif e == "call_suspend" and r["call_id"] in calls:
            calls[r["call_id"]]["status"] = "suspended"
        elif e == "call_resume" and r["call_id"] in calls:
            calls[r["call_id"]]["status"] = "open"
    return calls


def cmd_call_open(a) -> int:
    sid = require_sid(a.session)
    s = load_session(sid)
    if s.get("status") != "active":
        die(f"❌ 会话 {sid} 当前状态为 {s.get('status')}，不可开新调用。请先结束或新建会话。")
    s["call_seq"] = int(s.get("call_seq", 0)) + 1
    cid = f"c{s['call_seq']:03d}"
    rp = (a.parent or "").strip().lower()
    if rp in ("none", "root"):
        parent = None
    elif a.parent:
        parent = a.parent
    else:
        parent = s["stack"][-1] if s.get("stack") else None
    ctx = load_ctx(sid)
    reads = [r for r in (a.reads or []) if r]
    writes = [w for w in (a.writes or []) if w]
    missing = [k for k in reads if k not in ctx.get("slots", {})]
    trace_append(sid, "call_open", call_id=cid, skill=a.skill, intent=a.intent,
                 parent=parent, inputs=kv_pairs(a.input), reads=reads,
                 writes=writes, missing_reads=missing)
    s["stack"] = list(s.get("stack", [])) + [cid]
    s["current_skill"] = a.skill
    save_session(sid, s)
    payload = {
        "call_id": cid, "session": sid, "skill": a.skill, "intent": a.intent,
        "parent": parent, "inputs": kv_pairs(a.input),
        "context_in": {k: ctx["slots"][k]["value"] for k in reads
                       if k in ctx.get("slots", {})},
        "missing_context": missing,
        "must_write": writes,
    }
    emit(payload)
    return 0


def cmd_call_close(a) -> int:
    sid = require_sid(a.session)
    calls = _calls(sid)
    cid = a.id or _top_open(calls)
    if not cid or cid not in calls:
        die("❌ 没有可关闭的未闭合调用。请通过 --id 指定要关闭的调用。")
    outs = kv_pairs(a.output)
    trace_append(sid, "call_close", call_id=cid, status=a.status,
                 outputs=outs, artifacts=a.artifact or [],
                 note=a.note or "")
    if outs:
        ctx = load_ctx(sid)
        slots = ctx.setdefault("slots", {})
        for k, v in outs.items():
            slots[k] = _push_version(slots.get(k, {}), v, calls[cid]["skill"],
                                     cid, "text", now_iso())
            trace_append(sid, "ctx_write", key=k, rev=slots[k]["rev"],
                         written_by=calls[cid]["skill"], call_id=cid,
                         preview=str(v)[:200])
        save_ctx(sid, ctx)
    s = load_session(sid)
    s["stack"] = [x for x in s.get("stack", []) if x != cid]
    # 栈空时不回退 current_skill：switch --keep-open 挂起调用后关闭它时，
    # current_skill 仍是 switch 目标技能，不应被清空。
    if s["stack"]:
        s["current_skill"] = calls[s["stack"][-1]]["skill"]
    save_session(sid, s)
    emit({"call_id": cid, "status": a.status, "stack": s["stack"],
          "current_skill": s["current_skill"]})
    return 0


def _top_open(calls: dict) -> str | None:
    for cid in reversed(list(calls)):
        if calls[cid]["status"] == "open":
            return cid
    return None


def cmd_call_list(a) -> int:
    sid = require_sid(a.session)
    calls = _calls(sid)
    rows = [c for c in calls.values()
            if not a.open_only or c["status"] in ("open", "suspended")]
    emit(rows)
    return 0


def cmd_call_resume(a) -> int:
    sid = require_sid(a.session)
    calls = _calls(sid)
    if a.id not in calls:
        die(f"❌ 未知调用：{a.id}。请先运行 router.py call list 查看可用调用。")
    trace_append(sid, "call_resume", call_id=a.id, skill=calls[a.id]["skill"])
    s = load_session(sid)
    s["stack"] = [x for x in s.get("stack", []) if x != a.id] + [a.id]
    s["current_skill"] = calls[a.id]["skill"]
    save_session(sid, s)
    emit({"resumed": a.id, "skill": s["current_skill"], "stack": s["stack"]})
    return 0


# -------------------------------------------------------------------- switching

def cmd_switch(a) -> int:
    sid = require_sid(a.session)
    s = load_session(sid)
    frm = s.get("current_skill")
    if frm is None:
        print(json.dumps({"warning": "当前范围内没有活跃调用，switch 的 from 为空（交接源丢失）。请保持源调用打开，或先使用 --keep-open 再切换。"}, ensure_ascii=False), file=sys.stderr)
    calls = _calls(sid)
    suspended = None
    if a.keep_open:
        cid = _top_open(calls)
        if cid:
            trace_append(sid, "call_suspend", call_id=cid, skill=calls[cid]["skill"],
                         reason=a.reason)
            suspended = cid
    ctx = load_ctx(sid)
    carry = [k for k in (a.carry or []) if k in ctx.get("slots", {})]
    dropped = [k for k in (a.carry or []) if k not in carry]
    s["switches"] = int(s.get("switches", 0)) + 1
    s["current_skill"] = a.to
    save_session(sid, s)
    trace_append(sid, "switch", **{"from": frm}, to=a.to, kind=a.kind,
                 reason=a.reason, carry=carry, missing_carry=dropped,
                 suspended_call=suspended)
    emit({"from": frm, "to": a.to, "kind": a.kind, "reason": a.reason,
          "carried": {k: ctx["slots"][k]["value"] for k in carry},
          "missing_carry": dropped, "suspended_call": suspended,
          "switch_count": s["switches"],
          "note": "上下文与调用链保留在文件里，切换不需要重建会话或重跑前序步骤"})
    return 0


# ------------------------------------------------------------------ context bus

def _push_version(slot: dict, value, written_by, call_id, type_, ts) -> dict:
    """Version a context slot.

    Seeds `history` from a legacy single-value slot if missing, then appends the
    new version. Top-level keys stay mirrored to the current version so `ctx get`
    (which reads slots[key]["value"]) stays backward-compatible.
    """
    history = list(slot.get("history") or [])
    if not history and "value" in slot:
        history.append({
            "value": slot.get("value"),
            "written_by": slot.get("written_by"),
            "call_id": slot.get("call_id"),
            "rev": slot.get("rev", 1),
            "ts": slot.get("ts"),
        })
    rev = (slot.get("rev", 0) or 0) + 1
    ver = {"value": value, "written_by": written_by, "call_id": call_id,
           "rev": rev, "ts": ts}
    return {
        "value": value, "type": type_, "written_by": written_by,
        "call_id": call_id, "rev": rev, "ts": ts,
        "history": history + [ver],
    }


def cmd_ctx_set(a) -> int:
    sid = require_sid(a.session)
    ctx = load_ctx(sid)
    slots = ctx.setdefault("slots", {})
    try:
        value = json.loads(a.value) if a.json else a.value
    except json.JSONDecodeError:
        die("❌ --json 模式下 value 不是合法 JSON。请检查 JSON 格式后重试。")
    calls = _calls(sid)
    cid = _top_open(calls)
    written_by = a.by or (calls[cid]["skill"] if cid else None)
    slots[a.key] = _push_version(slots.get(a.key, {}), value, written_by, cid,
                                 "json" if a.json else "text", now_iso())
    save_ctx(sid, ctx)
    trace_append(sid, "ctx_write", key=a.key, rev=slots[a.key]["rev"],
                 written_by=written_by, call_id=cid,
                 preview=str(value)[:200])
    emit({"key": a.key, "rev": slots[a.key]["rev"], "written_by": written_by})
    return 0


def cmd_ctx_get(a) -> int:
    sid = require_sid(a.session)
    slots = load_ctx(sid).get("slots", {})
    if a.key:
        if a.key not in slots:
            die(f"❌ 上下文键不存在：{a.key}。请先通过 ctx set 写入该键。")
        cur = slots[a.key]
        if not a.meta:
            trace_append(sid, "ctx_read", key=a.key, rev=cur.get("rev"))
        emit(cur if a.meta else cur["value"])
        return 0
    emit({k: (v if a.meta else v["value"]) for k, v in slots.items()})
    return 0


def cmd_ctx_del(a) -> int:
    sid = require_sid(a.session)
    ctx = load_ctx(sid)
    if a.key in ctx.get("slots", {}):
        ctx["slots"].pop(a.key)
        save_ctx(sid, ctx)
        trace_append(sid, "ctx_del", key=a.key)
    emit({"deleted": a.key})
    return 0


def cmd_ctx_history(a) -> int:
    sid = require_sid(a.session)
    slots = load_ctx(sid).get("slots", {})
    if a.key not in slots:
        die(f"❌ 上下文键不存在：{a.key}。请先通过 ctx set 写入该键。")
    slot = slots[a.key]
    hist = slot.get("history") or [{
        "value": slot.get("value"), "written_by": slot.get("written_by"),
        "call_id": slot.get("call_id"), "rev": slot.get("rev", 1),
        "ts": slot.get("ts"),
    }]
    cur_rev = slot.get("rev")
    emit([{**h, "is_current": h.get("rev") == cur_rev} for h in hist])
    return 0


def cmd_ctx_rollback(a) -> int:
    sid = require_sid(a.session)
    ctx = load_ctx(sid)
    slots = ctx.setdefault("slots", {})
    if a.key not in slots:
        die(f"❌ 上下文键不存在：{a.key}。请先通过 ctx set 写入该键。")
    slot = slots[a.key]
    target = next((h for h in (slot.get("history") or [])
                   if h.get("rev") == a.rev), None)
    if target is None and slot.get("rev") == a.rev:
        target = slot
    if target is None:
        die(f"❌ 键 '{a.key}' 下找不到版本 rev {a.rev}。请运行 ctx history {a.key} 查看可用版本。")
    prev_rev = slot.get("rev")
    slots[a.key] = _push_version(slot, target.get("value"),
                                 target.get("written_by"), target.get("call_id"),
                                 slot.get("type", "text"), now_iso())
    slots[a.key]["rolled_back_from"] = prev_rev
    save_ctx(sid, ctx)
    trace_append(sid, "ctx_write", key=a.key, rev=slots[a.key]["rev"],
                 written_by=slots[a.key]["written_by"],
                 call_id=slots[a.key]["call_id"], rolled_back_from=prev_rev,
                 preview=str(target.get("value"))[:200])
    emit({"key": a.key, "rev": slots[a.key]["rev"],
          "value": slots[a.key]["value"], "rolled_back_from": prev_rev})
    return 0


# --------------------------------------------------------------- trace / replay

def cmd_trace(a) -> int:
    sid = require_sid(a.session)
    events = read_trace(sid)
    if a.format == "jsonl":
        for r in events:
            print(json.dumps(r, ensure_ascii=False))
        return 0
    s = load_session(sid)
    calls = _calls(sid)
    lines = [f"# 调用链 {sid}", f"目标: {s.get('goal') or '(未填)'}",
             f"模式: {s.get('mode')}  状态: {s.get('status')}  "
             f"切换次数: {s.get('switches', 0)}  调用数: {len(calls)}", ""]
    icon = {"route": "?", "call_open": "→", "call_close": "✓", "switch": "⇄",
            "ctx_write": "•", "ctx_read": "◁", "ctx_del": "×", "call_suspend": "‖",
            "call_resume": "▶", "session_new": "▷", "session_end": "■",
            "acquire": "↓"}
    parent_of = {c["id"]: c.get("parent") for c in calls.values()}

    def depth(cid: str | None) -> int:
        d, seen = 0, set()
        while cid and cid not in seen:
            seen.add(cid)
            cid = parent_of.get(cid)
            if cid:
                d += 1
        return d

    for r in events:
        e = r["event"]
        mark = icon.get(e, "·")
        t = r["ts"][11:19]
        if e == "session_new":
            lines.append(f"{r['seq']:>3} {t} {mark} 会话开始 mode={r.get('mode')}")
        elif e == "session_end":
            lines.append(f"{r['seq']:>3} {t} {mark} 会话结束 {r.get('summary', '')}")
        elif e == "call_suspend":
            lines.append(f"{r['seq']:>3} {t} {mark} 挂起 {r['call_id']} "
                         f"[{r.get('skill')}] {r.get('reason', '')}")
        elif e == "call_resume":
            lines.append(f"{r['seq']:>3} {t} {mark} 恢复 {r['call_id']} "
                         f"[{r.get('skill')}]（上下文原样保留，无需重跑）")
        elif e == "ctx_del":
            lines.append(f"{r['seq']:>3} {t} {mark} 删除上下文 {r['key']}")
        elif e == "route":
            cands = ", ".join(f"{c['name']}({c['score']})"
                              for c in r.get("candidates", [])[:3])
            lines.append(f"{r['seq']:>3} {t} {mark} 路由 [{r['decision']}] "
                         f"→ {r.get('chosen') or '-'}")
            lines.append(f"        任务: {r.get('task', '')[:90]}")
            lines.append(f"        候选: {cands}")
            lines.append(f"        依据: {r.get('reason', '')}")
        elif e == "call_open":
            pad = "  " * depth(r["call_id"])
            lines.append(f"{r['seq']:>3} {t} {mark} {pad}调用 {r['call_id']} "
                         f"[{r['skill']}] {r.get('intent', '')}")
            if r.get("reads"):
                lines.append(f"        读取上下文: {', '.join(r['reads'])}")
            if r.get("missing_reads"):
                lines.append(f"        ⚠ 缺失上下文: {', '.join(r['missing_reads'])}")
        elif e == "call_close":
            outs = ", ".join(r.get("outputs", {}).keys()) or "-"
            lines.append(f"{r['seq']:>3} {t} {mark} 结束 {r['call_id']} "
                         f"[{r['status']}] 产出: {outs}")
            if r.get("note"):
                lines.append(f"        备注: {r['note']}")
        elif e == "switch":
            lines.append(f"{r['seq']:>3} {t} {mark} 切换 {r.get('from') or '-'} "
                         f"→ {r['to']} ({r.get('kind')})")
            lines.append(f"        原因: {r.get('reason', '')}")
            if r.get("carry"):
                lines.append(f"        携带: {', '.join(r['carry'])}")
        elif e == "ctx_write":
            rb = r.get("rolled_back_from")
            tail = f" (rollback from rev{rb})" if rb else ""
            lines.append(f"{r['seq']:>3} {t} {mark} 写入 {r['key']} "
                         f"(rev{r['rev']}, by {r.get('written_by') or '-'}){tail}")
        elif e == "ctx_read":
            lines.append(f"{r['seq']:>3} {t} {mark} 读取 {r['key']} "
                         f"(rev{r.get('rev')})")
        elif e == "acquire":
            lines.append(f"{r['seq']:>3} {t} {mark} 获取远程技能 {r.get('skill')} "
                         f"← {r.get('origin')} [{r.get('audit')}]")
        else:
            extra = {k: v for k, v in r.items() if k not in ("ts", "seq", "event")}
            lines.append(f"{r['seq']:>3} {t} {mark} {e} "
                         f"{json.dumps(extra, ensure_ascii=False)[:120]}")
    out = "\n".join(lines)
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
        emit({"written": a.out, "events": len(events)})
    else:
        print(out)
    return 0


def cmd_replay(a) -> int:
    sid = require_sid(a.session)
    calls = _calls(sid)
    if a.id not in calls:
        die(f"❌ 未知调用：{a.id}。请先运行 router.py call list 查看可用调用。")
    c = calls[a.id]
    ctx = load_ctx(sid).get("slots", {})
    emit({
        "skill": c["skill"], "intent": c["intent"], "inputs": c["inputs"],
        "context_in": {k: ctx[k]["value"] for k in c["reads"] if k in ctx},
        "expected_writes": c["writes"],
        "previous_status": c["status"], "previous_outputs": c["outputs"],
        "hint": "把该 envelope 交给目标 skill 即可原样重放，无需重建会话",
    })
    return 0


def cmd_acquire_log(a) -> int:
    """Record that a skill was fetched from the network and audited."""
    sid = require_sid(a.session)
    trace_append(sid, "acquire", skill=a.skill, origin=a.origin, audit=a.audit,
                 path=a.path, note=a.note or "")
    emit({"logged": a.skill, "origin": a.origin, "audit": a.audit})
    return 0


# ------------------------------------------------- v2.1 路由反馈学习 (P1)

def cmd_feedback_list(a) -> int:
    """列出所有本地反馈条目（数据仅存于 ~/.workbuddy/feedback.json）。"""
    fbs = learning.list_all()
    if not fbs:
        print("(no feedback entries)")
        return 0
    for i, fb in enumerate(fbs, 1):
        print(f"{i}. task={fb.get('task')!r} chosen={fb.get('chosen')!r} "
              f"excluded={fb.get('excluded', [])!r} "
              f"weight={fb.get('weight', 1.0)} ts={fb.get('timestamp', 0):.0f}")
    return 0


def cmd_feedback_clear(a) -> int:
    """清空全部反馈并失效路由缓存（避免旧权重继续生效）。"""
    n = learning.clear()
    invalidate_route_cache()
    print(f"cleared {n} feedback entr{'y' if n == 1 else 'ies'}")
    return 0


def cmd_feedback_stats(a) -> int:
    """统计反馈：总记录数 + 覆盖任务数。"""
    s = learning.stats()
    print(f"entries={s['entries']} tasks={s['tasks']}")
    return 0


# ------------------------------------------------------------ v2.0 workflow

def _workflow_mod():
    try:
        import workflow
        return workflow
    except ImportError as e:
        die(f"❌ 工作流引擎不可用：{e}")


def cmd_workflow_run(a) -> int:
    """执行多技能工作流：按模板串行跑 steps，失败自动回滚该步并提示 resume。"""
    sid = require_sid(a.session)
    wf = _workflow_mod()
    try:
        return wf.cli_run(sid, a.name)
    except (FileNotFoundError, ValueError) as e:
        die(f"❌ {e}")


def cmd_workflow_resume(a) -> int:
    """从工作流失败断点续跑（已回滚步骤不会重复执行）。"""
    sid = require_sid(a.session)
    return _workflow_mod().cli_resume(sid)


def cmd_workflow_from_text(a) -> int:
    """v3.0: 自然语言 -> 工作流模板（--save 落盘 YAML）。"""
    return _workflow_mod().cli_from_text(a.text, save=a.save)


def cmd_workflow_build(a) -> int:
    """v3.0: 交互式构建工作流模板（--save 落盘 YAML）。"""
    return _workflow_mod().cli_build(save=a.save)


# ------------------------------------------------------------------ P1 bridges

def cmd_intent_parse(a) -> int:
    r = intent.parse(a.text)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(r, ensure_ascii=False))
    return 0


def cmd_sentinel_check(a) -> int:
    sub_tasks = None
    if a.subtasks:
        try:
            sub_tasks = json.loads(a.subtasks)
        except Exception as e:
            emit({"error": f"subtasks 解析失败: {e}"})
            return 1
    skills = [x.strip() for x in a.skills.split(",")] if a.skills else None
    r = sentinel.check(a.text, sub_tasks=sub_tasks, available_skills=skills)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(r, ensure_ascii=False))
    return 0 if r["proceed"] else 2


# ------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="router.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="target session id (default: active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ses = sub.add_parser("session").add_subparsers(dest="sub", required=True)
    p = ses.add_parser("new")
    p.add_argument("--goal", default="")
    p.add_argument("--mode", default="auto", choices=("auto", "always", "manual"))
    p.add_argument("--id")
    p.set_defaults(fn=cmd_session_new)
    ses.add_parser("list").set_defaults(fn=cmd_session_list)
    p = ses.add_parser("use")
    p.add_argument("id")
    p.set_defaults(fn=cmd_session_use)
    p = ses.add_parser("end")
    p.add_argument("--id")
    p.add_argument("--summary", default="")
    p.set_defaults(fn=cmd_session_end)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    p = sub.add_parser("route", help="rank + decide which skill handles a task")
    p.add_argument("task")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--mode", choices=("auto", "always", "manual"))
    p.add_argument("--threshold", type=float, default=AUTO_THRESHOLD)
    p.add_argument("--margin", type=float, default=AUTO_MARGIN)
    p.add_argument("--no-cache", action="store_true",
                   help="跳过路由决策缓存，强制重新打分（调试用）")
    p.add_argument("--parallel", action="store_true",
                   help="强制启用并行执行（默认 auto：按 intent.parallelizable 自动检测）")
    p.add_argument("--exclude", action="append", help="skill name to skip (repeatable)")
    p.add_argument("--guard", action="store_true",
                   help="路由前额外跑意图解析 + 能力/资源边界检查（安全边界始终检查）")
    p.add_argument("--explain", action="store_true",
                   help="输出详细路由报告（top_candidates/score_breakdown/"
                        "decision_reason/missing_trigger），不影响正常路由行为")
    p.set_defaults(fn=cmd_route)

    # --- v1.2 P1: intent (需求雷达) / sentinel (边界哨兵) ---
    ip = sub.add_parser("intent", help="规则引擎：自然语言 -> 结构化任务")
    isp = ip.add_subparsers(dest="sub", required=True)
    ipp = isp.add_parser("parse")
    ipp.add_argument("text")
    ipp.add_argument("--json", action="store_true")
    ipp.set_defaults(fn=cmd_intent_parse)

    sp = sub.add_parser("sentinel", help="路由前边界哨兵（安全/能力/资源）")
    ssp = sp.add_subparsers(dest="sub", required=True)
    spp = ssp.add_parser("check")
    spp.add_argument("text")
    spp.add_argument("--subtasks", help="JSON：intent.sub_tasks，用于能力覆盖判定")
    spp.add_argument("--skills", help="逗号分隔的本地技能名，用于能力覆盖判定")
    spp.add_argument("--json", action="store_true")
    spp.set_defaults(fn=cmd_sentinel_check)

    call = sub.add_parser("call").add_subparsers(dest="sub", required=True)
    p = call.add_parser("open")
    p.add_argument("--skill", required=True)
    p.add_argument("--intent", required=True)
    p.add_argument("--input", action="append", metavar="K=V")
    p.add_argument("--reads", action="append", metavar="CTX_KEY")
    p.add_argument("--writes", action="append", metavar="CTX_KEY")
    p.add_argument("--parent")
    p.set_defaults(fn=cmd_call_open)
    p = call.add_parser("close")
    p.add_argument("--id")
    p.add_argument("--status", required=True,
                   choices=("ok", "failed", "partial", "skipped"))
    p.add_argument("--output", action="append", metavar="K=V")
    p.add_argument("--artifact", action="append")
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_call_close)
    p = call.add_parser("list")
    p.add_argument("--open", dest="open_only", action="store_true")
    p.set_defaults(fn=cmd_call_list)
    p = call.add_parser("resume")
    p.add_argument("id")
    p.set_defaults(fn=cmd_call_resume)

    p = sub.add_parser("switch", help="hand off to another skill, keep all state")
    p.add_argument("--to", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--kind", default="handoff",
                   choices=("handoff", "fallback", "escalate", "retry", "rollback"))
    p.add_argument("--carry", action="append", metavar="CTX_KEY")
    p.add_argument("--keep-open", action="store_true",
                   help="suspend the current call instead of leaving it open")
    p.set_defaults(fn=cmd_switch)

    ctx = sub.add_parser("ctx").add_subparsers(dest="sub", required=True)
    p = ctx.add_parser("set")
    p.add_argument("key")
    p.add_argument("value")
    p.add_argument("--json", action="store_true", help="parse value as JSON")
    p.add_argument("--by", help="override writer name")
    p.set_defaults(fn=cmd_ctx_set)
    p = ctx.add_parser("get")
    p.add_argument("key", nargs="?")
    p.add_argument("--meta", action="store_true")
    p.set_defaults(fn=cmd_ctx_get)
    p = ctx.add_parser("history", help="list all versions of a context slot")
    p.add_argument("key")
    p.set_defaults(fn=cmd_ctx_history)
    p = ctx.add_parser("rollback", help="restore a slot to a previous revision")
    p.add_argument("key")
    p.add_argument("--rev", type=int, required=True, help="target revision number")
    p.set_defaults(fn=cmd_ctx_rollback)
    p = ctx.add_parser("del")
    p.add_argument("key")
    p.set_defaults(fn=cmd_ctx_del)

    p = sub.add_parser("trace", help="render the audit trail")
    p.add_argument("--format", default="tree", choices=("tree", "jsonl"))
    p.add_argument("--out")
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("replay", help="print a re-invocation envelope for one call")
    p.add_argument("id")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("acquire-log", help="record a network skill acquisition")
    p.add_argument("--skill", required=True)
    p.add_argument("--origin", required=True)
    p.add_argument("--audit", required=True, choices=("P0", "P1", "P2", "skipped"))
    p.add_argument("--path", default="")
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_acquire_log)

    fb = sub.add_parser("feedback", help="v2.1: 路由反馈学习（本地记录/清空/统计）")
    fbsub = fb.add_subparsers(dest="sub", required=True)
    fbsub.add_parser("list", help="列出所有反馈（初始为空）").set_defaults(
        fn=cmd_feedback_list)
    fbsub.add_parser("clear", help="清空所有反馈").set_defaults(
        fn=cmd_feedback_clear)
    fbsub.add_parser("stats", help="统计总记录数与覆盖任务数").set_defaults(
        fn=cmd_feedback_stats)

    wf = sub.add_parser("workflow", help="v2.0: 多技能工作流编排引擎")
    wsub = wf.add_subparsers(dest="sub", required=True)
    p = wsub.add_parser("run", help="执行工作流（按模板串行跑 steps）")
    p.add_argument("name", help="工作流模板名（如 research-publish）")
    p.set_defaults(fn=cmd_workflow_run)
    wsub.add_parser("resume", help="从失败断点续跑（自动回滚后恢复）").set_defaults(
        fn=cmd_workflow_resume)
    p = wsub.add_parser("from-text", help="v3.0: 从自然语言生成工作流模板")
    p.add_argument("text", help="自然语言任务描述，如 '搜索并整理AI进展'")
    p.add_argument("--save", default=None, help="保存为 YAML 模板名")
    p.set_defaults(fn=cmd_workflow_from_text)
    p = wsub.add_parser("build", help="v3.0: 交互式构建工作流模板")
    p.add_argument("--save", default=None, help="保存为 YAML 模板名")
    p.set_defaults(fn=cmd_workflow_build)
    return ap


def main() -> int:
    a = build_parser().parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
