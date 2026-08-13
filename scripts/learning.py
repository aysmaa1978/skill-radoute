#!/usr/bin/env python3
"""v2.1 路由反馈学习：本地记录「该任务不该用哪些技能、最后选了谁」。

数据只存在本机 `~/.workbuddy/feedback.json`，绝不外发；用途仅为优化后续
路由打分（registry.score_skill 的反馈加权），用户可随时用
`router.py feedback clear` 清空。纯标准库，零依赖。

数据模型（version=1）::

    {"version": 1, "entries": [
        {"task": "...", "excluded": ["a", "b"], "chosen": "c",
         "timestamp": 1730000000.0, "weight": 1.0},
    ]}

- `task`      触发反馈的原始任务描述（路由时的 query）
- `excluded`  用户否决过的技能名列表
- `chosen`    最终选中的技能名
- `timestamp` 记录时刻（Unix 秒）
- `weight`    影响系数：1.0 全量生效，0.5 减半（供按时间衰减使用）
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

FEEDBACK_VERSION = 1
FEEDBACK_FILE = Path(os.environ.get(
    "SKILL_RADOUTE_FEEDBACK",
    str(Path.home() / ".workbuddy" / "feedback.json")))

SIMILARITY_THRESHOLD = 0.8  # v2.1: 任务相似度超过该阈值才应用反馈加权


def _empty() -> dict:
    return {"version": FEEDBACK_VERSION, "entries": []}


def _load() -> dict:
    """读取反馈文件；缺失/损坏时返回空结构（绝不抛错中断路由）。"""
    try:
        if not FEEDBACK_FILE.is_file():
            return _empty()
        data = json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            return _empty()
        return data
    except Exception:
        return _empty()


def _save(data: dict) -> None:
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FEEDBACK_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(FEEDBACK_FILE)


def _norm(text: str) -> str:
    """归一化：转小写、去掉所有空白，便于相似度比较。"""
    return re.sub(r"\s+", "", str(text or "")).lower()


def similarity(a: str, b: str) -> float:
    """任务文本相似度（0.0~1.0）：difflib 字符序列比值，纯标准库。

    对中文按字符序列比较即可捕捉「同一任务的多种说法」。
    """
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def record_feedback(task: str, excluded: list, chosen: str,
                    weight: float = 1.0) -> dict:
    """记录一条反馈并落盘，返回新条目。自动去重同 task 同 chosen 的旧条目
    （只保留最新一条），避免同类反馈反复叠加权重。"""
    entry = {
        "task": str(task or "").strip(),
        "excluded": [str(x) for x in (excluded or [])],
        "chosen": str(chosen or "").strip(),
        "timestamp": time.time(),
        "weight": float(weight),
    }
    data = _load()
    entries = data.setdefault("entries", [])
    entries[:] = [
        e for e in entries
        if not (e.get("task") == entry["task"] and e.get("chosen") == entry["chosen"])
    ]
    entries.append(entry)
    _save(data)
    return entry


def get_feedback(task: str, threshold: float = SIMILARITY_THRESHOLD) -> list[dict]:
    """返回与 `task` 相似度 > threshold 的反馈条目（按相似度降序）。"""
    out = []
    for e in _load().get("entries", []):
        sim = similarity(task, e.get("task", ""))
        if sim > threshold:
            out.append((sim, e))
    out.sort(key=lambda x: -x[0])
    return [e for _, e in out]


def all_feedback() -> list[dict]:
    """返回全部反馈条目（供 registry 打分加权使用）。"""
    return _load().get("entries", [])


def list_all() -> list[dict]:
    return all_feedback()


def clear() -> int:
    """清空所有反馈，返回被清空的条目数。"""
    n = len(_load().get("entries", []))
    _save(_empty())
    return n


def stats() -> dict:
    """统计：总记录数 + 覆盖任务数。"""
    entries = _load().get("entries", [])
    return {"entries": len(entries), "tasks": len({e.get("task") for e in entries})}


def feedback_fingerprint() -> str:
    """反馈数据指纹（文件 mtime+size），供路由决策缓存 key 使用：
    反馈变化后旧缓存自动失效，不会给出过期权重。"""
    try:
        st = FEEDBACK_FILE.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "0:0"


if __name__ == "__main__":
    print(f"feedback file: {FEEDBACK_FILE}")
    print(f"stats: {stats()}")
