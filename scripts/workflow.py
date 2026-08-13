#!/usr/bin/env python3
"""workflow.py - v2.0 多技能工作流编排引擎。

从「选一个技能」升级为「定义并执行多步工作流」：用户用 YAML/JSON 模板声明
steps（skill + intent + 上下文输入/输出），一次命令串行执行，步骤间自动
传递上下文（context bus），任一步失败自动回滚该步写入并给出恢复指令
（router.py workflow resume），支持失败后从断点续跑。

纯标准库（内置极简 YAML 子集解析器，兼容 JSON），无第三方依赖。

模板格式（YAML 子集或 JSON）：
    name: "调研并发布"
    steps:
      - skill: tavily
        intent: "搜索最新AI进展"
        output: research.raw
      - skill: summarize
        intent: "总结调研内容"
        input: research.raw
        output: draft.summary
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router  # noqa: E402   # 复用 context bus / trace / call 生命周期
import intent  # noqa: E402   # v3.0: 自然语言 -> 工作流模板（parse_workflow）

STATE_FILE = Path(os.environ.get(
    "SKILL_ROUTER_WORKFLOW_STATE",
    str(Path.home() / ".workbuddy" / "workflow_state.json")))


# ------------------------------------------------------------ template load

def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    return v


def parse_template(text: str) -> dict:
    """解析工作流模板：支持 JSON 与 YAML 子集（name + steps 列表）。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("工作流模板为空")
    if text.startswith("{"):
        tmpl = json.loads(text)
        if not isinstance(tmpl, dict) or "steps" not in tmpl:
            raise ValueError("JSON 模板须含 steps 列表")
        return tmpl
    root: dict = {}
    steps: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if stripped.startswith("- "):                     # 新步骤
            cur = {}
            steps.append(cur)
            key, _, val = stripped[2:].partition(":")
            cur[key.strip()] = _scalar(val.strip())
        elif ":" in stripped:
            key, _, val = stripped.partition(":")
            k = key.strip()
            if k == "steps":
                continue
            if indent == 0:                               # 顶层键（name 等）
                cur = None
                root[k] = _scalar(val.strip())
            else:                                         # 缩进 -> 当前步骤字段
                if cur is None:
                    cur = {}
                    steps.append(cur)
                cur[k] = _scalar(val.strip())
    root["steps"] = steps
    if not steps:
        raise ValueError("工作流模板未定义任何步骤（steps）")
    for i, s in enumerate(steps, 1):
        if not s.get("skill"):
            raise ValueError(f"第 {i} 步缺少 skill")
    return root


# ------------------------------------------------------------ v3.0 NLP -> template

def parse_workflow(text: str, name: str = "") -> dict:
    """把自然语言任务描述解析成工作流模板（与 parse_template 输出同构）。

    基于 intent.parse 的子任务拆分：每个子任务映射为一个 step：
      - skill   取该类型建议技能的第一项（intent.TYPE_SKILLS）
      - intent  取子任务的模板 target
      - output  用 <type>.result 命名，供后续步骤 input 引用
      - input   当前类型依赖前一类型时自动串接上一步 output

    v3.0 (M4): 技能选择会结合本地反馈学习数据——被否决的技能跳过、
    被选中的技能优先（learning.get_feedback 相似度匹配）。

    返回 {"name": ..., "steps": [...]}，可直接 `router.py workflow run`
    （等价模板），也可 `--save` 落盘为 YAML。
    抛 ValueError 当任务描述解析不出任何动作。
    """
    spec = intent.parse(text)
    steps: list[dict] = []
    prev_type: str | None = None
    prev_out: str | None = None
    for st in spec["sub_tasks"]:
        t = st["type"]
        skills = intent.TYPE_SKILLS.get(t, [])
        step = {"skill": _pick_skill(t, text, skills),
                "intent": st.get("target", t)}
        if prev_out and prev_type in intent.DEPENDS_ON.get(t, ()):
            step["input"] = prev_out       # 依赖链自动串接上下文
        out = f"{t}.result"
        step["output"] = out
        steps.append(step)
        prev_type, prev_out = t, out
    if not steps:
        raise ValueError(f"❌ 无法从任务描述解析出任何动作：{text}\n"
                         f"   原因：没有命中 20 类动作词表中的任一关键词\n"
                         f"   解决：换一种说法，如「搜索...」「写...」「画...」；"
                         f"或先用 router.py intent parse \"{text}\" 查看解析结果")
    return {"name": name or f"{spec['intent']}_workflow", "steps": steps}


def _pick_skill(task_type: str, text: str, skills: list) -> str:
    """v3.0 (M4): 结合本地反馈学习选择技能——被否决的跳过、被选中的优先。

    反馈不存在/不可用时回退到建议技能列表第一项（向后兼容）。
    """
    if not skills:
        return task_type
    try:
        import learning
        fbs = learning.get_feedback(text)
    except Exception:
        fbs = []
    if fbs:
        chosen = {str(fb.get("chosen", "")).lower() for fb in fbs}
        excluded = {str(x).lower() for fb in fbs
                    for x in (fb.get("excluded") or [])}
        for s in skills:
            if s.lower() in chosen:
                return s                       # 反馈选中的技能优先
        for s in skills:
            if s.lower() not in excluded:
                return s                       # 跳过被否决的技能
    return skills[0]


def find_template(name: str) -> Path | None:
    """按名称查找模板：<cwd>/workflows、<cwd>、~/.workbuddy/workflows、
    技能根目录 workflows/。支持 .yaml/.yml/.json 扩展（可省略）。"""
    cands = [
        Path.cwd() / "workflows" / name,
        Path.cwd() / name,
        Path.home() / ".workbuddy" / "workflows" / name,
        Path(__file__).resolve().parent.parent / "workflows" / name,
    ]
    for base in cands:
        for suffix in ("", ".yaml", ".yml", ".json"):
            p = Path(str(base) + suffix)
            if p.is_file():
                return p
    return None


def load_template(name: str) -> dict:
    p = find_template(name)
    if not p:
        # v3.0: 报错带「原因 + 解决建议」，不再只给一句找不到
        raise FileNotFoundError(
            f"❌ 未找到工作流模板：{name}\n"
            f"   原因：在 ./workflows/、./、~/.workbuddy/workflows/、技能目录 workflows/ "
            f"均未找到 <{name}>.yaml/.yml/.json\n"
            f"   解决：① 把模板放入上述任一目录；"
            f"② 用 router.py workflow from-text \"<任务>\" --save {name} 一键生成；"
            f"③ 或 router.py workflow build --save {name} 交互式构建")
    return parse_template(p.read_text(encoding="utf-8", errors="replace"))


# -------------------------------------------------------------- state / run

def _load_state() -> dict | None:
    if not STATE_FILE.is_file():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_state(st: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except OSError:
        pass


def _clear_state() -> None:
    try:
        STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _ctx_get(sid: str, key: str):
    if not key:
        return None
    ctx = router.load_ctx(sid)
    slot = ctx.get("slots", {}).get(key)
    return slot.get("value") if slot else None


def _ctx_set(sid: str, key: str, value) -> None:
    if not key:
        return
    ctx = router.load_ctx(sid)
    slots = ctx.setdefault("slots", {})
    slots[key] = router._push_version(slots.get(key, {}), value,
                                      "workflow", None, "text", router.now_iso())
    router.save_ctx(sid, ctx)


def _ctx_restore(sid: str, key: str, prior) -> None:
    """回滚：把 ctx 槽位恢复到该步写入前的状态（有先值则还原，无则删除）。"""
    if not key:
        return
    ctx = router.load_ctx(sid)
    slots = ctx.setdefault("slots", {})
    if prior is _MISSING:
        slots.pop(key, None)
    else:
        slots[key] = router._push_version(slots.get(key, {}), prior,
                                          "workflow", None, "text", router.now_iso())
    router.save_ctx(sid, ctx)


class _Missing:
    pass


_MISSING = _Missing()


def run_workflow(sid: str, tmpl: dict, exec_fn=None, start: int = 0) -> int:
    """串行执行工作流。

    exec_fn(step, ctx_in) -> dict：默认记录型执行器（仅打通调用链与上下文
    传递，真正执行留给 Skill 工具 / 注入的 runner）。抛异常即该步失败：
    回滚该步 ctx 写入 -> 落盘状态 -> 提示 workflow resume。
    """
    steps = tmpl["steps"]
    state = {"name": tmpl["name"], "sid": sid, "current": 0,
             "completed": [], "failed": None}
    if start > 0:
        prev = _load_state() or {}
        state = {"name": prev.get("name", tmpl["name"]), "sid": sid,
                 "current": start,
                 "completed": prev.get("completed", list(range(start))),
                 "failed": None}
    for idx in range(start, len(steps)):
        step = steps[idx]
        state["current"] = idx
        state["failed"] = None
        cid = f"w{idx + 1:03d}"
        reads = [step["input"]] if step.get("input") else []
        writes = [step["output"]] if step.get("output") else []
        missing = [k for k in reads if _ctx_get(sid, k) is None]
        print(f"▶ 步骤 {idx + 1}/{len(steps)} [{step['skill']}] "
              f"{step.get('intent', '')}")
        router.trace_append(sid, "call_open", call_id=cid,
                            skill=step["skill"], intent=step.get("intent", ""),
                            parent=None, inputs={}, reads=reads, writes=writes,
                            missing_reads=missing)
        ctx_in = {k: _ctx_get(sid, k) for k in reads if k}
        prior = _MISSING
        if step.get("output"):
            prior_val = _ctx_get(sid, step["output"])
            prior = prior_val if prior_val is not None else _MISSING
        try:
            # 按需加载技能完整内容（v2.0 动态加载），执行注入的 runner
            registry_load(step["skill"])
            outputs = exec_fn(step, ctx_in) if exec_fn else {"status": "ok"}
            if not isinstance(outputs, dict):
                outputs = {"result": outputs}
            if step.get("output"):
                _ctx_set(sid, step["output"], outputs)
            router.trace_append(sid, "call_close", call_id=cid, status="ok",
                                outputs=outputs)
            state["completed"].append(idx)
            _save_state(state)
        except Exception as e:
            _ctx_restore(sid, step.get("output"), prior)   # 自动回滚该步写入
            router.trace_append(sid, "call_close", call_id=cid, status="failed",
                                error=str(e)[:200])
            state["failed"] = idx
            _save_state(state)
            print(f"❌ 工作流「{tmpl['name']}」第 {idx + 1} 步失败：{e}", file=sys.stderr)
            print(f"   已回滚第 {idx + 1} 步写入，前 {idx} 步结果保留。", file=sys.stderr)
            print("   恢复指令：router.py workflow resume", file=sys.stderr)
            return 1
    _clear_state()
    print(f"✅ 工作流「{tmpl['name']}」执行完成，共 {len(steps)} 步。")
    return 0


def registry_load(slug: str) -> None:
    """按需加载技能完整内容（v2.0 动态加载）；失败不阻断（仅记录型执行）。"""
    try:
        import registry
        registry.load_skill_full(slug)
    except Exception:
        pass


# -------------------------------------------------- v3.0 交互构建 / 文本生成

def yaml_dump(tmpl: dict) -> str:
    """把模板 dict 转成 YAML 子集文本（与 parse_template 解析器同构）。"""
    lines = [f'name: "{tmpl.get("name", "")}"', "steps:"]
    for s in tmpl.get("steps", []):
        lines.append("  - skill: " + str(s.get("skill", "")))
        if s.get("intent"):
            lines.append(f'    intent: "{s.get("intent")}"')
        if s.get("input"):
            lines.append("    input: " + str(s["input"]))
        if s.get("output"):
            lines.append("    output: " + str(s["output"]))
    return "\n".join(lines) + "\n"


def _save_dir() -> Path:
    """模板保存目录：SKILL_ROUTER_WORKFLOW_DIR > ./workflows（若存在）> ~/.workbuddy/workflows。"""
    env = os.environ.get("SKILL_ROUTER_WORKFLOW_DIR", "").strip()
    if env:
        return Path(env)
    cwd_wf = Path.cwd() / "workflows"
    if cwd_wf.is_dir():
        return cwd_wf
    return Path.home() / ".workbuddy" / "workflows"


def save_template(tmpl: dict, name: str | None = None) -> Path:
    """保存模板为 YAML 文件，返回路径。name 缺省取模板名，非法字符替换为 _。"""
    name = (name or tmpl.get("name") or "workflow").strip()
    name = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name)
    d = _save_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.yaml"
    p.write_text(yaml_dump(tmpl), encoding="utf-8")
    return p


def cli_from_text(text: str, save: str | None = None) -> int:
    """from-text：自然语言 -> 工作流模板（可选 --save 落盘 YAML）。"""
    try:
        tmpl = parse_workflow(text, name=(save or ""))
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    if save:
        try:
            p = save_template(tmpl, save)
        except OSError as e:
            print(f"❌ 保存模板失败：{e}", file=sys.stderr)
            return 1
        print(f"✅ 已保存工作流模板：{p}")
    else:
        print(yaml_dump(tmpl), end="")
        print("💡 加 --save <名称> 可保存为 YAML 模板")
    return 0


def cli_build(save: str | None = None) -> int:
    """build：交互式构建工作流模板（逐步骤问答，可选 --save 落盘 YAML）。"""
    def ask(prompt: str, default: str = "") -> str:
        try:
            v = input(prompt).strip()
        except EOFError:           # 非交互环境视为跳过
            return default
        return v or default

    print("🧱 交互式工作流构建（直接回车跳过可选字段）")
    name = ask("工作流名称: ", "my-workflow")
    steps: list[dict] = []
    idx = 1
    while True:
        print(f"--- 步骤 {idx} ---")
        skill = ask("  skill（技能名，如 tavily）: ")
        if not skill:
            if steps:
                break
            print("❌ 未添加任何步骤", file=sys.stderr)
            return 1
        intent_txt = ask("  intent（要做什么）: ", skill)
        inp = ask("  input（读取的上下文 key，可空）: ")
        out = ask("  output（写入的上下文 key，可空）: ", f"step{idx}.result")
        step = {"skill": skill, "intent": intent_txt}
        if inp:
            step["input"] = inp
        if out:
            step["output"] = out
        steps.append(step)
        more = ask("继续添加步骤？[y/N] ", "n")
        if more.lower() != "y":
            break
        idx += 1
    tmpl = {"name": name, "steps": steps}
    if save:
        try:
            p = save_template(tmpl, save)
        except OSError as e:
            print(f"❌ 保存模板失败：{e}", file=sys.stderr)
            return 1
        print(f"✅ 已保存工作流模板：{p}")
    else:
        print(yaml_dump(tmpl), end="")
        print("💡 加 --save <名称> 可保存为 YAML 模板")
    return 0


def cli_run(sid: str, name: str, exec_fn=None) -> int:
    tmpl = load_template(name)
    return run_workflow(sid, tmpl, exec_fn=exec_fn, start=0)


def cli_resume(sid: str, exec_fn=None) -> int:
    state = _load_state()
    if not state:
        print("⚠️ 没有可恢复的工作流（可能已完成或从未运行）")
        return 0
    tmpl = {"name": state.get("name", "resume"), "steps": []}
    p = find_template(state.get("name", ""))
    if not p:
        print(f"⚠️ 找不到模板：{state.get('name')}，无法续跑", file=sys.stderr)
        return 1
    tmpl = parse_template(p.read_text(encoding="utf-8", errors="replace"))
    start = state.get("failed")
    if start is None:
        start = state.get("current", 0)
    if start is None or start >= len(tmpl["steps"]):
        _clear_state()
        print("⚠️ 工作流已完成，无可恢复步骤")
        return 0
    print(f"↻ 从第 {start + 1} 步续跑工作流「{tmpl['name']}」...")
    return run_workflow(sid, tmpl, exec_fn=exec_fn, start=start)


# --------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="workflow.py", description=__doc__)
    ap.add_argument("--session", help="target session id")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="执行工作流")
    p.add_argument("name", help="工作流模板名（如 research-publish）")
    sub.add_parser("resume", help="从失败断点续跑")
    p = sub.add_parser("from-text", help="v3.0: 从自然语言生成工作流模板")
    p.add_argument("text", help="自然语言任务描述，如 '搜索并整理AI进展'")
    p.add_argument("--save", default=None, help="保存为 YAML 模板名")
    p = sub.add_parser("build", help="v3.0: 交互式构建工作流模板")
    p.add_argument("--save", default=None, help="保存为 YAML 模板名")
    a = ap.parse_args(argv)
    sid = router.require_sid(a.session)
    if a.cmd == "run":
        return cli_run(sid, a.name)
    if a.cmd == "from-text":
        return cli_from_text(a.text, save=a.save)
    if a.cmd == "build":
        return cli_build(save=a.save)
    return cli_resume(sid)


if __name__ == "__main__":
    raise SystemExit(main())
