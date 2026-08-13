#!/usr/bin/env python3
"""v3.0 NLP 解析契约测试：动作词表 / parse_workflow / 准确率门槛。

纯标准库，无网络。覆盖：
  M1a intent 动作词表（20 个动作类型逐一识别）
  M1b workflow.parse_workflow（自然语言 -> 可运行模板，含依赖自动串接）
  准确率门槛：24 条自然语言用例，解析正确率 >= 80%（目标实测 ~100%）
用法：

    python3 scripts/test_workflow_nlp.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intent  # noqa: E402
import workflow  # noqa: E402

FAILED: list[str] = []
ACCURACY_TOTAL = 0
ACCURACY_HIT = 0


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}: got {got!r}, want {want!r}")
        FAILED.append(label)


def parsed_types(text: str) -> list:
    return [st["type"] for st in intent.parse(text)["sub_tasks"]]


# ------------------------------------------------- M1a: 动作词表（20 动作）
def test_action_vocab():
    samples = {
        "collect": "搜索AI新闻",
        "structure": "整理成表格",
        "analyze": "分析销售数据",
        "write": "撰写一篇文案",
        "visualize": "画一张流程图",
        "publish": "把内容发布出去",
        "code": "实现一个函数",
        "translate": "翻译成英文",
        "image": "生成一张海报",
        "video": "做一个短片",
        "plan": "规划项目里程碑",
        "brainstorm": "头脑风暴创意",
        "review": "审阅这份文件",
        "extract": "提取关键数据",
        "convert": "转成pdf格式",
        "qa": "什么是强化学习",
        "test": "验证登录功能",
        "debug": "调试这个报错",
        "download": "下载数据集",
        "install": "安装新技能",
    }
    check("action vocab has 20 types", len(intent.TYPE_PATTERNS), 20)
    for t, text in samples.items():
        got = parsed_types(text)
        check(f"action {t}: {text!r}", got, [t])


# ------------------------------------------- M1b: parse_workflow 模板生成
def test_parse_workflow():
    # 调研 -> 整理：structure 依赖 collect，input 自动串接
    t = workflow.parse_workflow("搜索AI进展并整理成要点")
    check("template name", t["name"], "research_workflow")
    check("two steps", len(t["steps"]), 2)
    s1, s2 = t["steps"]
    check("step1 skill tavily", s1["skill"], "tavily")
    check("step1 output", s1["output"], "collect.result")
    check("step2 skill summarize", s2["skill"], "summarize")
    check("step2 input wired from step1", s2["input"], "collect.result")
    check("step2 output", s2["output"], "structure.result")
    # 与 parse_template 同构：JSON 序列化后能原样解析（可 run / --save）
    t2 = workflow.parse_template(json.dumps(t, ensure_ascii=False))
    check("template round-trip steps", t2["steps"], t["steps"])
    # 无动作的任务 -> ValueError
    raised = False
    try:
        workflow.parse_workflow("你好")
    except ValueError:
        raised = True
    check("no-action text raises", raised, True)
    # 多动作（写+画图）生成两步骤，互不依赖不串 input
    t3 = workflow.parse_workflow("写文章并画架构图")
    check("multi-action 2 steps", len(t3["steps"]), 2)
    check("independent step2 no input", "input" not in t3["steps"][1], True)


# ----------------------------------------- 准确率门槛：24 条自然语言用例
CASES: list[tuple[str, list]] = [
    ("搜索最新AI进展并整理成要点", ["collect", "structure"]),
    ("调研一下然后写公众号文章", ["collect", "write"]),
    ("画一张系统架构图", ["visualize"]),
    ("把调研写成文章并发布到公众号", ["collect", "write", "publish"]),
    ("总结这份文档并对比两个方案", ["structure", "analyze"]),
    ("翻译这段文字成英文", ["translate"]),
    ("生成一张产品海报", ["image"]),
    ("做一个产品介绍视频", ["video"]),
    ("帮我规划一下项目里程碑", ["plan"]),
    ("头脑风暴几个创意点子", ["brainstorm"]),
    ("审阅并校对这份文件", ["review"]),
    ("从文档里提取关键数据", ["extract"]),
    ("把这份表格转成pdf", ["convert"]),
    ("回答一下什么是强化学习", ["qa"]),
    ("为登录模块进行验证", ["test"]),
    ("帮我调试这个报错", ["debug"]),
    ("下载最新的数据集", ["download"]),
    ("安装一个markdown技能", ["install"]),
    ("实现一个排序函数", ["code"]),
    ("查找客户资料并总结要点", ["collect", "structure"]),
    ("整理要点并生成流程图", ["structure", "visualize"]),
    ("分析销售数据并生成图表", ["analyze", "visualize"]),
    ("把这段内容翻译成英文再校对一遍", ["translate", "review"]),
    ("实现一个功能并测试一下", ["code", "test"]),
]


def test_accuracy():
    global ACCURACY_TOTAL, ACCURACY_HIT
    ACCURACY_TOTAL = len(CASES)
    for text, want in CASES:
        ACCURACY_TOTAL += 0
        got = parsed_types(text)
        if got == want:
            ACCURACY_HIT += 1
            print(f"  ok   ACC {text!r} -> {got}")
        else:
            print(f"  FAIL ACC {text!r}: got {got}, want {want}")
            FAILED.append(f"ACC {text}")
    rate = ACCURACY_HIT / max(1, len(CASES))
    print(f"  -> 准确率 {ACCURACY_HIT}/{len(CASES)} = {rate:.0%}")
    if rate < 0.8:
        FAILED.append(f"accuracy {rate:.2f} < 0.80")
        print("  FAIL 准确率低于 80% 门槛")
    else:
        print("  ok   准确率 >= 80%")


# ------------------------------------------- M2: build / from-text / --save
def test_yaml_roundtrip():
    tmpl = workflow.parse_workflow("搜索AI进展并整理成要点")
    y = workflow.yaml_dump(tmpl)
    t2 = workflow.parse_template(y)
    check("yaml dump round-trip name", t2["name"], tmpl["name"])
    check("yaml dump round-trip steps", t2["steps"], tmpl["steps"])


def test_save_template():
    import tempfile
    d = tempfile.mkdtemp(prefix="wf-save-")
    old = os.environ.get("SKILL_ROUTER_WORKFLOW_DIR")
    os.environ["SKILL_ROUTER_WORKFLOW_DIR"] = d
    try:
        tmpl = workflow.parse_workflow("搜索并整理AI进展", name="research-publish")
        p = workflow.save_template(tmpl)
        check("saved file exists", p.is_file(), True)
        check("saved filename", p.name, "research-publish.yaml")
        t2 = workflow.parse_template(p.read_text(encoding="utf-8"))
        check("saved template round-trip", t2["steps"], tmpl["steps"])
    finally:
        if old is None:
            os.environ.pop("SKILL_ROUTER_WORKFLOW_DIR", None)
        else:
            os.environ["SKILL_ROUTER_WORKFLOW_DIR"] = old
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_from_text_cli():
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    rc = workflow.cli_from_text("搜索AI进展并整理成要点")
    with redirect_stdout(buf):
        rc = workflow.cli_from_text("搜索AI进展并整理成要点")
    check("from-text rc=0", rc, 0)
    y = buf.getvalue()
    check("from-text prints name", 'name: "research_workflow"' in y, True)
    check("from-text prints steps", "- skill: tavily" in y, True)
    # 无动作 -> 非零
    import io as _io
    err = _io.StringIO()
    from contextlib import redirect_stderr
    with redirect_stderr(err):
        rc2 = workflow.cli_from_text("你好")
    check("from-text no-action rc=1", rc2, 1)


def test_build_interactive():
    import builtins
    import io
    from contextlib import redirect_stdout
    answers = iter(["调研发布", "tavily", "搜索AI进展", "", "research.raw",
                    "y", "summarize", "总结", "research.raw", "draft.summary",
                    "n"])
    real_input = builtins.input
    builtins.input = lambda *a: next(answers)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = workflow.cli_build()
    finally:
        builtins.input = real_input
    check("build rc=0", rc, 0)
    out = buf.getvalue()
    check("build yaml has 2 steps", out.count("- skill:") == 2, True)
    check("build step wiring",
          "input: research.raw" in out and "output: draft.summary" in out, True)


# ------------------------------------------- M4: 反馈接入 + 报错增强
def test_feedback_skill_pick():
    import tempfile
    import learning as L
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    fp = Path(path)
    orig = L.FEEDBACK_FILE
    L.FEEDBACK_FILE = fp
    try:
        # 无反馈：collect 用建议列表第一项 tavily
        t0 = workflow.parse_workflow("搜索AI新闻")
        check("no-feedback picks first suggestion",
              t0["steps"][0]["skill"], "tavily")
        # 反馈否决 tavily（相似任务）-> 回退到 web-search
        L.record_feedback("搜索AI新闻", ["tavily"], "web-search")
        t1 = workflow.parse_workflow("搜索AI新闻")
        check("excluded skill skipped",
              t1["steps"][0]["skill"], "web-search")
        # 反馈选中某个技能 -> 相似任务（相似度 > 0.8）优先采用
        L.record_feedback("搜索AI新闻", ["tavily"], "web-search")
        t2 = workflow.parse_workflow("帮我搜索AI新闻")
        check("chosen skill preferred", t2["steps"][0]["skill"], "web-search")
        # 与反馈任务不相似（<0.8）-> 反馈不生效，回退建议列表
        t3 = workflow.parse_workflow("搜索最新AI进展")
        check("far task falls back to suggestion", t3["steps"][0]["skill"], "tavily")
    finally:
        L.FEEDBACK_FILE = orig
        fp.unlink(missing_ok=True)


def test_error_hints():
    # load_template 找不到 -> 报错含「原因 + 解决」
    try:
        workflow.load_template("no-such-template-xyz")
        check("missing template raises", False)
    except FileNotFoundError as e:
        msg = str(e)
        check("missing template has 原因", "原因" in msg, True)
        check("missing template has 解决", "解决" in msg, True)
        check("missing template suggests from-text", "from-text" in msg, True)
    # parse_workflow 无动作 -> 报错含「原因 + 解决」
    try:
        workflow.parse_workflow("你好")
        check("no-action raises", False)
    except ValueError as e:
        msg = str(e)
        check("no-action has 原因", "原因" in msg, True)
        check("no-action has 解决", "解决" in msg, True)


# ------------------------------------------- 规则引擎报告 #R1 回归（v3.0.1）
def test_rule_engine_regressions():
    """规则引擎测试报告（v3.0.0）发现的 4 例单意图误拆，修复后必须恢复单意图。"""
    cases = [
        ("search the web for latest AI research papers", ["collect"]),
        ("design a minimalist poster with philosophy", ["image"]),
        ("plan a multi step implementation task", ["plan"]),
        ("find and install a skill from skillhub", ["collect"]),
    ]
    for text, want in cases:
        got = parsed_types(text)
        check(f"R1 single-intent {text!r}", got, want)
        check(f"R1 not multi {text!r}", len(got) >= 2, False)
    # 真多意图不受影响（成对动作词合并只针对获取技能语境）
    s = intent.parse("write a toutiao article and also draw a diagram for it")
    check("R1 real multi still detected", len(s["sub_tasks"]) >= 2, True)
    s2 = intent.parse("research online then write a wechat article")
    check("R1 research+write still detected", len(s2["sub_tasks"]) >= 2, True)


if __name__ == "__main__":
    test_action_vocab()
    test_parse_workflow()
    test_accuracy()
    test_yaml_roundtrip()
    test_save_template()
    test_from_text_cli()
    test_build_interactive()
    test_feedback_skill_pick()
    test_error_hints()
    test_rule_engine_regressions()
    print("\nWORKFLOW NLP TESTS: PASS" if not FAILED
          else f"\nWORKFLOW NLP TESTS: {len(FAILED)} FAILED -> {FAILED}")
    raise SystemExit(1 if FAILED else 0)
