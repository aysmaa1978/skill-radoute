#!/usr/bin/env python3
"""Acquire security-fix contract tests (v1.5 云鼎修复 ①②③).

Pure stdlib, no network. Proves:
  ① trusted download source (GitHub Releases) + version pinning
  ② SHA256 gate: preset+match installs, mismatch/non-preset aborts+deletes
  ③ P0 needs human confirm; --auto only for preset+version-locked skills
"""
from __future__ import annotations

import builtins
import hashlib
import io
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import finder
import acquire

PASS = 0
FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def _zip_bytes(slug: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{slug}/SKILL.md",
                   "---\nname: %s\ndescription: x\n---\n" % slug)
    return buf.getvalue()


def _sha256(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- 修复①
def test_finder():
    url = finder.build_release_url("skill-radoute", "v1.5.0")
    check("release url is signed GitHub Releases",
          url == "https://github.com/aysmaa1978/skill-radoute"
                 "/releases/download/v1.5.0/skill-radoute-v1.5.0.skill.zip")
    # unknown slug -> refused (no untrusted CDN fallback)
    try:
        finder.build_release_url("ghost", "v1.0.0")
        check("unknown slug refused", False)
    except finder.FinderError:
        check("unknown slug refused", True)
    # no version -> refused (no dynamic `latest`)
    try:
        finder.build_release_url("skill-radoute", "")
        check("no version refused", False)
    except finder.FinderError:
        check("no version refused", True)
    # search returns a trusted, github-sourced candidate
    cands = finder.search("skill-radoute", version="v1.5.0")
    check("search returns 1 trusted candidate",
          len(cands) == 1 and cands[0]["source"] == "github")
    check("candidate download_url is GitHub Releases",
          cands[0]["download_url"].startswith("https://github.com/"))
    # unknown slug -> [] (no crash, caller can fall back to no_match)
    check("unknown slug search -> []", finder.search("ghost", version="v1.0.0") == [])


# ---------------------------------------------------------------- 修复②
def test_verify():
    data = _zip_bytes("demo")
    h = _sha256(data)
    with tempfile.TemporaryDirectory() as td:
        zp = Path(td) / "demo.zip"
        zp.write_bytes(data)
        # preset + matching hash -> passes, file kept
        acquire.KNOWN_SKILLS["demo"] = h
        try:
            acquire._verify_hash(zp, "demo")
            ok = True
        except Exception:
            ok = False
        check("preset + matching hash passes", ok and zp.exists())
        # mismatch -> error + file deleted
        acquire.KNOWN_SKILLS["demo"] = "sha256:deadbeef"
        raised = False
        msg = ""
        try:
            acquire._verify_hash(zp, "demo")
        except RuntimeError as e:
            raised = True
            msg = str(e)
        check("hash mismatch -> error + file deleted",
              raised and ("请联系作者更新" in msg) and (not zp.exists()))
        # non-preset -> 请联系作者更新 + file deleted
        zp2 = Path(td) / "other.zip"
        zp2.write_bytes(data)
        raised = False
        msg = ""
        try:
            acquire._verify_hash(zp2, "unlisted")
        except RuntimeError as e:
            raised = True
            msg = str(e)
        check("non-preset -> 请联系作者更新 + deleted",
              raised and ("请联系作者更新" in msg) and (not zp2.exists()))
    acquire.KNOWN_SKILLS.pop("demo", None)


# ------------------------------------------------ 修复②+③ 端到端 (mock 网络)
def test_download():
    data = _zip_bytes("demo")
    h = _sha256(data)
    acquire.KNOWN_SKILLS["demo"] = h

    class FakeResp:
        def __init__(self, b):
            self._b = b
            self._off = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            if n == -1:
                out = self._b[self._off:]
                self._off = len(self._b)
                return out
            out = self._b[self._off:self._off + n]
            self._off += n
            return out

    orig = acquire.urllib.request.urlopen
    acquire.urllib.request.urlopen = lambda req, timeout=0: FakeResp(data)
    with tempfile.TemporaryDirectory() as td:
        acquire.DL_ROOT = Path(td) / "dl"
        try:
            sel = {
                "slug": "demo",
                "download_url": "https://github.com/o/demo/releases/download/v1.0.0/demo.skill.zip",
                "version": "v1.0.0",
            }
            out = acquire._download(sel)
            check("preset download extracts skill dir w/ SKILL.md",
                  (out / "SKILL.md").exists())
        finally:
            acquire.urllib.request.urlopen = orig
            acquire.KNOWN_SKILLS.pop("demo", None)


# ---------------------------------------------------------------- 修复③
def test_confirm():
    class A:
        pass

    orig_trace = acquire.trace
    orig_set = acquire.st.set_step
    acquire.trace = lambda *a, **k: {}
    acquire.st.set_step = lambda *a, **k: None
    real_input = builtins.input
    try:
        a = A()
        a.auto = True
        a.force = False
        acquire.KNOWN_SKILLS["demo"] = "sha256:x"
        sel = {"slug": "demo", "version": "v1.0.0"}
        rep = {"verdict": "P2", "risk": "none"}
        check("auto + preset+pinned P2 -> approved",
              acquire._run_confirm({}, a, sel, rep) is True)
        # P0 under --auto -> rejected without prompting
        a2 = A()
        a2.auto = True
        a2.force = False
        check("P0 under --auto -> rejected",
              acquire._run_confirm({}, a2, {"slug": "x", "version": "v1.0.0"},
                                    {"verdict": "P0", "risk": "high"}) is False)
        # P0 + --auto + preset -> rejected（预置哈希不豁免 P0 人工确认）
        a3 = A()
        a3.auto = True
        a3.force = False
        check("P0 + --auto + preset -> rejected",
              acquire._run_confirm({}, a3, {"slug": "demo", "version": "v1.0.0"},
                                    {"verdict": "P0", "risk": "high"}) is False)
        # 非交互环境（stdin 关闭抛 EOFError）视为拒绝，不崩溃
        a4 = A()
        a4.auto = False
        a4.force = False
        def _eof(prompt=""):
            raise EOFError()
        builtins.input = _eof
        check("P0 + EOFError stdin -> rejected without crash",
              acquire._run_confirm({}, a4, {"slug": "demo", "version": "v1.0.0"},
                                    {"verdict": "P0", "risk": "high"}) is False)
        # non-preset under --auto -> degrades to manual; user says no -> rejected
        builtins.input = lambda p="": "n"
        check("auto + non-preset + user=n -> rejected",
              acquire._run_confirm({}, a, {"slug": "y", "version": "v1.0.0"}, rep) is False)
        # user says yes -> approved
        builtins.input = lambda p="": "y"
        check("auto + non-preset + user=y -> approved",
              acquire._run_confirm({}, a, {"slug": "y", "version": "v1.0.0"}, rep) is True)
        acquire.KNOWN_SKILLS.pop("demo", None)
    finally:
        acquire.trace = orig_trace
        acquire.st.set_step = orig_set
        builtins.input = real_input


if __name__ == "__main__":
    test_finder()
    test_verify()
    test_download()
    test_confirm()
    print(f"\nACQUIRE SECURITY TESTS: {PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)
