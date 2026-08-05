#!/usr/bin/env python3
"""skill-radoute v1.1 remote-acquisition orchestrator.

Step 4 of: find -> audit -> confirm -> install -> register.

Drives the 5-step pipeline. Interactive by default (input()), or --auto to
skip prompts. Fail-safe: --auto still REJECTS P0 unless --force. Every
transition is appended to acquire_trace.jsonl in router-trace-compatible
format ({"ts", "seq", "event", ...}) so it reads like any other trace event.
State persists in acquire_state.json; an interrupted run resumes from the
first unfinished step via `acquire.py resume`.

Does not modify router.py / registry.py public interfaces.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import finder
import security_check
import acquire_state as st

SKILLS_DIR = Path.home() / ".workbuddy" / "skills"
TRACE_FILE = Path(os.environ.get(
    "SKILL_ROUTER_ACQUIRE_TRACE",
    str(Path.home() / ".workbuddy" / "acquire_trace.jsonl")))
DL_ROOT = Path.home() / ".workbuddy" / ".acquire_dl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_seq() -> int:
    if not TRACE_FILE.is_file():
        return 1
    n = 0
    with TRACE_FILE.open("r", encoding="utf-8") as f:
        for _ in f:
            n += 1
    return n + 1


def trace(event: str, **fields) -> dict:
    rec = {"ts": _now(), "seq": _next_seq(), "event": event}
    rec.update(fields)
    TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _pick(cands: list, slug: str | None) -> dict | None:
    if not cands:
        return None
    if slug:
        for c in cands:
            if c.get("slug") == slug or c.get("name") == slug:
                return c
        return None
    return cands[0]


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for name in zf.namelist():
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            raise ValueError(f"zip slip blocked: {name}")
    zf.extractall(dest)


def _download(sel: dict) -> Path:
    url = sel["download_url"]
    work = DL_ROOT / sel["slug"]
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    zip_path = work / f"{sel['slug']}.zip"
    req = urllib.request.Request(url, headers={"User-Agent": "skill-radoute/1.1"})
    with urllib.request.urlopen(req, timeout=90) as r, open(zip_path, "wb") as f:
        shutil.copyfileobj(r, f)
    ext = work / "extracted"
    with zipfile.ZipFile(zip_path) as z:
        _safe_extract(z, ext)
    entries = [p for p in ext.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return ext


def _run_find(state: dict, args) -> list:
    st.set_step(state, "find", "in_progress")
    cands = finder.search(args.query, source=args.source, limit=args.limit)
    st.set_ctx(state, candidates=cands, query=args.query, source=args.source)
    st.set_step(state, "find", "done")
    trace("acquire_find", query=args.query, source=args.source, count=len(cands))
    return cands


def _run_audit(state: dict, args):
    st.set_step(state, "audit", "in_progress")
    cands = st.get_ctx(state, "candidates") or []
    sel = _pick(cands, args.slug)
    if not sel:
        trace("acquire_audit", error="no_candidate")
        st.set_step(state, "audit", "failed")
        return None
    dl = _download(sel)
    report = security_check.audit(str(dl))
    st.set_ctx(state, selected=sel, audit=report, download_path=str(dl))
    st.set_step(state, "audit", "done")
    trace("acquire_audit", slug=sel["slug"], risk=report["risk"],
          verdict=report["verdict"], findings=len(report["findings"]))
    return sel, report, dl


def _run_confirm(state: dict, args, sel: dict, report: dict) -> bool:
    st.set_step(state, "confirm", "in_progress")
    v = report["verdict"]
    if v == "P0" and not args.force:
        if args.auto:
            trace("acquire_confirm", decision="rejected", reason="P0_under_auto")
        else:
            ans = input(f"[HIGH risk {report['risk']}] install anyway? [y/N] ").strip().lower()
            if ans != "y":
                trace("acquire_confirm", decision="rejected")
                st.set_step(state, "confirm", "failed")
                return False
        if args.auto:
            st.set_step(state, "confirm", "failed")
            return False
    elif v == "P1" and not args.auto:
        ans = input(f"[MEDIUM risk] confirm install? [Y/n] ").strip().lower()
        if ans == "n":
            trace("acquire_confirm", decision="rejected")
            st.set_step(state, "confirm", "failed")
            return False
    st.set_step(state, "confirm", "done")
    trace("acquire_confirm", decision="approved", verdict=v)
    return True


def _run_install(state: dict, args, sel: dict, dl) -> Path | None:
    st.set_step(state, "install", "in_progress")
    name = sel.get("slug") or sel.get("name")
    dest = SKILLS_DIR / name
    if dest.exists():
        if args.force:
            shutil.rmtree(dest)
        else:
            trace("acquire_install", error="exists", name=name)
            st.set_step(state, "install", "failed")
            return None
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(dl), dest)
    st.set_ctx(state, install_path=str(dest))
    st.set_step(state, "install", "done")
    trace("acquire_install", name=name, path=str(dest))
    return dest


def _run_register(state: dict, args, sel: dict) -> bool:
    st.set_step(state, "register", "in_progress")
    try:
        import registry
        registry.scan()
        ok = True
    except Exception as e:  # registry unavailable / scan error -> not fatal
        trace("acquire_register", warning=str(e)[:200])
        ok = False
    if ok:
        st.set_step(state, "register", "done")
        trace("acquire_register", slug=sel["slug"], status="ok")
    else:
        st.set_step(state, "register", "skipped")
        trace("acquire_register", slug=sel["slug"], status="skipped")
    return ok


def _pipeline(state: dict, args, start: str) -> None:
    steps = ("find", "audit", "confirm", "install", "register")
    idx = steps.index(start)
    sel = st.get_ctx(state, "selected")
    report = st.get_ctx(state, "audit")
    dl = st.get_ctx(state, "download_path")
    for step in steps[idx:]:
        if step == "find":
            _run_find(state, args)
        elif step == "audit":
            r = _run_audit(state, args)
            if r is None:
                return _fail(state, "audit: no candidate or download failed")
            sel, report, dl = r
        elif step == "confirm":
            if not _run_confirm(state, args, sel, report):
                return _fail(state, "confirm rejected")
        elif step == "install":
            dest = _run_install(state, args, sel, dl)
            if dest is None:
                return _fail(state, "install failed (exists? use --force)")
        elif step == "register":
            _run_register(state, args, sel)
    try:
        if dl and Path(dl).exists():
            shutil.rmtree(Path(dl).parent, ignore_errors=True)
    except Exception:
        pass
    name = sel.get("slug") or sel.get("name")
    trace("acquire_done", session=state["session_id"], name=name)
    print(f"done. '{name}' installed at {SKILLS_DIR / name}")


def _fail(state: dict, msg: str) -> None:
    trace("acquire_failed", reason=msg)
    print(f"aborted: {msg}", file=sys.stderr)
    print("resume with: python acquire.py resume", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(prog="acquire.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start acquisition (new session)")
    r.add_argument("--query", required=True)
    r.add_argument("--source", default="skillhub")
    r.add_argument("--limit", type=int, default=10)
    r.add_argument("--slug", default=None, help="pin a specific candidate slug")
    r.add_argument("--auto", action="store_true", help="skip prompts (P0 still rejected)")
    r.add_argument("--force", action="store_true", help="overwrite existing / install P0")
    r.add_argument("--force-new", action="store_true",
                   help="start fresh even if a session is in progress")
    rs = sub.add_parser("resume", help="resume an interrupted session")
    rs.add_argument("--auto", action="store_true")
    rs.add_argument("--force", action="store_true")
    sp = sub.add_parser("status", help="show current state")
    sp.add_argument("--json", action="store_true")
    sub.add_parser("reset", help="clear session")
    a = ap.parse_args()

    if a.cmd == "run":
        cur = st.load()
        if st.is_complete(cur) or cur.get("session_id") is None or a.force_new:
            state = st.new_session(a.query, a.source, a.auto)
        else:
            print("session in progress; use `resume` or `--force-new`", file=sys.stderr)
            return 2
        _pipeline(state, a, "find")
    elif a.cmd == "resume":
        state = st.load()
        if st.is_complete(state) or state.get("session_id") is None:
            print("nothing to resume")
            return 0
        _pipeline(state, a, st.resume_step(state) or "find")
    elif a.cmd == "status":
        st._print(st.load(), a.json)
    elif a.cmd == "reset":
        st._print(st.reset_session(), False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
