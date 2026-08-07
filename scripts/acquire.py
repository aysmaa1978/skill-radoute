#!/usr/bin/env python3
"""skill-radoute v1.1 remote-acquisition orchestrator.

Step 4 of: find -> audit -> confirm -> install -> register.

Drives the 5-step pipeline. Interactive by default (input()), or --auto to
skip prompts. Fail-safe: --auto REJECTS P0; P0 always needs interactive
confirm (--force only overwrites an existing install, never bypasses P0). Every
transition is appended to acquire_trace.jsonl in router-trace-compatible
format ({"ts", "seq", "event", ...}) so it reads like any other trace event.
State persists in acquire_state.json; an interrupted run resumes from the
first unfinished step via `acquire.py resume`.

Does not modify router.py / registry.py public interfaces.
"""
from __future__ import annotations

import argparse
import hashlib
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

# v1.5 (云鼎修复②): 下载后 SHA256 校验。预期值硬编码，绝不从远程获取。
# 只安装本表内、哈希匹配、且版本锁定的技能；其余一律拒绝。
# 哈希由作者用 `sha256sum <zip>` 计算后写入，禁止占位符上线。
# 下方 tavily/poster 为初始预置示例；其哈希为占位符，作者补全真实
# 发布包哈希前，这两项的获取会被拒绝并提示「请联系作者更新」。
KNOWN_SKILLS: dict[str, str] = {
    "tavily": "sha256:abc123...",   # 初始预置（占位，待作者用 sha256sum <zip> 补真实哈希）
    "poster": "sha256:def456...",   # 初始预置（占位，待作者用 sha256sum <zip> 补真实哈希）
    # 路由器自身自更新：vX.Y.Z 的哈希在下一版记录（避免「鸡生蛋」自举悖论），
    # 即本版 KNOWN_SKILLS 不内嵌自身哈希，待 v1.6.0 再补。
}
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


class _NeedAuthor(RuntimeError):
    """Slug not in trusted table / no version: ask author to onboard it."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return "sha256:" + h.hexdigest()


def _verify_hash(zip_path: Path, slug: str) -> None:
    """云鼎修复②: 比对下载 zip 的 SHA256 与硬编码预期值。

    未预置（不在 KNOWN_SKILLS）或哈希不匹配 -> 删除文件并抛错，
    绝不进入安装流程。哈希值只来自代码，不从远程获取。
    """
    expected = KNOWN_SKILLS.get(slug)
    if not expected:
        os.remove(zip_path)
        raise RuntimeError(
            f"请联系作者更新：技能 '{slug}' 未预置可信哈希，无法自动安装")
    actual = _sha256(zip_path)
    if actual != expected:
        os.remove(zip_path)
        raise RuntimeError(
            f"SHA256 校验失败：{slug} 期望 {expected}，实际 {actual}；"
            f"已删除下载文件，请联系作者更新")


def _download(sel: dict) -> Path:
    url = sel["download_url"]
    work = DL_ROOT / sel["slug"]
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    zip_path = work / f"{sel['slug']}.zip"
    req = urllib.request.Request(url, headers={"User-Agent": "skill-radoute/1.5"})
    with urllib.request.urlopen(req, timeout=90) as r, open(zip_path, "wb") as f:
        shutil.copyfileobj(r, f)
    # --- 云鼎修复②: SHA256 校验（硬编码预期值，绝不从远程获取）---
    # 未预置 / 不匹配 -> 删除文件并抛错，绝不进入安装流程。
    _verify_hash(zip_path, sel["slug"])
    # --- 完整性检查: 拒绝损坏/伪造的 zip（必须可读且含 SKILL.md）---
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if not any("SKILL.md" in name for name in zf.namelist()):
                raise ValueError("SKILL.md not found in zip")
    except Exception as e:
        os.remove(zip_path)
        raise RuntimeError(f"Integrity check failed: {e}")
    ext = work / "extracted"
    with zipfile.ZipFile(zip_path) as z:
        _safe_extract(z, ext)
    entries = [p for p in ext.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return ext


def _run_find(state: dict, args) -> list:
    st.set_step(state, "find", "in_progress")
    version = args.version or st.get_ctx(state, "version") or ""
    try:
        cands = finder.search(args.query, source=args.source,
                              limit=args.limit, version=version)
    except finder.FinderError as e:
        st.set_step(state, "find", "failed")
        raise _NeedAuthor(str(e))
    st.set_ctx(state, candidates=cands, query=args.query,
               source=args.source, version=version)
    st.set_step(state, "find", "done")
    trace("acquire_find", query=args.query, source=args.source,
          version=version, count=len(cands))
    return cands


def _run_audit(state: dict, args):
    st.set_step(state, "audit", "in_progress")
    cands = st.get_ctx(state, "candidates") or []
    sel = _pick(cands, args.slug)
    if not sel:
        trace("acquire_audit", error="no_candidate")
        st.set_step(state, "audit", "failed")
        return None
    try:
        dl = _download(sel)
    except RuntimeError as e:
        st.set_step(state, "audit", "failed")
        raise                       # _pipeline surfaces it via _fail
    report = security_check.audit(str(dl))
    st.set_ctx(state, selected=sel, audit=report, download_path=str(dl))
    st.set_step(state, "audit", "done")
    trace("acquire_audit", slug=sel["slug"], risk=report["risk"],
          verdict=report["verdict"], findings=len(report["findings"]))
    return sel, report, dl


def _run_confirm(state: dict, args, sel: dict, report: dict) -> bool:
    st.set_step(state, "confirm", "in_progress")
    v = report["verdict"]
    preset = (sel["slug"] in KNOWN_SKILLS) and bool(sel.get("version"))
    # 高风险技能包（P0）必须交互确认，--auto / --force 均不影响此拦截。
    # --force 仅用于覆盖已安装目录（见 _run_install），绝不绕过 P0 安全确认。
    if v == "P0":
        if args.auto:
            trace("acquire_confirm", decision="rejected", reason="P0_under_auto")
            st.set_step(state, "confirm", "failed")
            return False
        ans = input(f"[HIGH risk {report['risk']}] install anyway? [y/N] ").strip().lower()
        if ans != "y":
            trace("acquire_confirm", decision="rejected")
            st.set_step(state, "confirm", "failed")
            return False
    elif args.auto and not preset:
        # 云鼎修复③：--auto 仅对「已预置哈希且版本锁定」的技能生效；
        # 其余一律降级为人工确认，不自动获取。
        ans = input(f"[not pinned/preset] confirm install of {sel['slug']}? [y/N] ").strip().lower()
        if ans != "y":
            trace("acquire_confirm", decision="rejected",
                  reason="auto_requires_preset_and_pinned")
            st.set_step(state, "confirm", "failed")
            return False
    elif v == "P1" and not args.auto:
        ans = input(f"[MEDIUM risk] confirm install? [Y/n] ").strip().lower()
        if ans == "n":
            trace("acquire_confirm", decision="rejected")
            st.set_step(state, "confirm", "failed")
            return False
    st.set_step(state, "confirm", "done")
    trace("acquire_confirm", decision="approved", verdict=v, preset=preset)
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
            try:
                _run_find(state, args)
            except _NeedAuthor as e:
                return _fail(state, f"请联系作者更新：{e}")
        elif step == "audit":
            try:
                r = _run_audit(state, args)
            except RuntimeError as e:
                return _fail(state, str(e))
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
    r.add_argument("--query", required=True, help="skill slug, e.g. skill-radoute")
    r.add_argument("--source", default="github")
    r.add_argument("--version", default="",
                   help="pinned version tag, e.g. v1.5.0 (required for trusted fetch)")
    r.add_argument("--limit", type=int, default=10)
    r.add_argument("--slug", default=None, help="pin a specific candidate slug")
    r.add_argument("--auto", action="store_true",
                   help="skip prompts; only for preset+version-locked skills (P0 still rejected)")
    r.add_argument("--force", action="store_true", help="overwrite existing install only (does NOT bypass P0 confirm)")
    r.add_argument("--force-new", action="store_true",
                   help="start fresh even if a session is in progress")
    rs = sub.add_parser("resume", help="resume an interrupted session")
    rs.add_argument("--version", default="",
                   help="pinned version tag if not already captured")
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
