#!/usr/bin/env python3
"""intent.py - rule-based intent parser for skill-radoute.

把自然语言任务描述转成结构化任务规格，供 router.route 作为增强输入消费。
纯标准库，不依赖 LLM。识别方式：关键词组合 + 正则（不依赖外部 NLP）。

设计取舍：
- 不引入任何第三方依赖，规则库内置于本文件，便于单测与一眼审阅。
- 只抽取「任务类型」与「领域」，不臆造目标细节；target 用模板短语，
  稳定可消费，后续模块若需要真实名词可在此基础上接 LLM（v1.2 P2）。
"""

import argparse
import json
import re
import sys

# 任务类型关键词（小写匹配，中文原样）
TYPE_PATTERNS = {
    "collect": ["搜索", "查找", "查一下", "调研", "研究", "收集", "搜集", "找资料", "资料", "信息", "素材", "search", "find", "research", "lookup", "fetch"],
    "structure": ["整理", "总结", "归纳", "梳理", "提炼", "结构化", "概括", "summarize", "organize", "outline"],
    "analyze": ["分析", "对比", "评估", "诊断", "analyze", "compare", "evaluate", "diagnose"],
    "write": ["写", "起草", "撰写", "文章", "稿子", "文案", "报告", "头条", "微头条", "邮件", "write", "draft", "compose", "article", "blog", "report"],
    "visualize": ["画", "架构图", "流程图", "示意图", "配图", "可视化", "图表", "脑图", "diagram", "draw", "chart", "visualize", "graph", "mindmap"],
    "publish": ["发布", "推送", "发表", "上线", "发头条", "发文章", "publish", "post", "deploy", "ship"],
    "code": ["代码", "写代码", "脚本", "实现", "开发", "函数", "code", "script", "implement", "function", "program"],
    "translate": ["翻译", "译", "translate", "localize"],
    "image": ["图片", "图像", "海报", "生图", "配图生成", "image", "poster", "picture", "photo"],
    "video": ["视频", "短片", "动画", "video", "clip", "animation"],
    # --- v3.0: 动作词表扩展（20+ 动作）---
    "plan": ["规划", "计划", "排期", "里程碑", "路线图", "plan", "schedule", "roadmap", "milestone"],
    "brainstorm": ["头脑风暴", "创意", "点子", "灵感", "brainstorm", "ideate", "idea"],
    "review": ["审查", "审阅", "校对", "复查", "润色", "review", "proofread", "audit"],
    "extract": ["提取", "抽取", "摘录", "提炼要点", "extract"],
    "convert": ["转换", "格式转换", "转成", "转pdf", "convert", "transform"],
    "qa": ["问答", "答疑", "解释", "解答", "回答", "什么是", "qa", "explain", "answer"],
    "test": ["测试", "验证", "自测", "test", "verify", "validate"],
    "debug": ["调试", "修复bug", "排错", "debug", "troubleshoot"],
    "download": ["下载", "download"],
    "install": ["安装", "install", "setup"],
}

# 领域关键词
DOMAIN_PATTERNS = {
    "AI_Agent": ["ai", "agent", "智能体", "大模型", "llm", "prompt", "多模态", "agentic", "gpt", "agent 系统"],
    "web": ["网页", "网站", "爬虫", "web", "crawl", "scrape", "http"],
    "data": ["数据", "报表", "excel", "csv", "数据库", "data", "dataset", "analytics", "统计"],
    "image": ["海报", "设计", "图片", "image", "poster", "design", "ui"],
    "video": ["视频", "video", "短片"],
    "doc": ["文档", "word", "ppt", "pdf", "幻灯片", "doc", "presentation"],
}
DOMAIN_DEFAULT = "general"

# 任务类型 -> 候选技能 slug 提示（仅作建议，不承诺已安装）
TYPE_SKILLS = {
    "collect": ["tavily", "web-search"],
    "structure": ["summarize"],
    "analyze": ["summarize"],
    "write": ["wechat-publisher", "toutiao-publish", "writing-plans"],
    "visualize": ["drawio-skill", "mermaid"],
    "publish": ["wechat-publisher", "toutiao-publish"],
    "code": ["fullstack-dev", "vibe-coding"],
    "translate": ["translate"],
    "image": ["image-gen", "poster"],
    "video": ["video-gen"],
    # --- v3.0 新动作的建议技能 ---
    "plan": ["project-planner"],
    "brainstorm": ["ideation"],
    "review": ["review-skill"],
    "extract": ["summarize"],
    "convert": ["convert-skill"],
    "qa": ["assistant-chat"],
    "test": ["fullstack-dev"],
    "debug": ["fullstack-dev"],
    "download": ["web-search"],
    "install": ["acquire"],
}

# 类型顺序（用于稳定输出与意图标签）
TYPE_ORDER = ["collect", "structure", "analyze", "write", "visualize", "publish", "code", "translate", "image", "video", "plan", "brainstorm", "review", "extract", "convert", "qa", "test", "debug", "download", "install"]

# v2.0: 子任务依赖图（type -> 必须先完成的 type）。用于判定多子任务能否并行：
# 存在依赖边（如 调研->写作）必须串行；无依赖边（如 写作+画图）可并行。
DEPENDS_ON: dict[str, tuple[str, ...]] = {
    "collect": (),
    "structure": ("collect",),
    "analyze": ("collect",),
    "write": ("collect", "structure"),
    "publish": ("write",),
    "visualize": (),
    "image": (),
    "video": (),
    "code": (),
    "translate": (),
    # --- v3.0 新动作的依赖 ---
    "plan": (),
    "brainstorm": (),
    "review": ("write",),
    "extract": ("collect",),
    "convert": (),
    "qa": (),
    "test": ("code",),
    "debug": ("code",),
    "download": (),
    "install": (),
}


def _parallel_groups(types: list[str]) -> tuple[bool, list[list[str]]]:
    """拓扑分层：同一层的子任务互不依赖（可并行），层与层之间必须串行。

    返回 (parallelizable, groups)：
      - 单层（全部互不依赖）-> parallelizable=True，groups=[[全部]]
      - 多层（存在依赖链）  -> parallelizable=False，groups 为分层执行计划
    """
    groups: list[list[str]] = []
    remaining = list(types)
    while remaining:
        layer = [t for t in remaining
                 if not any(d in remaining for d in DEPENDS_ON.get(t, ()))]
        if not layer:          # 环路兜底（正常不会发生）
            layer, remaining = remaining, []
        groups.append(layer)
        for t in layer:
            remaining.remove(t)
    return len(groups) == 1, groups

# 模板化 target（稳定、可消费）
TEMPLATE_TARGET = {
    "collect": "收集相关资料",
    "structure": "整理成结构化内容",
    "analyze": "分析数据/内容",
    "write": "撰写文稿",
    "visualize": "绘制可视化图（架构图/流程图）",
    "publish": "发布到目标平台",
    "code": "编写代码/脚本",
    "translate": "翻译内容",
    "image": "生成图像",
    "video": "生成视频",
    # --- v3.0 新动作的模板 target ---
    "plan": "制定计划/排期",
    "brainstorm": "产出创意方案",
    "review": "审查/校对内容",
    "extract": "提取关键信息",
    "convert": "格式/内容转换",
    "qa": "问答/解释说明",
    "test": "测试与验证",
    "debug": "调试与修复",
    "download": "下载资源",
    "install": "安装/配置",
}


_KW_CACHE: dict = {}


def _kw_matches(kw: str, low: str) -> bool:
    """关键词匹配：ASCII 词用整词（词边界）匹配，中文用子串匹配。

    v3.0.1 修复（规则引擎报告 #R1）：英文关键词此前按子串匹配，导致
    "test" 命中 "latest"、"post" 命中 "poster"、"implement" 命中
    "implementation" 等假阳性，单意图查询被误拆为多意图。
    """
    if not kw:
        return False
    if kw.isascii():
        rx = _KW_CACHE.get(kw)
        if rx is None:
            rx = re.compile(rf"(?<![a-z0-9]){re.escape(kw.lower())}(?![a-z0-9])")
            _KW_CACHE[kw] = rx
        return rx.search(low) is not None
    return kw.lower() in low


def _matched_types(text: str):
    low = text.lower()
    hits = []
    for t, kws in TYPE_PATTERNS.items():
        for kw in kws:
            if _kw_matches(kw, low):
                hits.append(t)
                break
    return [t for t in TYPE_ORDER if t in hits]


def _domain(text: str):
    low = text.lower()
    for d, kws in DOMAIN_PATTERNS.items():
        if any(kw.lower() in low for kw in kws):
            return d
    return DOMAIN_DEFAULT


def _intent_label(types: list):
    key = "+".join(types)
    table = {
        "collect+structure+visualize": "research_and_visualize",
        "collect+structure+write": "research_and_write",
        "collect+write+publish": "research_write_publish",
        "analyze+visualize": "analyze_visualize",
        "collect+structure": "research",
        "write+publish": "write_publish",
        "visualize": "visualize",
        "code": "implement",
        "translate": "translate",
        # v3.0 新增组合标签
        "collect+structure+write+publish": "research_write_publish",
        "code+test": "implement_and_test",
        "write+review": "write_review",
        "collect+extract+structure": "research",
        "plan+write": "plan_and_write",
    }
    if key in table:
        return table[key]
    if "collect" in types and "visualize" in types:
        return "research_and_visualize"
    if "collect" in types and "write" in types:
        return "research_and_write"
    if types:
        return types[0]
    return "general"


def parse(text: str) -> dict:
    """解析自然语言为结构化规格。v2.0 起附带 parallelizable / parallel_groups：
    多子任务时按依赖图分层，无依赖即可并行（供 route --parallel 与工作流引擎消费）。

    v3.0.1 修复（规则引擎报告 #R1）：
      - 英文关键词整词匹配（消除 test/latest、post/poster、implement/implementation 子串假阳性）
      - 「find/install/下载/安装 + 技能/skill」视为单一获取目标（成对动作词合并），
        不再误拆为多意图。
    """
    types = _matched_types(text)
    # 成对动作词合并：查找/安装一个技能 = 单一获取目标（acquire）
    if set(types) == {"collect", "install"} and re.search(
            r"技能|skill|skillhub|插件|plugin", text, re.IGNORECASE):
        types = ["collect"]
    sub_tasks = [{"type": t, "target": TEMPLATE_TARGET[t]} for t in types]
    domain = _domain(text)
    suggested = []
    for t in types:
        for s in TYPE_SKILLS.get(t, []):
            if s not in suggested:
                suggested.append(s)
    parallelizable, parallel_groups = _parallel_groups(types)
    return {
        "intent": _intent_label(types),
        "domain": domain,
        "sub_tasks": sub_tasks,
        "suggested_skills": suggested,
        "parallelizable": parallelizable,
        "parallel_groups": parallel_groups,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="intent.py", description="规则引擎：自然语言 -> 结构化任务")
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("parse", help="解析一段任务描述")
    pp.add_argument("text", help="自然语言任务文本")
    pp.add_argument("--json", action="store_true", help="美化 JSON 输出")
    args = p.parse_args(argv)
    if args.cmd == "parse":
        r = parse(args.text)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(r, ensure_ascii=False))
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
