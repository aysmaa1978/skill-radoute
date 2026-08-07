#!/usr/bin/env python3
"""call 生命周期冒烟测试：open / close / suspend / resume + 调用栈断言。

黑盒跑 CLI，覆盖 route 回归集碰不到的那条链路（d449b38 曾在此静默删掉出栈逻辑）。
纯标准库，无框架。用法：

    python3 scripts/test_call_chain.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROUTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "router.py")
HOME = tempfile.mkdtemp(prefix="router-smoke-")
ENV = {**os.environ, "SKILL_ROUTER_HOME": HOME}
FAILED: list[str] = []


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, ROUTER, *args],
                          capture_output=True, text=True, env=ENV)


def j(*args: str) -> dict:
    r = run(*args)
    assert r.returncode == 0, f"{args} exited {r.returncode}: {r.stderr.strip()[:300]}"
    return json.loads(r.stdout)


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}: got {got!r}, want {want!r}")
        FAILED.append(label)


def main() -> int:
    sid = j("session", "new", "--goal", "call chain smoke", "--mode", "auto")["session"]
    S = ("--session", sid)

    # open 两层，验证入栈与 parent 继承
    d = j(*S, "call", "open", "--skill", "demo-a", "--intent", "outer")
    check("open c001 id", d["call_id"], "c001")
    check("open c001 parent", d["parent"], None)
    d = j(*S, "call", "open", "--skill", "demo-b", "--intent", "inner")
    check("open c002 parent", d["parent"], "c001")
    check("open calls after 2 opens",
          [c["id"] for c in j(*S, "status")["open_calls"]], ["c001", "c002"])

    # switch --keep-open 挂起栈顶，current_skill 指向目标
    d = j(*S, "switch", "--to", "demo-c", "--kind", "handoff",
          "--reason", "smoke", "--keep-open")
    check("switch suspended call", d["suspended_call"], "c002")
    check("c002 suspended", {c["id"]: c["status"] for c in j(*S, "call", "list")}["c002"],
          "suspended")

    # resume 回到现场：c002 重新置顶且状态转回 open
    d = j(*S, "call", "resume", "c002")
    check("resume stack", d["stack"], ["c001", "c002"])
    check("resume current_skill", d["skill"], "demo-b")
    check("c002 reopened", {c["id"]: c["status"] for c in j(*S, "call", "list")}["c002"],
          "open")

    # close 内层：必须出栈，current_skill 回落到父调用（d449b38 回归点）
    d = j(*S, "call", "close", "--status", "ok", "--output", "k=v")
    check("close inner id", d["call_id"], "c002")
    check("close inner stack", d["stack"], ["c001"])
    check("close inner current_skill", d["current_skill"], "demo-a")
    check("output landed in ctx", j(*S, "ctx", "get", "k"), "v")

    # close 外层：栈清空，current_skill 归 None
    d = j(*S, "call", "close", "--status", "ok")
    check("close outer stack", d["stack"], [])
    check("close outer current_skill", d["current_skill"], None)

    # 无可关闭调用时应报错退出，而不是静默成功
    check("close with empty stack exits nonzero",
          run(*S, "call", "close", "--status", "ok").returncode != 0, True)

    print("\nCALL CHAIN SMOKE: PASS" if not FAILED
          else f"\nCALL CHAIN SMOKE: {len(FAILED)} FAILED -> {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
