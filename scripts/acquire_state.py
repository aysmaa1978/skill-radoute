#!/usr/bin/env python3
"""Resumable state for skill-radoute v1.1 remote-acquisition chain.

Step 3 of: find -> audit -> confirm -> install -> register.

Persists progress to a single JSON file so an interrupted acquire run can
be resumed from the first unfinished step. Pure-ish: owns persistence only
(no network, no trace writes). Trace is the acquire.py orchestrator's job.

File: ~/.workbuddy/acquire_state.json
      (override with env SKILL_ROUTER_ACQUIRE_STATE for tests/sandboxing)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_STATE = Path.home() / ".workbuddy" / "acquire_state.json"
STEPS = ("find", "audit", "confirm", "install", "register")
STEP_STATUS = ("pending", "in_progress", "done", "failed", "skipped")


def _state_path() -> Path:
    return Path(os.environ.get("SKILL_ROUTER_ACQUIRE_STATE", str(DEFAULT_STATE)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return "acq-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()


def _blank() -> dict:
    return {
        "session_id": None,
        "query": None,
        "source": "skillhub",
        "auto": False,
        "started_at": None,
        "updated_at": None,
        "steps": {s: "pending" for s in STEPS},
        "context": {},
    }


def load() -> dict:
    p = _state_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _blank()
    return _blank()


def save(state: dict) -> None:
    state["updated_at"] = _now()
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # atomic on POSIX & Windows (same volume)


def new_session(query: str, source: str = "skillhub", auto: bool = False) -> dict:
    st = _blank()
    st["session_id"] = _new_id()
    st["query"] = query
    st["source"] = source
    st["auto"] = auto
    st["started_at"] = _now()
    st["steps"] = {s: "pending" for s in STEPS}
    st["context"] = {}
    save(st)
    return st


def set_step(state: dict, step: str, status: str) -> dict:
    if step not in STEPS:
        raise ValueError(f"unknown step: {step}")
    if status not in STEP_STATUS:
        raise ValueError(f"unknown status: {status}")
    state["steps"][step] = status
    save(state)
    return state


def set_ctx(state: dict, **kwargs) -> dict:
    state.setdefault("context", {})
    state["context"].update(kwargs)
    save(state)
    return state


def get_ctx(state: dict, key, default=None):
    return state.get("context", {}).get(key, default)


def resume_step(state: dict) -> str | None:
    for s in STEPS:
        if state["steps"].get(s) not in ("done", "skipped"):
            return s
    return None


def is_complete(state: dict) -> bool:
    return all(state["steps"].get(s) == "done" for s in STEPS)


def reset_session() -> dict:
    st = _blank()
    save(st)
    return st


def _print(state: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    print(f"session: {state.get('session_id')}")
    print(f"query:   {state.get('query')}  source: {state.get('source')}  auto: {state.get('auto')}")
    print("steps:")
    for s in STEPS:
        print(f"  {s:8} {state['steps'].get(s)}")
    print(f"resume:  {resume_step(state)}")
    print(f"done:    {is_complete(state)}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="acquire_state.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("show", help="print current state")
    p.add_argument("--json", action="store_true")
    n = sub.add_parser("new", help="start a new acquire session")
    n.add_argument("--query", required=True)
    n.add_argument("--source", default="skillhub")
    n.add_argument("--auto", action="store_true")
    m = sub.add_parser("mark", help="set a step status (+ optional context)")
    m.add_argument("--step", required=True, choices=STEPS)
    m.add_argument("--status", required=True, choices=STEP_STATUS)
    m.add_argument("--set", nargs="*", default=[], metavar="K=V",
                   help="merge K=V into context; V parsed as JSON if possible")
    sub.add_parser("resume", help="print next step to run")
    sub.add_parser("reset", help="clear current session")
    a = ap.parse_args()

    if a.cmd == "show":
        _print(load(), a.json)
    elif a.cmd == "new":
        _print(new_session(a.query, a.source, a.auto), False)
    elif a.cmd == "mark":
        st = load()
        set_step(st, a.step, a.status)
        if a.set:
            kv = {}
            for item in a.set:
                k, _, v = item.partition("=")
                try:
                    v = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    pass
                kv[k] = v
            set_ctx(st, **kv)
        _print(st, False)
    elif a.cmd == "resume":
        print(resume_step(load()) or "complete")
    elif a.cmd == "reset":
        _print(reset_session(), False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
