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
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

AUTO_THRESHOLD = 1.2   # top1 score must clear this to auto-execute
AUTO_MARGIN = 1.30     # top1 must beat top2 by this ratio
CALL_STATES = ("open", "suspended", "ok", "failed", "partial", "skipped")


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
        die("no active session. run: router.py session new --goal \"...\"")
    if not (sessions_dir() / sid / "session.json").is_file():
        die(f"unknown session: {sid}")
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


def _next_seq(sid: str) -> int:
    p = sdir(sid) / "trace.jsonl"
    if not p.is_file():
        return 1
    n = 0
    with p.open("r", encoding="utf-8") as f:
        for _ in f:
            n += 1
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
            die(f"expected key=value, got: {it}")
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

def cmd_route(a) -> int:
    sid = require_sid(a.session)
    s = load_session(sid)
    mode = a.mode or s.get("mode", "auto")
    cands = registry.search(a.task, top=a.top)
    exclude = set(a.exclude or [])
    cands = [c for c in cands if c["name"] not in exclude]

    decision, chosen, reason = "no_match", None, ""
    if cands:
        top1 = cands[0]
        top2 = cands[1] if len(cands) > 1 else None
        margin = (top1["score"] / top2["score"]) if top2 and top2["score"] else 99.0
        if mode == "manual":
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
    else:
        reason = "本地注册表无匹配，需走远程获取流程（见 references/remote-acquisition.md）"

    ev = trace_append(sid, "route", task=a.task, mode=mode, decision=decision,
                      chosen=(chosen or {}).get("name"), reason=reason,
                      candidates=[{"name": c["name"], "tier": c["tier"],
                                   "score": c["score"]} for c in cands])
    emit({
        "session": sid, "decision": decision, "reason": reason,
        "chosen": chosen, "candidates": cands, "trace_seq": ev["seq"],
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
        die(f"session {sid} is {s.get('status')}; start a new one")
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
        die("no open call to close (pass --id)")
    outs = kv_pairs(a.output)
    trace_append(sid, "call_close", call_id=cid, status=a.status,
                 outputs=outs, artifacts=a.artifact or [],
                 note=a.note or "")
    if outs:
        ctx = load_ctx(sid)
        slots = ctx.setdefault("slots", {})
        for k, v in outs.items():
            prev = slots.get(k)
            slots[k] = {
                "value": v,
                "type": "text",
                "written_by": calls[cid]["skill"],
                "call_id": cid,
                "ts": now_iso(),
                "rev": (prev or {}).get("rev", 0) + 1,
            }
        save_ctx(sid, ctx)
    s = load_session(sid)
    s["stack"] = [x for x in s.get("stack", []) if x != cid]
    s["current_skill"] = calls[s["stack"][-1]]["skill"] if s["stack"] else None
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
        die(f"unknown call: {a.id}")
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
        print(json.dumps({"warning": "no active call in scope; switch 'from' is empty (handoff source lost). Keep the source call open or use --keep-open before switching."}, ensure_ascii=False), file=sys.stderr)
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

def cmd_ctx_set(a) -> int:
    sid = require_sid(a.session)
    ctx = load_ctx(sid)
    slots = ctx.setdefault("slots", {})
    try:
        value = json.loads(a.value) if a.json else a.value
    except json.JSONDecodeError:
        die("--json given but value is not valid JSON")
    calls = _calls(sid)
    cid = _top_open(calls)
    prev = slots.get(a.key)
    slots[a.key] = {
        "value": value,
        "type": "json" if a.json else "text",
        "written_by": a.by or (calls[cid]["skill"] if cid else None),
        "call_id": cid,
        "ts": now_iso(),
        "rev": (prev or {}).get("rev", 0) + 1,
    }
    save_ctx(sid, ctx)
    trace_append(sid, "ctx_set", key=a.key, rev=slots[a.key]["rev"],
                 written_by=slots[a.key]["written_by"], call_id=cid,
                 preview=str(value)[:200])
    emit({"key": a.key, "rev": slots[a.key]["rev"],
          "written_by": slots[a.key]["written_by"]})
    return 0


def cmd_ctx_get(a) -> int:
    sid = require_sid(a.session)
    slots = load_ctx(sid).get("slots", {})
    if a.key:
        if a.key not in slots:
            die(f"context key not found: {a.key}")
        emit(slots[a.key] if a.meta else slots[a.key]["value"])
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
            "ctx_set": "•", "ctx_del": "×", "call_suspend": "‖",
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
        elif e == "ctx_set":
            lines.append(f"{r['seq']:>3} {t} {mark} 写入 {r['key']} "
                         f"(rev{r['rev']}, by {r.get('written_by') or '-'})")
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
        die(f"unknown call: {a.id}")
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
    p.add_argument("--exclude", action="append", help="skill name to skip (repeatable)")
    p.set_defaults(fn=cmd_route)

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
    return ap


def main() -> int:
    a = build_parser().parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
