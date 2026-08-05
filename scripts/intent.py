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
}

# 类型顺序（用于稳定输出与意图标签）
TYPE_ORDER = ["collect", "structure", "analyze", "write", "visualize", "publish", "code", "translate", "image", "video"]

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
}


def _matched_types(text: str):
    low = text.lower()
    hits = []
    for t, kws in TYPE_PATTERNS.items():
        for kw in kws:
            if kw.lower() in low:
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
    """解析自然语言任务为结构化规格。"""
    types = _matched_types(text)
    sub_tasks = [{"type": t, "target": TEMPLATE_TARGET[t]} for t in types]
    domain = _domain(text)
    suggested = []
    for t in types:
        for s in TYPE_SKILLS.get(t, []):
            if s not in suggested:
                suggested.append(s)
    return {
        "intent": _intent_label(types),
        "domain": domain,
        "sub_tasks": sub_tasks,
        "suggested_skills": suggested,
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
