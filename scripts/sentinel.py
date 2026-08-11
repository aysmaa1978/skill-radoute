#!/usr/bin/env python3
"""sentinel.py - pre-route boundary guard for skill-radoute.

在 router.route 前检查任务是否越界：安全边界 / 能力边界 / 资源边界。
纯标准库。安全边界默认常开（proceed=false 直接拒绝路由）；能力与资源边界
为预警（proceed 仍为 True，但带 warnings）。黑名单可配置于
~/.workbuddy/sentinel_rules.json（环境变量 SKILL_ROUTER_SENTINEL_RULES 可覆盖）。

设计取舍：
- 安全拦截是硬阻断，关键词黑名单 + 简单语义判断，宁可多报不漏报。
- 能力/资源检查是软预警：本地技能覆盖不足或 API Key 缺失时仍允许路由，
  由人工/后续环节决策，避免在合法任务上误杀。
"""

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_RULES = {
    "security_blacklist": [
        "黑掉", "黑客", "攻击", "入侵", "破解密码", "撞库", "拖库", "提权",
        "webshell", "后门", "ddos", "sql注入", "xss", "暴力破解", "钓鱼",
        "hack", "attack", "exploit", "intrusion", "brute force", "ddos",
        "sql injection", "xss", "malware", "ransomware", "phishing", "botnet",
    ],
    "capability_min_coverage": 0.8,
}

# 任务类型 -> 本地技能名应包含的关键词（用于能力覆盖判定）
TYPE_COVER = {
    "collect": ["tavily", "search", "web"],
    "structure": ["summar", "organ"],
    "analyze": ["summar", "analy"],
    "write": ["publish", "write", "toutiao", "wechat"],
    "visualize": ["drawio", "diagram", "mermaid", "chart"],
    "publish": ["publish", "toutiao", "wechat"],
    "code": ["dev", "code", "vibe"],
    "translate": ["translat"],
    "image": ["image", "poster", "gen"],
    "video": ["video", "gen"],
}

# 资源需求探测
RESOURCE_NEEDS = [
    (["tavily", "搜索", "search"], "TAVILY_API_KEY"),
    (["生图", "海报", "image", "poster"], "IMAGE_GEN_API_KEY"),
    (["视频", "video", "短片"], "VIDEO_GEN_API_KEY"),
]

RULES_PATH = Path(os.environ.get(
    "SKILL_ROUTER_SENTINEL_RULES",
    os.path.expanduser("~/.workbuddy/sentinel_rules.json"),
))


def load_rules() -> dict:
    if RULES_PATH.exists():
        try:
            data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_RULES)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_RULES)


def _security_blocked(text: str, rules: dict):
    low = text.lower()
    for kw in rules.get("security_blacklist", []):
        if kw.lower() in low:
            return kw
    return None


def _capability(sub_tasks, available_skills):
    """返回 (coverage: float|None, uncovered: list)。无子任务/无技能列表则跳过。"""
    if not sub_tasks or not available_skills:
        return None, []
    av = [s.lower() for s in available_skills]
    uncovered = []
    for st in sub_tasks:
        t = st.get("type")
        keys = TYPE_COVER.get(t, [])
        if not any(k in a for k in keys for a in av):
            uncovered.append(t)
    coverage = (len(sub_tasks) - len(uncovered)) / len(sub_tasks)
    return coverage, uncovered


def _resources(text: str, sub_tasks, env):
    missing = []
    hay = text.lower()
    types = {st.get("type") for st in (sub_tasks or [])}
    for kws, env_key in RESOURCE_NEEDS:
        if any(k.lower() in hay for k in kws) or any(kw in types for kw in kws):
            if not env.get(env_key):
                missing.append(env_key)
    return missing


def check(text: str, sub_tasks=None, available_skills=None, env=None, rules=None) -> dict:
    rules = rules or load_rules()
    env = env if env is not None else os.environ

    # 1. 安全边界（硬阻断）
    hit = _security_blocked(text, rules)
    if hit:
        return {
            "proceed": False,
            "reason": "security_policy_violation",
            "matched": hit,
            "suggestion": ("该任务超出安全边界。建议：1) 仅审计或测试你自己拥有"
                           "书面授权的系统；2) 检索公开的漏洞报告与防御方案，而非发起攻击。"),
        }

    # 2. 能力边界（软预警）
    warnings = []
    coverage, uncovered = _capability(sub_tasks, available_skills)
    if coverage is not None:
        min_cov = rules.get("capability_min_coverage", 0.8)
        if coverage < min_cov:
            warnings.append(
                f"本地技能仅覆盖 {coverage:.0%} 的子任务（<{min_cov:.0%}）："
                f"未覆盖 {', '.join(uncovered)}，建议走远程获取补齐")

    # 3. 资源边界（软预警）
    missing = _resources(text, sub_tasks, env)
    if missing:
        warnings.append("缺少环境变量：" + ", ".join(missing) + "（相关技能可能无法运行）")

    return {"proceed": True, "reason": "ok", "warnings": warnings,
            "capability_coverage": coverage}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sentinel.py", description="路由前边界哨兵")
    sub = p.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("check", help="检查一段任务是否越界")
    cp.add_argument("text", help="自然语言任务文本")
    cp.add_argument("--subtasks", help="JSON 字符串：intent.sub_tasks，用于能力覆盖判定")
    cp.add_argument("--skills", help="逗号分隔的本地技能名列表，用于能力覆盖判定")
    cp.add_argument("--json", action="store_true", help="美化 JSON 输出")
    args = p.parse_args(argv)
    if args.cmd == "check":
        sub_tasks = None
        if args.subtasks:
            try:
                sub_tasks = json.loads(args.subtasks)
            except Exception as e:
                print(json.dumps({"error": f"subtasks 解析失败: {e}"}, ensure_ascii=False))
                return 1
        skills = [s.strip() for s in args.skills.split(",")] if args.skills else None
        r = check(args.text, sub_tasks=sub_tasks, available_skills=skills)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(r, ensure_ascii=False))
        return 0 if r["proceed"] else 2
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
