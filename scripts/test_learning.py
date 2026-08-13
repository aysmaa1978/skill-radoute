#!/usr/bin/env python3
"""v2.1 契约测试：反馈学习（记录/查询/加权）+ 镜像源切换。

纯标准库，无网络，无框架。覆盖：
  B1 learning: record_feedback 落盘 / get_feedback 相似匹配 / clear / stats
  B2 registry : score_skill 反馈加权（chosen +1.5、excluded -2.0、weight 减半、
                相似度阈值、无反馈时行为不变）
  B3 router  : feedback list|stats|clear 黑盒 CLI
  A1 finder  : mirrors() 环境变量覆盖 + download() 镜像切换（mock 网络）
用法：

    python3 scripts/test_learning.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import finder  # noqa: E402
import learning  # noqa: E402
import registry as R  # noqa: E402

FAILED: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}: got {got!r}, want {want!r}")
        FAILED.append(label)


def _tmp_file() -> Path:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    return Path(path)


# ---------------------------------------------------------------- B1 learning
def test_learning():
    fp = _tmp_file()
    orig = learning.FEEDBACK_FILE
    learning.FEEDBACK_FILE = fp
    try:
        # 初始为空
        check("initial stats empty",
              learning.stats(), {"entries": 0, "tasks": 0})
        # 记录一条
        e = learning.record_feedback(
            "帮我写公众号文章并配图", ["drawio-skill", "poster"], "wechat-publisher")
        check("record returns entry",
              (e["task"], e["chosen"], e["weight"]),
              ("帮我写公众号文章并配图", "wechat-publisher", 1.0))
        check("record persisted to file", fp.is_file(), True)
        check("stats after 1 record",
              learning.stats(), {"entries": 1, "tasks": 1})
        # 同 task + chosen 重复记录 -> 去重（只保留最新）
        learning.record_feedback("帮我写公众号文章并配图",
                                 ["drawio-skill"], "wechat-publisher", weight=0.5)
        check("dedupe same task+chosen",
              learning.stats(), {"entries": 1, "tasks": 1})
        check("dedupe keeps latest weight",
              learning.list_all()[0]["weight"], 0.5)
        # 相似任务命中（改写说法，字符相似度应 > 0.8）
        hits = learning.get_feedback("帮我写一篇公众号文章并配图")
        check("similar task matched", len(hits), 1)
        check("matched chosen", hits[0]["chosen"], "wechat-publisher")
        # 不相关任务不命中
        check("unrelated task no match",
              learning.get_feedback("画一张系统架构图"), [])
        # clear
        n = learning.clear()
        check("clear returns count", n, 1)
        check("clear empties", learning.stats(), {"entries": 0, "tasks": 0})
        # 损坏文件 -> 视为空，不崩溃
        fp.write_text("{not json", encoding="utf-8")
        check("corrupt file -> empty stats",
              learning.stats(), {"entries": 0, "tasks": 0})
    finally:
        learning.FEEDBACK_FILE = orig
        fp.unlink(missing_ok=True)


# ---------------------------------------------------------------- B2 registry
def _base_score(query: str, name: str, desc: str, feedback):
    rec = {"name": name, "dir_name": name, "tier": "user",
           "description": desc, "tags": [], "display_name": ""}
    s, why, det = R.score_skill(R.tokenize(query), query.lower(), rec,
                                feedback=feedback)
    return s, why, det


def test_weighting():
    fb = [{"task": "帮我写公众号文章", "excluded": ["drawio-skill"],
           "chosen": "wechat-publisher", "weight": 1.0}]
    q = "帮我写公众号文章"
    # chosen 技能：反馈后比无反馈时高 1.5
    s0, _w0, d0 = _base_score(q, "wechat-publisher", "写公众号推文", None)
    s1, w1, d1 = _base_score(q, "wechat-publisher", "写公众号推文", fb)
    check("chosen boost +1.5", round(s1 - s0, 4), 1.5)
    check("chosen why mentions 反馈加权", any("反馈加权" in x for x in w1), True)
    check("feedback_boost in detail", d1.get("feedback_boost"), 1.5)
    # excluded 技能：低 2.0
    s2, _w2, _d2 = _base_score(q, "drawio-skill", "画架构图", None)
    s3, w3, _d3 = _base_score(q, "drawio-skill", "画架构图", fb)
    check("excluded penalty -2.0", round(s3 - s2, 4), -2.0)
    check("excluded why mentions 反馈加权", any("反馈加权" in x for x in w3), True)
    # 无关技能不受影响
    s4, _w4, _d4 = _base_score(q, "tavily", "网络搜索", None)
    s5, _w5, _d5 = _base_score(q, "tavily", "网络搜索", fb)
    check("unrelated skill unchanged", s4, s5)
    # weight=0.5 减半：+0.75 / -1.0
    fb05 = [dict(fb[0], weight=0.5)]
    s6, _w6, _d6 = _base_score(q, "wechat-publisher", "写公众号推文", fb05)
    s7, _w7, _d7 = _base_score(q, "drawio-skill", "画架构图", fb05)
    check("weight 0.5 halves chosen", round(s6 - s0, 4), 0.75)
    check("weight 0.5 halves excluded", round(s7 - s2, 4), -1.0)
    # 相似度 <= 0.8 不生效（任务完全不相关）
    fb_far = [{"task": "画一张系统架构图", "excluded": ["x"], "chosen": "y",
               "weight": 1.0}]
    _s8, _w8, d8 = _base_score(q, "y", "某技能", fb_far)
    check("below-threshold feedback no effect", d8.get("feedback_boost"), 0.0)
    # 无反馈时 detail 不含 feedback_boost（向后兼容，key 集合不变）
    _s9, _w9, d9 = _base_score(q, "wechat-publisher", "写公众号推文", None)
    check("no feedback -> no feedback_boost key", "feedback_boost" in d9, False)


# ---------------------------------------------------------------- A1 finder
def test_mirrors():
    check("default mirrors == MIRRORS", finder.mirrors(), finder.MIRRORS)
    old = os.environ.get(finder.MIRROR_ENV)
    try:
        os.environ[finder.MIRROR_ENV] = "https://a.example.com,https://b.example.com"
        check("env override parsed",
              finder.mirrors(),
              ["https://a.example.com", "https://b.example.com"])
        os.environ[finder.MIRROR_ENV] = "https://only.example.com"
        check("env single mirror", finder.mirrors(), ["https://only.example.com"])
        os.environ[finder.MIRROR_ENV] = ""
        check("empty env falls back to default",
              finder.mirrors(), finder.MIRRORS)
    finally:
        if old is None:
            os.environ.pop(finder.MIRROR_ENV, None)
        else:
            os.environ[finder.MIRROR_ENV] = old
    # build_release_url 支持镜像基址，默认仍指向 GitHub（向后兼容）
    u = finder.build_release_url("skill-radoute", "v2.1.0")
    check("default url is github", u.startswith("https://github.com/"), True)
    um = finder.build_release_url("skill-radoute", "v2.1.0",
                                  mirror="https://gitclone.com")
    check("mirror url uses mirror base", um.startswith("https://gitclone.com/"), True)


class _FakeResp:
    def __init__(self, b: bytes):
        self._b = b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self._b


def test_download_switch():
    """github 源失败 -> 自动切换到下一个镜像源（mock 网络，不发真实请求）。"""
    old_env = os.environ.get(finder.MIRROR_ENV)
    os.environ[finder.MIRROR_ENV] = "https://github.com,https://hub.fastgit.xyz"
    orig_open = finder.urllib.request.urlopen
    calls: list[str] = []

    def fake_open(req, timeout=0):
        calls.append(req.full_url)
        if req.full_url.startswith("https://github.com/"):
            raise urllib.error.URLError("timed out")
        return _FakeResp(b"SKILL-CONTENT")

    finder.urllib.request.urlopen = fake_open
    try:
        err = io.StringIO()
        with redirect_stderr(err):
            data, url = finder.download("skill-radoute", "v2.1.0", timeout=5)
        check("first mirror tried", calls[0].startswith("https://github.com/"), True)
        check("second mirror used", url.startswith("https://hub.fastgit.xyz/"), True)
        check("switch message printed", "切换至国内镜像源" in err.getvalue(), True)
        check("data returned", data, b"SKILL-CONTENT")
    finally:
        finder.urllib.request.urlopen = orig_open
        if old_env is None:
            os.environ.pop(finder.MIRROR_ENV, None)
        else:
            os.environ[finder.MIRROR_ENV] = old_env


# ---------------------------------------------------------------- B3 router CLI
def test_router_feedback_cli():
    fb_path = _tmp_file()
    env = {**os.environ, "SKILL_RADOUTE_FEEDBACK": str(fb_path)}
    router = str(Path(__file__).parent / "router.py")

    def run(*args: str) -> subprocess.CompletedProcess:
        # 显式 UTF-8：router 输出含中文，Windows 默认 gbk 解码会崩
        return subprocess.run([sys.executable, router, *args],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)

    try:
        # list（初始为空）
        r = run("feedback", "list")
        check("feedback list exits 0", r.returncode, 0)
        check("feedback list empty output", r.stdout.strip(), "(no feedback entries)")
        # stats
        r = run("feedback", "stats")
        check("feedback stats 0/0", r.stdout.strip(), "entries=0 tasks=0")
        # 预置一条反馈 -> list / stats 反映出来
        fb_path.write_text(json.dumps(
            {"version": 1, "entries": [
                {"task": "写文章", "excluded": ["a"], "chosen": "b",
                 "timestamp": 1.0, "weight": 1.0},
                {"task": "画图", "excluded": [], "chosen": "c",
                 "timestamp": 2.0, "weight": 1.0},
            ]}), encoding="utf-8")
        r = run("feedback", "stats")
        check("feedback stats 2/2", r.stdout.strip(), "entries=2 tasks=2")
        r = run("feedback", "list")
        check("feedback list shows entries", "chosen='b'" in r.stdout, True)
        # clear
        r = run("feedback", "clear")
        check("feedback clear exits 0", r.returncode, 0)
        check("feedback clear removed 2", r.stdout.strip(), "cleared 2 feedback entries")
        r = run("feedback", "stats")
        check("feedback stats after clear", r.stdout.strip(), "entries=0 tasks=0")
    finally:
        fb_path.unlink(missing_ok=True)


if __name__ == "__main__":
    test_learning()
    test_weighting()
    test_mirrors()
    test_download_switch()
    test_router_feedback_cli()
    print("\nLEARNING + MIRROR TESTS: PASS" if not FAILED
          else f"\nLEARNING + MIRROR TESTS: {len(FAILED)} FAILED -> {FAILED}")
    raise SystemExit(1 if FAILED else 0)
