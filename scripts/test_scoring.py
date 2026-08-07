#!/usr/bin/env python3
"""打分层不变量测试：CJK 归一化 / 词干门槛 / 停用词 n-gram 泄漏。

守住 v1.4 建立的三条不变量，纯标准库。用法：

    python3 scripts/test_scoring.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registry as R  # noqa: E402

FAILED: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}: got {got!r}, want {want!r}")
        FAILED.append(label)


def main() -> int:
    # ① 同语义中英查询的归一化分母必须一致，否则 CJK 白挨分数惩罚
    for cn, en in [("画架构图", "draw diagram"), ("写公众号文章", "write wechat article")]:
        check(f"mass parity {cn}~{en}", R.query_mass(cn), R.query_mass(en))

    # ② 词干前缀匹配只保留屈折变化，不得跨概念
    for q, d in [("debug", "debugging"), ("publish", "publisher"), ("test", "testing")]:
        check(f"stem keep {q}~{d}", R._stem_match(q, d), True)
    for q, d in [("data", "database"), ("auto", "automation"),
                 ("mark", "marketplace"), ("word", "wordpress")]:
        check(f"stem drop {q}~{d}", R._stem_match(q, d), False)

    # ③ 全功能字组成的 CJK n-gram 不得入池（曾让"帮我遛狗"匹配到海报技能）
    for q, leaked in [("帮我遛狗", "帮我"), ("我想学游泳", "我想"), ("今天天气怎么样", "怎么样")]:
        check(f"no stopword ngram {leaked!r} from {q}", leaked in R.tokenize(q), False)
    # 但真实内容词必须留下，别把过滤做成一刀切
    check("content ngram kept 架构", "架构" in R.tokenize("画一张架构图"), True)
    check("content ngram kept 头条", "头条" in R.tokenize("发一篇微头条"), True)

    # ④ 语义同义提升（v1.5）：同义词未词法命中时加分，且不相关技能不得被误抬
    def score_detail(query: str, desc: str):
        rec = {"name": "x", "dir_name": "x", "tier": "user",
               "description": desc, "tags": [], "display_name": ""}
        _s, _w, det = R.score_skill(R.tokenize(query), query.lower(), rec)
        return det

    # 检索 与 搜索 同组：技能描述只有"搜索"，查询用"检索"应获得语义加分
    d1 = score_detail("检索最新的AI新闻", "用搜索查找 AI 新闻")
    check("synonym lift 检索->搜索", d1["semantic_gain"] > 0, True)
    check("synonym matched term 检索", "检索" in d1["semantic_matched"], True)
    # 查询词已词法命中则不重复计分
    d2 = score_detail("搜索 AI 新闻", "用搜索查找 AI 新闻")
    check("no double count when lexical hit", d2["semantic_gain"], 0.0)
    # 不相关技能（画图）不得因"检索"得语义分
    d3 = score_detail("检索 AI 新闻", "用画图的方式展示数据")
    check("no false lift on unrelated", d3["semantic_gain"], 0.0)
    check("no false matched on unrelated", d3["semantic_matched"], [])

    print("\nSCORING INVARIANTS: PASS" if not FAILED
          else f"\nSCORING INVARIANTS: {len(FAILED)} FAILED -> {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
