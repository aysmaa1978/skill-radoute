#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route --explain 输出契约测试（v1.5）。

隔离真实注册表，直接驱动 cmd_route，断言：
  - explain 路径含 top_candidates / score_breakdown / decision_reason
  - confirm 决策额外含 missing_trigger
  - 非 explain 路径输出字段与原先完全一致，不含任何 explain-only key
用法：

    python3 scripts/test_explain.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import router  # noqa: E402

router.require_sid = lambda x: "T"
router.load_session = lambda x: {"mode": "auto"}
router.trace_append = lambda *a, **k: {"seq": 0}
CAP: list = []
router.emit = lambda d: CAP.append(d)


def _cand(name: str, score: float) -> dict:
    return {
        "name": name, "tier": "user", "score": score,
        "why": [f"命中: {name}"], "path": f"/{name}",
        "skill_md": f"/{name}/SKILL.md", "description": f"do {name}",
        "score_breakdown": {
            "name_score": 1.0, "desc_score": 0.0, "tag_score": 0.0,
            "name_in_query_bonus": 0.0, "semantic_matched": [],
            "semantic_gain": 0.0, "raw": 1.0, "norm": 0.3,
            "tier_weight": 1.0, "final": round(score, 4),
        },
    }


FAKE_AUTO = [_cand("alpha", 2.0), _cand("beta", 1.0)]
FAKE_CONFIRM = [_cand("alpha", 1.5), _cand("beta", 1.4)]


def _fake(search_impl):
    def _s(task, top=5, source=None, reg=None, with_detail=False):
        cands = (search_impl()[:top]) if with_detail else [
            {k: v for k, v in c.items() if k != "score_breakdown"}
            for c in search_impl()[:top]
        ]
        return cands
    router.registry.search = _s


FAILED: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        FAILED.append(label)


def run(explain: bool) -> dict:
    CAP.clear()
    a = __import__("types").SimpleNamespace(
        task="do alpha and beta", session="T", mode="auto", top=5,
        threshold=router.AUTO_THRESHOLD, margin=router.AUTO_MARGIN,
        guard=False, exclude=None, explain=explain)
    router.cmd_route(a)
    return CAP[0]


def main() -> int:
    # --- explain 路径：auto 决策 ---
    _fake(lambda: FAKE_AUTO)
    d = run(True)
    check("explain has top_candidates", "top_candidates" in d, True)
    check("explain has decision_reason", "decision_reason" in d, True)
    check("top_candidates count == 2", len(d["top_candidates"]), 2)
    check("cand0 has score_breakdown", "score_breakdown" in d["top_candidates"][0], True)
    check("score_breakdown keys",
          set(d["top_candidates"][0]["score_breakdown"].keys()),
          {"name_score", "desc_score", "tag_score", "name_in_query_bonus",
           "semantic_matched", "semantic_gain", "raw", "norm",
           "tier_weight", "final"})
    check("explain decision == auto", d["decision"], "auto")
    check("auto has no missing_trigger", "missing_trigger" in d, False)
    check("explain chosen == alpha", d["chosen"], "alpha")

    # --- explain 路径：confirm 决策应含 missing_trigger ---
    _fake(lambda: FAKE_CONFIRM)
    d2 = run(True)
    check("explain decision == confirm", d2["decision"], "confirm")
    check("confirm has missing_trigger", "missing_trigger" in d2, True)
    check("missing_trigger non-empty", bool(d2["missing_trigger"]), True)

    # --- 非 explain 路径：字段与原先一致，不得含 explain-only key ---
    d3 = run(False)
    explain_only = {"top_candidates", "decision_reason", "missing_trigger"}
    check("non-explain no explain-only keys", explain_only.isdisjoint(d3.keys()), True)
    check("non-explain keeps legacy candidates", "candidates" in d3, True)
    check("non-explain no score_breakdown", "score_breakdown" not in d3, True)

    print("\nEXPLAIN CONTRACT: PASS" if not FAILED
          else f"\nEXPLAIN CONTRACT: {len(FAILED)} FAILED -> {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
