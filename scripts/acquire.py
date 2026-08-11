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
import time
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
    # 路由器自身自更新：v1.6.0 发布包真实 SHA256（由 sha256sum 计算，
    # 已核对 skill-radoute-v1.6.0.skill.zip 实测值）。哈希硬编码、绝不来自网络；
    # 自举约定：本版源码记录本版发布包哈希（包内不内嵌自身哈希，避免鸡生蛋），
    # 供下一版引用，自更新链路可端到端跑通。
    "skill-radoute": "sha256:bba2be8311babfdbdbcc31c3ed9bc3ee5ec8ecbd2bc76a0c2357735368cd466b",
    # 以下为待作者补全真实发布包哈希的示例位；补全前获取会被拒绝并
    # 提示「请联系作者更新」。占位串不可匹配任何真实 zip，故必然失败退出。
    "tavily": "sha256:abc123...",   # 待作者用 sha256sum <tavily 发布包> 补真实哈希
    "poster": "sha256:def456...",   # 待作者用 sha256sum <poster 发布包> 补真实哈希
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
            raise ValueError(f"❌ 解压路径越界（zip slip）已被阻止：{name}。请勿安装来源不明的技能包。")
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
    req = urllib.request.Request(url, headers={"User-Agent": "skill-radoute/1.7"})
    # 国内网络适配：优先读 GITHUB_PROXY，其次标准 HTTPS_PROXY/HTTP_PROXY。
    # urllib 默认不识别自定义变量 GITHUB_PROXY，这里显式装配 ProxyHandler。
    proxy = (os.environ.get("GITHUB_PROXY")
             or os.environ.get("HTTPS_PROXY")
             or os.environ.get("HTTP_PROXY"))
    opener = (urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
        if proxy else None)
    opener_open = opener.open if opener else urllib.request.urlopen
    # v1.7：单次请求 30 秒超时（避免网络慢时永久卡死）；下载失败自动重试
    # 最多 3 次，指数退避等待 1s / 2s / 4s；下载中每 512KB 打印一次进度。
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            print(f"正在下载技能包：{sel['slug']} ...", file=sys.stderr)
            with opener_open(req, timeout=30) as r, open(zip_path, "wb") as f:
                total = 0
                while True:
                    chunk = r.read(512 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    print(f"  已下载 {total / (1024 * 1024):.2f} MB", file=sys.stderr)
            break
        except Exception as e:  # 网络超时/不可达：清理后按退避策略重试
            zip_path.unlink(missing_ok=True)
            last_err = e
            if attempt < 3:
                wait = 2 ** (attempt - 1)  # 指数退避：1s、2s、4s
                print(f"⚠️ 第 {attempt} 次下载失败（{e}），{wait}s 后自动重试...",
                      file=sys.stderr)
                time.sleep(wait)
    else:
        hint = ""
        if isinstance(last_err, (TimeoutError,)) or "timed out" in str(last_err).lower():
            hint = "（网络超时）请检查连接，或设置 GITHUB_PROXY=http://代理地址 后重试。"
        raise RuntimeError(
            f"❌ 下载失败：已重试 3 次仍无法连接（{last_err}）。{hint}"
            f"请检查网络，然后执行 python acquire.py resume 继续。")
    # --- 云鼎修复②: SHA256 校验（硬编码预期值，绝不从远程获取）---
    # 未预置 / 不匹配 -> 删除文件并抛错，绝不进入安装流程。
    _verify_hash(zip_path, sel["slug"])
    # --- 完整性检查: 拒绝损坏/伪造的 zip（必须可读且含 SKILL.md）---
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if not any("SKILL.md" in name for name in zf.namelist()):
                raise ValueError("❌ 技能包中未找到 SKILL.md，可能不是有效的技能包")
    except Exception as e:
        os.remove(zip_path)
        raise RuntimeError(f"❌ 技能包完整性校验失败：{e}。已删除下载文件，请从可信来源重新获取。")
    ext = work / "extracted"
    with zipfile.ZipFile(zip_path) as z:
        _safe_extract(z, ext)
    entries = [p for p in ext.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return ext


def _run_find(state: dict, args) -> list:
    st.set_step(state, "find", "in_progress")
    # resume 子命令没有 --query/--source/--limit：从上次会话 context 回退读取，
    # 与 version 的处理保持一致（防止 AttributeError 崩溃）。
    query = getattr(args, "query", None) or st.get_ctx(state, "query")
    source = getattr(args, "source", None) or st.get_ctx(state, "source") or "github"
    limit = getattr(args, "limit", None) or st.get_ctx(state, "limit") or 10
    version = getattr(args, "version", None) or st.get_ctx(state, "version") or ""
    try:
        cands = finder.search(query, source=source,
                              limit=limit, version=version)
    except finder.FinderError as e:
        st.set_step(state, "find", "failed")
        raise _NeedAuthor(str(e))
    st.set_ctx(state, candidates=cands, query=query,
               source=source, version=version, limit=limit)
    st.set_step(state, "find", "done")
    trace("acquire_find", query=query, source=source,
          version=version, count=len(cands))
    return cands


def _run_audit(state: dict, args):
    st.set_step(state, "audit", "in_progress")
    cands = st.get_ctx(state, "candidates") or []
    # resume 子命令没有 --slug：回退到上次会话锁定的 slug
    sel = _pick(cands, getattr(args, "slug", None) or st.get_ctx(state, "slug"))
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
    st.set_ctx(state, selected=sel, audit=report, download_path=str(dl),
               slug=sel["slug"])
    st.set_step(state, "audit", "done")
    trace("acquire_audit", slug=sel["slug"], risk=report["risk"],
          verdict=report["verdict"], findings=len(report["findings"]))
    return sel, report, dl


def _ask(prompt: str) -> str:
    """交互确认输入：非交互环境（stdin 关闭抛 EOFError）视为拒绝，不崩溃。"""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "n"


def _run_confirm(state: dict, args, sel: dict, report: dict) -> bool:
    st.set_step(state, "confirm", "in_progress")
    v = report["verdict"]
    preset = (sel["slug"] in KNOWN_SKILLS) and bool(sel.get("version"))
    # 高风险技能包（P0）必须交互确认，--auto / --force 均不影响此拦截。
    # --force 仅用于覆盖已安装目录（见 _run_install），绝不绕过 P0 安全确认。
    if v == "P0":
        # P0 高危包恒需人工确认：--auto 非交互模式下无法确认，一律拒绝
        # （--auto/--force 均不绕过，预置哈希也不例外）。
        if args.auto:
            trace("acquire_confirm", decision="rejected", reason="P0_under_auto")
            st.set_step(state, "confirm", "failed")
            return False
        ans = _ask(f"[HIGH risk {report['risk']}] install anyway? [y/N] ")
        if ans != "y":
            trace("acquire_confirm", decision="rejected")
            st.set_step(state, "confirm", "failed")
            return False
    elif args.auto and not preset:
        # 云鼎修复③：--auto 仅对「已预置哈希且版本锁定」的技能生效；
        # 其余一律降级为人工确认，不自动获取。
        ans = _ask(f"[not pinned/preset] confirm install of {sel['slug']}? [y/N] ")
        if ans != "y":
            trace("acquire_confirm", decision="rejected",
                  reason="auto_requires_preset_and_pinned")
            st.set_step(state, "confirm", "failed")
            return False
    elif v == "P1" and not args.auto:
        ans = _ask(f"[MEDIUM risk] confirm install? [Y/n] ")
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
        # v1.8: 技能集已变更，清空路由决策缓存（指纹 key 也会自动失效，这里双保险）
        try:
            import router
            router.invalidate_route_cache()
        except Exception:
            pass
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


def _pipeline(state: dict, args, start: str) -> int:
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
                return _fail(state, "❌ 审计阶段失败：没有候选技能或下载失败")
            sel, report, dl = r
        elif step == "confirm":
            if not _run_confirm(state, args, sel, report):
                return _fail(state, "❌ 安装确认被拒绝")
        elif step == "install":
            dest = _run_install(state, args, sel, dl)
            if dest is None:
                return _fail(state, "❌ 安装失败：目标目录已存在。如需覆盖请加 --force 参数。")
        elif step == "register":
            _run_register(state, args, sel)
    try:
        if dl and Path(dl).exists():
            shutil.rmtree(Path(dl).parent, ignore_errors=True)
    except Exception:
        pass
    name = sel.get("slug") or sel.get("name")
    trace("acquire_done", session=state["session_id"], name=name)
    print(f"✅ 完成：'{name}' 已安装到 {SKILLS_DIR / name}")
    return 0


def _fail(state: dict, msg: str) -> int:
    trace("acquire_failed", reason=msg)
    print(f"❌ 已中止：{msg}", file=sys.stderr)
    print("继续安装请运行：python acquire.py resume", file=sys.stderr)
    return 1


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
            print("⚠️ 已有进行中的会话。请运行 `resume` 继续，或加 `--force-new` 重新开始。", file=sys.stderr)
            return 2
        return _pipeline(state, a, "find")
    elif a.cmd == "resume":
        state = st.load()
        if st.is_complete(state) or state.get("session_id") is None:
            print("⚠️ 没有可恢复的会话（可能已完成或已重置）")
            return 0
        return _pipeline(state, a, st.resume_step(state) or "find")
    elif a.cmd == "status":
        st._print(st.load(), a.json)
    elif a.cmd == "reset":
        st._print(st.reset_session(), False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
