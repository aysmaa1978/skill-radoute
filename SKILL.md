---
name: skill-radoute
description: 元技能路由器。在一次会话内发现、选择、调用并切换其它 skill，维护跨 skill 的共享上下文与可回溯调用链。当任务需要多个技能协作、不确定该用哪个技能、当前技能失败需要换一个、要求记录/审计技能调用过程、或本地没有合适技能需要从网络检索安装时使用。触发词：技能路由、skill router、该用哪个技能、换个技能试试、编排多个 skill、调用链、切换技能、找不到合适的技能、技能注册表、上下文传递给下一个技能。
---

# Skill Router

## Overview

把「选哪个技能、怎么把上一步结果交给下一步、切换时状态怎么办、事后怎么复盘」这四件事，从模型的临时判断变成有文件记录的可控流程。

三层职责：
- `registry.py` 扫描本机全部技能来源，建索引，按任务描述打分排序。
- `router.py` 管会话、路由决策、上下文总线、调用链，全部落盘到 `.workbuddy/router/`。
- 本 SKILL.md 规定何时调用它们，以及模型在其中的判断责任。

**边界**：路由器只做选择、传值、记账。任何实际业务都发生在被路由到的那个 skill 里，且不修改任何既有 skill 的文件。

---

## 环境准备

脚本路径以本技能目录为基准，下文记作 `<S>`。Python 解释器按此顺序取第一个可用的：受管 Python 绝对路径 → `python3` → `python`。本机为：

```bash
PY="python3"   # 或受管 Python / 任意 Python 3.10+ 解释器绝对路径
S="<本技能目录>/scripts"
```

状态目录默认 `<当前工作区>/.workbuddy/router/`，可用环境变量 `SKILL_ROUTER_HOME` 改写。

首次使用或装过新技能后刷新索引：

```bash
"$PY" "$S/registry.py" scan
```

---

## 核心工作流

### Step 1 · 开会话

一个用户目标一个会话。目标未变就**不要**新建，否则上下文与调用链断裂。

```bash
"$PY" "$S/router.py" session new --goal "把调研写成公众号文章并配图" --mode auto
```

`--mode`：`auto`（默认，置信度达标才自动执行，否则列候选让人确认）｜`always`（无条件取 top1）｜`manual`（每次都人工点名）。
高风险动作（发布、支付、删除、外发邮件）在 `route` 时临时加 `--mode manual` 覆盖。

已有会话时用 `router.py status` 确认当前状态，不要盲目新建。

### Step 2 · 路由

**每个子任务单独路由一次。** 把子任务描述得具体，描述越含糊分数越不可信。

```bash
"$PY" "$S/router.py" route "检索 AI Agent 2026 年的最新进展并给出结构化要点"
```

返回 `decision`：

| decision | 处理 |
|---|---|
| `auto` | 直接进 Step 3 |
| `confirm` | 把 top3 的名称、`why`、description 摘要列给用户，给出你的推荐与理由，等确认 |
| `decompose` | 多意图：返回 `sub_task_plan`（每子任务类型 + 建议技能），逐个子任务重新 `route` / `switch` 串成多步链路，不要取单技能硬跑 |
| `no_match` | 读 `references/remote-acquisition.md` 走远程获取 |

> `route` 每次都会先跑 `intent.parse`（纯标准库），当解析出 ≥2 个不同任务类型时即判定为多意图，返回 `decompose` 而非盲目取 top1。同族碰撞（`[SIBLING]`）优先级高于多意图。

**分数只是词法先验，最终选谁由你判断。** 命中理由全是零散单字、或 top1 的 description 明确排除了当前场景时，直接否决它，用 `--exclude <name>` 重新路由一次，让否决动作留在 trace 里。判定细则见 `references/routing-rules.md`。

### Step 3 · 开调用并真正加载目标技能

```bash
"$PY" "$S/router.py" call open \
  --skill tavily --intent "检索 AI Agent 最新进展" \
  --input query="AI agent 2026" \
  --reads research.brief --writes research.raw
```

输出的封套里 `context_in` 是自动注入的上游数据，`missing_context` 非空说明依赖没满足，先补齐或降级，不要硬着头皮往下走。

拿到封套后，**用 `Skill` 工具真正加载该技能**并按封套里的 `intent` / `inputs` / `context_in` 执行。router 负责记账，`Skill` 负责执行，两者缺一不可。

### Step 4 · 写回上下文并收尾

```bash
"$PY" "$S/router.py" ctx set research.raw '{"sources":3,"points":["..."]}' --json
"$PY" "$S/router.py" call close --id c001 --status ok --output count=3 --note "命中3个权威来源"
```

`--status`：`ok` ｜ `partial`（部分完成，note 写清遗留项）｜ `failed` ｜ `skipped`。

`--id` 推荐显式给出；省略时取当前未关闭的最内层调用，若没有可关闭的调用会报错并提示传 `--id`。

上下文只存摘要、路径、标识符、结构化结论。大段原文落文件，总线里只放路径。

### Step 5 · 切换

换技能时**永远用 `switch`，不新建会话**。

```bash
"$PY" "$S/router.py" switch --to drawio-skill --kind handoff \
  --reason "正文需要架构图，先出图再回来排版" \
  --carry research.raw --keep-open
```

`--keep-open` 会把当前调用挂起而非丢弃，后续 `call resume c002` 原样回到现场，上下文与调用栈完整保留。

`--kind`：`handoff` 正常交接 ｜ `fallback` 首选失败降级 ｜ `escalate` 能力不足升级 ｜ `retry` 换参重试 ｜ `rollback` 回退重做。

**切换不需要重启也不需要重新初始化**：全部状态在磁盘文件里，换技能只是换一份被加载的指令。

### Step 6 · 回溯与收尾

```bash
"$PY" "$S/router.py" trace                    # 渲染完整调用链
"$PY" "$S/router.py" trace --out chain.md     # 导出给用户
"$PY" "$S/router.py" replay c002              # 打印某次调用的重放封套
"$PY" "$S/router.py" session end --summary "文章已入草稿箱，封面待补"
```

向用户汇报时，把 `trace` 的关键几行贴出来，让选择依据可见。

---

## 命令速查

| 命令 | 用途 |
|---|---|
| `registry.py scan` | 重建索引（装过新技能后必跑） |
| `registry.py search "<任务>" --top 5` | 只打分不记账，快速探查 |
| `registry.py show <name>` | 查单个技能的完整记录 |
| `registry.py sources` | 列出检测到的技能根目录与数量 |
| `registry.py add --path <dir> --tier user --origin <url>` | 登记非标准位置的技能 |
| `router.py status` | 当前会话、当前技能、未闭合调用、上下文 key |
| `router.py session list / use <id> / end` | 会话管理 |
| `router.py route "<任务>" [--mode] [--exclude N]` | 路由决策 |
| `router.py call open / close / list / resume` | 调用生命周期 |
| `router.py switch --to N --reason R` | 技能切换 |
| `router.py ctx set / get / del / history / rollback` | 上下文总线读写（history/rollback 为版本化） |
| `router.py trace [--format jsonl] [--out f]` | 调用链渲染与导出 |
| `router.py replay <call_id>` | 重放封套 |
| `router.py intent parse "<文本>"` | 自然语言 → 结构化任务（意图/子任务/建议技能） |
| `router.py sentinel check "<任务>" [--subtasks J] [--skills S]` | 安全/能力/资源边界检查 |
| `router.py route "<任务>" --guard` | 路由前跑意图解析 + 能力/资源检查（安全拦截常开） |
| `router.py acquire-log --skill N --origin U --audit P2` | 记录远程技能获取 |
| `acquire.py run --query Q [--slug S] [--auto] [--force]` | 检索→审计→确认→安装→注册 全自动链路 |
| `acquire.py resume` / `acquire.py reset` | 中断后续跑 / 放弃当前会话 |

全局 `--session <id>` 可指定非活跃会话，放在子命令之前。

---

## 何时不要用这个技能

- 单一技能就能完成、且不需要留痕的任务。直接调那个技能，别套一层。
- 纯问答、纯解释类请求。
- 用户已经点名了技能且只有一步。此时直接执行，最多事后补一条记录。

套壳本身有成本，只有在「多技能协作」「选择存在不确定性」「需要审计」这三种情况下才值得。

---

## 参考文件

| 文件 | 何时读 |
|---|---|
| `references/routing-rules.md` | 需要调阈值、处理路由误配、设计多步链路、遇到失败要降级时 |
| `references/contracts.md` | 需要精确的封套/上下文/trace 字段定义，或排查状态文件问题时 |
| `references/remote-acquisition.md` | `decision=no_match`，或本地候选全都不胜任，需要从网络检索安装新技能时 |

---

## 一个完整示例

用户：把这份调研整理成公众号文章，配一张架构图。

```bash
"$PY" "$S/router.py" session new --goal "调研转公众号文章并配图" --mode auto

"$PY" "$S/router.py" route "把调研要点整理成公众号文章正文"
# → auto, chosen=wechat-publisher
"$PY" "$S/router.py" call open --skill wechat-publisher \
  --intent "撰写公众号正文" --reads research.raw --writes draft.md
#   ↑ 随后用 Skill 工具加载 wechat-publisher 并执行

"$PY" "$S/router.py" switch --to drawio-skill --kind handoff \
  --reason "正文需要架构图" --carry research.raw --keep-open
"$PY" "$S/router.py" route "画一张 Agent 系统架构图"
"$PY" "$S/router.py" call open --skill drawio-skill \
  --intent "生成架构图" --reads research.raw --writes draft.diagram
"$PY" "$S/router.py" ctx set draft.diagram "arch.drawio"
"$PY" "$S/router.py" call close --id c002 --status ok --artifact arch.drawio

"$PY" "$S/router.py" call resume c001       # 回到正文，上下文原样
"$PY" "$S/router.py" call close --id c001 --status ok --output words=1800
"$PY" "$S/router.py" trace
```

`trace` 输出会呈现为带缩进的调用树，每次路由的候选、分数、判定依据，以及每次切换的原因和携带的上下文，全部可查。

---

## 远程获取：acquire 子命令

`decision=no_match` 且本地候选全不胜任时，用 `acquire.py` 走完整获取链路，全程写入与 router trace 同格式的 `acquire_trace.jsonl`（`$SKILL_ROUTER_ACQUIRE_TRACE` 可改路径）。

远程获取使用带签名的 GitHub Releases 作为可信源（`finder.GITHUB_RELEASE`），不再使用任何未经验证的第三方 CDN。下载的 zip 先做 **SHA256 哈希校验**（预期值硬编码在 `acquire.KNOWN_SKILLS`，绝不从远程获取），再做完整性校验（必须是可读 zip 且含 `SKILL.md`）；未预置哈希、哈希不匹配、或伪造的包会被直接删除、不进入安装流程。

五步流水线，每步状态落在 `~/.workbuddy/acquire_state.json`（`$SKILL_ROUTER_ACQUIRE_STATE` 可改路径），中断后可 `resume` 续跑：

```
find    → finder.search()         从受信发布表按 slug+版本解析候选（禁止动态 latest）
audit   → security_check.audit()  文件操作/网络外发/硬编码密钥/Shell执行 分级
confirm → 自动安装安全技能包（仅 P1/P2 通过，P0 直接报错退出）
install → 下载 zip 解压到 ~/.workbuddy/skills/<slug>（含 zip-slip 防护）
register→ registry.scan() 刷新索引（失败仅标记 skipped，不阻断）
```

用法：

```bash
"$PY" "$S/acquire.py" run --query skill-radoute --version v1.5.0   # 显式锁定版本，走 GitHub Releases + 哈希校验
"$PY" "$S/acquire.py" run --query "海报" --auto          # 半自动，仅自动安装 P1/P2 安全包
"$PY" "$S/acquire.py" resume                            # 中断后续跑，从首未完成步
"$PY" "$S/acquire.py" reset                             # 放弃当前会话，回到空白
```

风险定级（供 `confirm` 分支）：`high`=P0（必须交互确认，`--auto`/`--force` 均不绕过）｜`medium`=P1（`--auto` 自动过）｜`low/none`=P2。`audit` 只做粗筛，不是安全证明；真正兜底在 confirm 环节。

> 高风险技能包（P0）需交互模式确认，不受 `--auto`/`--force` 影响。`--force` 仅用于覆盖已安装目录，绝不绕过 P0 安全确认。

注：当前 `--source` 仅 `github`（签名 Release）。新技能须先在 `finder.TRSTED_RELEASES` 登记受信 repo，并在 `acquire.KNOWN_SKILLS` 预置哈希，否则获取时提示“请联系作者更新”。SkillHub 官方 registry 端点就绪后可作为补充来源，但仍需版本锁定 + 哈希校验。

---

## 需求雷达与边界哨兵（v1.2）

路由前增加两层前置：`intent` 把自然语言解析成结构化任务，`sentinel` 在路由前检查任务是否越界。两者都是纯标准库规则引擎，不依赖 LLM，可作为后续 LLM 版（P2）的可替换薄层。

### 需求雷达 · intent

关键词 + 正则规则引擎，把一句话转成机器可消费的任务描述，直接喂给 `route` 做增强输入。

```bash
"$PY" "$S/router.py" intent parse "帮我整理AI资料，画架构图"
# → {"intent":"research_and_visualize","domain":"AI_Agent",
#     "sub_tasks":[...collect/structure/visualize...],
#     "suggested_skills":["tavily","summarize","drawio-skill"]}
```

输出字段稳定：`intent` / `domain` / `sub_tasks[]`（type+target） / `suggested_skills[]`。当前 `route` 只回传建议、不强行提权，避免词法规则污染语义路由。

### 边界哨兵 · sentinel

路由前守三道边界，任一道不过则拒绝或预警：

| 边界 | 行为 | 配置 |
|---|---|---|
| 安全 | 命中黑名单关键词直接拒（`proceed:false`，exit=2） | `security_blacklist`（必拦） |
| 能力 | 本地技能覆盖 sub_tasks 低于 80% 时预警 | `TYPE_COVER`（子串匹配） |
| 资源 | 需要 API Key 但未配置时预警 | `API_KEY_SKILLS` |

规则文件：`~/.workbuddy/sentinel_rules.json`（`SKILL_ROUTER_SENTINEL_RULES` 可覆盖），缺省用内置默认规则。

```bash
"$PY" "$S/router.py" sentinel check "帮我黑掉隔壁网站"
# → {"proceed":false,"reason":"security_policy_violation",...}

"$PY" "$S/router.py" route "整理AI资料画架构图" --guard
# 安全拦截常开；--guard 额外跑意图解析 + 能力/资源检查
# emit 带 intent 字段，trace 记 route_intent / route_warning / route_blocked
```

**集成点**：`route` 每次都先跑安全拦截（常开）；`--guard` 才额外跑意图解析与能力/资源检查（有 I/O 成本，默认关）。`sentinel` 只是第一道闸，真正的兜底在模型自身的 refuse 能力。
