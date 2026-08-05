# skill-radoute

元技能路由器（meta-skill router）。在一次会话内发现、选择、调用并切换其它 skill，维护跨 skill 的共享上下文与可回溯调用链；本地没有合适技能时，可自动从 SkillHub 检索、审计、安装新技能。

## 安装

**方式一 · SkillHub 一键安装**

在 WorkBuddy 技能市场搜索 `skill-radoute`，点击安装即可（v1.1 已提交审核）。

**方式二 · GitHub 手动安装**

```bash
# 克隆到用户级技能目录（WorkBuddy 会自动识别）
git clone https://github.com/aysmaa1978/skill-radoute \
  ~/.workbuddy/skills/skill-radoute
```

> 安装目标固定为 `~/.workbuddy/skills/`（用户级）。Python 解释器需 3.10+，无第三方依赖。

## 核心功能

两条主线：

- **路由（router）**：把「选哪个技能、怎么把上一步结果交给下一步、切换时状态怎么办、事后怎么复盘」从模型的临时判断变成有文件记录的可控流程。
  - 注册发现：扫描 project / user / plugin / builtin / connector 五类来源，建索引按任务描述打分排序。
  - 统一路由：`auto`（置信度达标才自动）／`always`（无条件 top1）／`manual`（人工点名）。
  - 上下文总线：上游产出按命名槽位注入下一步，缺失依赖显式暴露。
  - 可回溯：调用链、切换历史、否决动作全部落盘，可渲染、可重放、可导出。

- **远程获取（acquire）**：`no_match` 且本地候选全不胜任时，一条命令走完「检索 → 安全审计 → 确认 → 安装 → 注册」全流程，全程写入调用链。
  - 安全审计扫描文件操作／网络外发／硬编码密钥／Shell 执行，定级 high(P0)／medium(P1)／low(P2)。
  - 中断后可 `resume` 续跑，状态落盘。

边界：路由器只做选择、传值、记账，不修改任何既有 skill 的文件。

## 快速开始

```bash
PY="python3"                       # 或你的 Python 3.10+ 解释器
S="<技能目录>/scripts"

# 1) 路由：把调研写成文章
"$PY" "$S/router.py" session new --goal "调研转公众号文章" --mode auto
"$PY" "$S/router.py" route "把调研要点整理成公众号文章正文"
"$PY" "$S/router.py" call open --skill wechat-publisher \
  --intent "撰写公众号正文" --reads research.raw --writes draft.md
#   ↑ 随后用 Skill 工具加载 wechat-publisher 执行
"$PY" "$S/router.py" call close --id c001 --status ok --output words=1800

# 2) 切换：正文需要架构图，挂起当前调用去画
"$PY" "$S/router.py" switch --to drawio-skill --kind handoff \
  --reason "正文需要架构图" --carry research.raw --keep-open
#   ... 画完图后 ...
"$PY" "$S/router.py" call resume c001    # 原样回到正文现场

# 3) 远程获取：本地没有合适的海报技能，自动装一个
"$PY" "$S/acquire.py" run --query "把要点做成海报" --slug poster
"$PY" "$S/acquire.py" run --query "海报" --auto    # 半自动，跳过 P1/P2 确认
```

## 命令速查

**router.py**

| 命令 | 用途 |
|---|---|
| `registry.py scan` | 重建索引（装过新技能后必跑） |
| `registry.py search "<任务>" --top 5` | 只打分不记账，快速探查 |
| `registry.py show <name>` | 查单个技能完整记录 |
| `router.py session new / list / use / end` | 会话管理 |
| `router.py route "<任务>" [--mode] [--exclude N]` | 路由决策 |
| `router.py call open / close / list / resume` | 调用生命周期 |
| `router.py switch --to N --reason R` | 技能切换（挂起/恢复） |
| `router.py ctx set / get / del` | 上下文总线读写 |
| `router.py trace [--out f]` / `replay <call_id>` | 调用链渲染与重放 |
| `router.py status` | 当前会话/技能/未闭合调用/上下文 |

**acquire.py**

| 命令 | 用途 |
|---|---|
| `acquire.py run --query Q [--slug S] [--auto] [--force]` | 检索→审计→确认→安装→注册 全自动链路 |
| `acquire.py resume` | 中断后续跑，从首未完成步 |
| `acquire.py reset` | 放弃当前会话，回到空白 |

> `--auto` 自动跳过 P1/P2 确认，但 P0（高危）仍拒绝，除非加 `--force`。

## 配置

通过环境变量改写默认落盘位置：

| 变量 | 默认 | 作用 |
|---|---|---|
| `SKILL_ROUTER_HOME` | `<cwd>/.workbuddy/router/` | router 状态/索引/trace 目录 |
| `SKILL_ROUTER_ACQUIRE_STATE` | `~/.workbuddy/acquire_state.json` | acquire 会话状态文件 |
| `SKILL_ROUTER_ACQUIRE_TRACE` | 同 `SKILL_ROUTER_HOME` 下的 `acquire_trace.jsonl` | acquire 调用链记录 |

状态与生成物均在技能包之外，不会误提交。

## 目录结构

```
skill-radoute/
├── SKILL.md                  # 技能清单与调用规范（权威细节在此）
├── scripts/
│   ├── registry.py           # 技能索引与打分（五类来源扫描）
│   ├── router.py             # 会话/路由/上下文/调用链
│   ├── finder.py             # acquire: SkillHub 检索与归一化
│   ├── security_check.py     # acquire: 安全审计分级
│   ├── acquire_state.py      # acquire: 状态持久化与中断恢复
│   └── acquire.py            # acquire: 五步流水线主控
├── references/               # 路由规则、封套契约、远程获取协议
├── LICENSE                   # MIT
└── README.md
```

## License

MIT — 见 [LICENSE](LICENSE)。
