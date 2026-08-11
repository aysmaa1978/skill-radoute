#!/usr/bin/env python3
"""v2.0 编排引擎契约测试：工作流 / 并行执行 / 动态加载。

纯标准库，无网络。断言：
  工作流：
    - YAML/JSON 模板解析（steps 的 skill/intent/input/output）
    - 串行执行 3 步并按顺序调用，上下文自动传递
    - 任一步失败 -> 回滚该步写入、保留前序结果、返回非零
    - resume 从失败断点续跑，不重复已完成步骤
  并行执行：
    - 无依赖子任务 parallelizable=True 且 groups 单层
    - 有依赖（调研->写作）parallelizable=False 且分层
    - run_parallel 总耗时约等于最慢子任务（而非之和）
  动态加载：
    - LRU 保留最近 5 个，超出淘汰最久未用
    - load_skill_meta 零磁盘（索引驻留）、unload_skill 手动卸载
用法：

    python3 scripts/test_workflow.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intent  # noqa: E402
import router  # noqa: E402
import workflow  # noqa: E402

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


# ---------------------------------------------------------------- 模板解析
TMPL_YAML = """name: "调研并发布"
steps:
  - skill: tavily
    intent: "搜索最新AI进展"
    output: research.raw
  - skill: summarize
    intent: "总结调研内容"
    input: research.raw
    output: draft.summary
"""

TMPL_JSON = json_str = (
    '{"name": "JSON工作流", "steps": ['
    '{"skill": "a", "intent": "x", "output": "k1"},'
    '{"skill": "b", "input": "k1", "output": "k2"}]}')


def test_template():
    t = workflow.parse_template(TMPL_YAML)
    check("yaml name", t["name"] == "调研并发布")
    check("yaml steps count", len(t["steps"]) == 2)
    check("yaml step fields",
          t["steps"][0] == {"skill": "tavily", "intent": "搜索最新AI进展",
                            "output": "research.raw"})
    check("yaml input wiring",
          t["steps"][1]["input"] == "research.raw")
    t2 = workflow.parse_template(TMPL_JSON)
    check("json template", t2["name"] == "JSON工作流"
          and t2["steps"][1]["input"] == "k1")
    try:
        workflow.parse_template("steps: []")
        check("empty steps rejected", False)
    except ValueError:
        check("empty steps rejected", True)
    try:
        workflow.parse_template("name: x\nsteps:\n  - intent: no-skill\n")
        check("missing skill rejected", False)
    except ValueError:
        check("missing skill rejected", True)


# ---------------------------------------------------------------- 工作流执行
class _Ctx:
    """模拟 context bus：槽位存 _push_version 的版本化结构，get 解包 value。"""

    def __init__(self):
        self.slots = {}

    def get(self, key):
        slot = self.slots.get(key)
        return slot.get("value") if slot else None

    def set(self, key, value):
        self.slots[key] = {"value": value}


def _patch_router(ctx: _Ctx, order: list):
    router.require_sid = lambda x=None: "T"
    router.load_ctx = lambda sid: {"slots": ctx.slots}
    router.save_ctx = lambda sid, c: ctx.__setattr__("slots", c["slots"])
    router.trace_append = lambda *a, **k: {"seq": 1}
    router.now_iso = lambda: "t"


def test_run():
    ctx = _Ctx()
    order: list = []
    _patch_router(ctx, order)
    tmpl = workflow.parse_template(TMPL_YAML)

    def exec_fn(step, ctx_in):
        order.append(step["skill"])
        return {"result": step["skill"] + "-done"}

    rc = workflow.run_workflow("T", tmpl, exec_fn=exec_fn)
    check("serial run rc=0", rc == 0)
    check("serial order", order == ["tavily", "summarize"])
    check("ctx output step1",
          ctx.get("research.raw") == {"result": "tavily-done"})
    check("ctx output step2",
          ctx.get("draft.summary") == {"result": "summarize-done"})


def test_fail_and_resume():
    ctx = _Ctx()
    order: list = []
    _patch_router(ctx, order)
    tmpl = workflow.parse_template(
        "name: fail\nsteps:\n"
        "  - skill: a\n    output: s1\n"
        "  - skill: b\n    input: s1\n    output: s2\n"
        "  - skill: c\n    input: s2\n    output: s3\n")
    calls = {"b": 0}

    def exec_fn(step, ctx_in):
        order.append(step["skill"])
        if step["skill"] == "b":
            calls["b"] += 1
            if calls["b"] == 1:
                raise RuntimeError("boom")
        return {"result": step["skill"]}

    wf_state = Path(tempfile.mkdtemp(prefix="wf-")) / "state.json"
    workflow.STATE_FILE = wf_state
    rc = workflow.run_workflow("T", tmpl, exec_fn=exec_fn)
    check("fail rc=1", rc == 1)
    check("fail order stops at b", order == ["a", "b"])
    check("fail rollback s2 absent", ctx.get("s2") is None)
    check("fail keeps s1", ctx.get("s1") == {"result": "a"})
    state = workflow._load_state()
    check("state.failed=1", state and state.get("failed") == 1)
    check("state.completed=[0]", state and state.get("completed") == [0])
    # resume：从 failed=1 续跑，不重复 step a
    rc2 = workflow.run_workflow("T", tmpl, exec_fn=exec_fn, start=state["failed"])
    check("resume rc=0", rc2 == 0)
    check("resume order b(2nd),c", order == ["a", "b", "b", "c"])
    check("resume fills s2,s3",
          ctx.get("s2") == {"result": "b"} and ctx.get("s3") == {"result": "c"})
    check("state cleared after done", workflow._load_state() is None)


# ---------------------------------------------------------------- 并行执行
def test_parallel_detection():
    s1 = intent.parse("write a toutiao article and also draw a diagram")
    check("independent parallelizable", s1["parallelizable"] is True)
    check("independent single group", s1["parallel_groups"] == [["write", "visualize"]])
    s2 = intent.parse("research online then write a wechat article")
    check("dependent not parallelizable", s2["parallelizable"] is False)
    check("dependent layered groups", s2["parallel_groups"] == [["collect"], ["write"]])


def test_parallel_exec():
    def slow(name, t):
        return (name, lambda: (time.sleep(t), name)[1])

    t0 = time.perf_counter()
    res = router.run_parallel([slow("write", 0.25), slow("draw", 0.08)])
    wall = time.perf_counter() - t0
    check("wall ~ max not sum", wall < 0.38 and wall >= 0.22)
    check("results ordered", [r[0] for r in res] == ["write", "draw"])
    check("all ok", all(r[1] for r in res))
    # 异常隔离：一个失败不阻断另一个
    def boom():
        raise ValueError("x")
    res2 = router.run_parallel([("good", lambda: "ok"), ("bad", boom)])
    check("exception isolated",
          dict((r[0], r[1]) for r in res2) == {"good": True, "bad": False})


# ---------------------------------------------------------------- 动态加载
def test_dynamic_load():
    import registry
    tmp = Path(tempfile.mkdtemp(prefix="dl-"))
    skills = tmp / "skills"
    for name in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
        d = skills / name
        (d / "scripts").mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} does things\n---\n",
            encoding="utf-8")
        (d / "scripts" / "main.py").write_text(f"# {name}\n", encoding="utf-8")
    os.environ["SKILL_ROUTER_REGISTRY_CACHE"] = str(tmp / "rc.json")
    os.environ["SKILL_ROUTER_HOME"] = str(tmp / "router")
    # 复位内存驻留与指纹 TTL 缓存，避免前面用例（真实根目录扫描）污染本用例
    registry._MEM_INDEX = None
    registry._MEM_FP = ""
    registry._MEM_TS = 0.0
    registry._FP_CACHE = ""
    registry._FP_CACHE_TS = 0.0
    orig = registry.discover_roots
    registry.discover_roots = lambda: [("user", skills)]
    try:
        registry.get_index()
        for n in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
            p = registry.load_skill_full(n)
            check(f"load {n} w/ scripts", p is not None
                  and p["scripts"][0]["file"] == "main.py")
        loaded = [i["name"] for i in registry.cache_stats()["loaded"]]
        check("LRU keeps 5", len(loaded) == 5)
        check("oldest evicted", "alpha" not in loaded and "zeta" in loaded)
        registry.load_skill_full("alpha")   # 重新访问 -> beta 淘汰
        loaded2 = [i["name"] for i in registry.cache_stats()["loaded"]]
        check("re-access alpha evicts beta",
              "alpha" in loaded2 and "beta" not in loaded2)
        check("meta zero-disk", registry.load_skill_meta("beta")["name"] == "beta")
        check("manual unload", registry.unload_skill("alpha") is True
              and "alpha" not in [i["name"] for i in registry.cache_stats()["loaded"]])
    finally:
        registry.discover_roots = orig


if __name__ == "__main__":
    test_template()
    test_run()
    test_fail_and_resume()
    test_parallel_detection()
    test_parallel_exec()
    test_dynamic_load()
    print(f"\nV2.0 ORCHESTRATION TESTS: {PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)
