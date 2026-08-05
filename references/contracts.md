# 契约规范：调用封套、上下文总线、追踪事件

目录
- [1. 状态文件布局](#1-状态文件布局)
- [2. 调用封套 Call Envelope](#2-调用封套-call-envelope)
- [3. 上下文总线 Context Bus](#3-上下文总线-context-bus)
- [4. 追踪事件 Trace Events](#4-追踪事件-trace-events)
- [5. 命名约定](#5-命名约定)
- [6. 解耦规则](#6-解耦规则)

---

## 1. 状态文件布局

根目录由环境变量 `SKILL_ROUTER_HOME` 决定，默认 `<cwd>/.workbuddy/router`。

```
.workbuddy/router/
  registry.json            全量技能索引（可随时重建，非权威数据）
  registry_extra.json      手工登记的外部/远程技能
  current_session          当前活跃会话 id（纯文本一行）
  sessions/<sid>/
    session.json           会话元信息、当前技能、调用栈、计数器
    context.json           上下文总线（唯一的跨 skill 数据交换面）
    trace.jsonl            追加写事件日志（审计与回溯的唯一真相源）
```

关键性质：
- `trace.jsonl` 只追加，不改写、不删除。调用表由它重建（`_calls()`），因此历史永远可复原。
- `session.json` 与 `context.json` 是派生的快照，损坏时可从 trace 重放恢复。
- 全部状态在磁盘上，进程之间无共享内存。这是「切换不需要重启」的物理基础。

---

## 2. 调用封套 Call Envelope

任何 skill 的一次调用都必须先 `call open`，产出如下封套。被调用的 skill 只看封套，不看会话全貌。

```json
{
  "call_id": "c003",
  "session": "s-20260805-095858-c54",
  "skill": "drawio-skill",
  "intent": "生成 Agent 架构图",
  "parent": "c002",
  "inputs": {"style": "layered"},
  "context_in": {"research.raw": {"sources": 3}},
  "missing_context": [],
  "must_write": ["draft.diagram"]
}
```

| 字段 | 含义 | 约束 |
|---|---|---|
| `call_id` | 会话内自增 `cNNN` | 由 router 生成，禁止手写 |
| `intent` | 一句话说明这次调用要达成什么 | 必填，写给人看的 |
| `parent` | 父调用 id | 缺省取栈顶，构成调用树 |
| `inputs` | 一次性参数 | 不落上下文总线，仅本次有效 |
| `context_in` | 从总线读到的值 | 由 `--reads` 声明后自动注入 |
| `missing_context` | 声明要读但总线里没有 | 非空即为契约破裂，必须先补齐或降级 |
| `must_write` | 本次调用应写回总线的 key | 由 `--writes` 声明，收尾时校验 |

结束时 `call close` 产出结果记录：

```json
{
  "call_id": "c003",
  "status": "ok | partial | failed | skipped",
  "outputs": {"nodes": 12},
  "artifacts": ["arch.drawio"],
  "note": "自由文本，写清失败原因或遗留项"
}
```

`outputs` 存放小体量的结构化返回值；大体量产物写文件后把路径放进 `artifacts`，并把路径本身写入上下文总线。

---

## 3. 上下文总线 Context Bus

唯一的跨 skill 数据通道。skill 之间不直接互相传参，只通过命名槽位读写。

```json
{
  "slots": {
    "research.raw": {
      "value": {"sources": 3, "summary": "三条要点"},
      "type": "json",
      "written_by": "tavily",
      "call_id": "c001",
      "ts": "2026-08-05T09:59:13+08:00",
      "rev": 1
    }
  }
}
```

规则：
1. **写者署名**：`written_by` 与 `call_id` 自动记录，任何值都能追溯到产出它的那次调用。
2. **版本递增**：同一 key 覆盖写时 `rev` 累加，历史版本在 `trace.jsonl` 的 `ctx_set` 事件中可查。
3. **值要小**：总线存摘要、路径、标识符、结构化结论。原始大文本落文件，总线只放路径。
4. **读前声明**：`call open --reads k` 让缺失显式暴露，而非在 skill 内部悄悄读到空值。
5. **不做隐式类型转换**：`--json` 明确标注 JSON，其余按文本存。

---

## 4. 追踪事件 Trace Events

`trace.jsonl` 每行一个事件，公共字段 `ts` / `seq` / `event`。

| event | 触发时机 | 关键字段 |
|---|---|---|
| `session_new` | 会话创建 | `goal`, `mode` |
| `route` | 每次路由决策 | `task`, `decision`, `chosen`, `reason`, `candidates[]` |
| `call_open` | 调用开始 | `call_id`, `skill`, `intent`, `parent`, `reads`, `writes`, `missing_reads` |
| `call_close` | 调用结束 | `call_id`, `status`, `outputs`, `artifacts`, `note` |
| `call_suspend` | 中途让位给别的 skill | `call_id`, `reason` |
| `call_resume` | 挂起后回到原 skill | `call_id` |
| `switch` | 技能切换 | `from`, `to`, `kind`, `reason`, `carry`, `missing_carry`, `suspended_call` |
| `ctx_set` / `ctx_del` | 上下文写入/删除 | `key`, `rev`, `written_by`, `preview` |
| `acquire` | 从网络获取技能 | `skill`, `origin`, `audit`, `path` |
| `session_end` | 会话收尾 | `summary` |

`switch.kind` 取值语义：

| kind | 场景 |
|---|---|
| `handoff` | 正常交接，前一步已完成 |
| `fallback` | 首选技能失败，降级到备选 |
| `escalate` | 任务复杂度超出当前技能，升级到更强的 |
| `retry` | 换参数或换实现再试一次 |
| `rollback` | 撤回到更早的技能重做 |

---

## 5. 命名约定

- 上下文 key：`<域>.<名>`，小写点分。例：`research.raw`、`draft.url`、`review.blockers`。
- 域名建议按阶段而非按技能命名，这样换技能不用改 key（`research.*` 而非 `tavily.*`）。
- 会话 id：`s-YYYYMMDD-HHMMSS-xxx`，调用 id：`cNNN`。

---

## 6. 解耦规则

1. **技能不感知路由器**：被调用的 skill 按自己原本的方式工作，router 只在外层记录与传值。不改任何既有 skill 的文件。
2. **禁止技能间直接依赖**：A 不得假设 B 已运行。要什么就 `--reads` 声明，缺了就走 `missing_context` 分支。
3. **单一写者优先**：同一个 key 尽量只由一个阶段写。确需多写时靠 `rev` 与 trace 区分。
4. **失败不污染总线**：`call close --status failed` 时不要把半成品写进总线，用 `note` 说明。
5. **路由器不执行业务**：router 只做选择、记账、传值。任何实际工作都发生在被路由到的 skill 里。
