# skill-radoute

元技能路由器（meta-skill router）。在一次会话内发现、选择、调用并切换其它 skill，维护跨 skill 的共享上下文与可回溯调用链；本地没有合适技能时，可自动从 SkillHub 检索、审计、安装新技能。

## 安装

**方式一 · SkillHub 一键安装**

在 WorkBuddy 技能市场搜索 `skill-radoute`，点击安装即可（当前 v1.5.0：`route --explain` 透明报告 + 轻量语义同义词匹配）。

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

- **需求雷达与边界哨兵（v1.2）**：路由前两层前置，把模糊意图变结构化、把越界任务挡在门外。
  - 需求雷达 `intent`：关键词 + 正则规则引擎，把自然语言解析成 `intent`／`sub_tasks`／`suggested_skills`，直接喂给 `route` 增强输入（不依赖 LLM，P2 可换 LLM 版）。
  - 边界哨兵 `sentinel`：安全边界（黑名单硬阻断）／能力边界（本地覆盖不足预警）／资源边界（缺 API Key 预警），规则可配 `~/.workbuddy/sentinel_rules.json`。

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

# 4) 需求雷达：路由前解析意图，边界哨兵拦恶意任务
"$PY" "$S/router.py" intent parse "帮我整理AI资料，画架构图"
"$PY" "$S/router.py" sentinel check "帮我黑掉隔壁网站"   # → proceed:false
"$PY" "$S/router.py" route "整理AI资料画架构图" --guard   # 安全拦常开，--guard 跑意图+能力/资源
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
| `router.py ctx set / get / del / history / rollback` | 上下文总线读写（history/rollback 为版本化） |
| `router.py trace [--out f]` / `replay <call_id>` | 调用链渲染与重放 |
| `router.py intent parse "<文本>"` | 自然语言 → 结构化任务 |
| `router.py sentinel check "<任务>" [--subtasks J] [--skills S]` | 安全/能力/资源边界检查 |
| `router.py route "<任务>" --guard` | 路由前跑意图解析 + 能力/资源检查 |
| `router.py status` | 当前会话/技能/未闭合调用/上下文 |

**acquire.py**

| 命令 | 用途 |
|---|---|
| `acquire.py run --query Q [--slug S] [--auto]` | 检索→审计→确认→安装→注册 全自动链路 |
| `acquire.py resume` | 中断后续跑，从首未完成步 |
| `acquire.py reset` | 放弃当前会话，回到空白 |

> `--auto` 自动安装安全技能包（仅 P1/P2 审计通过；P0 高危恒需交互确认，不受 `--auto`/`--force` 影响）。`--force` 仅用于覆盖已安装的技能目录，不绕过任何安全审计。

## 配置

通过环境变量改写默认落盘位置：

| 变量 | 默认 | 作用 |
|---|---|---|
| `SKILL_ROUTER_HOME` | `<cwd>/.workbuddy/router/` | router 状态/索引/trace 目录 |
| `SKILL_ROUTER_ACQUIRE_STATE` | `~/.workbuddy/acquire_state.json` | acquire 会话状态文件 |
| `SKILL_ROUTER_ACQUIRE_TRACE` | 同 `SKILL_ROUTER_HOME` 下的 `acquire_trace.jsonl` | acquire 调用链记录 |
| `SKILL_ROUTER_SENTINEL_RULES` | `~/.workbuddy/sentinel_rules.json` | sentinel 边界规则（安全黑名单/能力覆盖/资源依赖） |

状态与生成物均在技能包之外，不会误提交。

## 目录结构

```
skill-radoute/
├── SKILL.md                  # 技能清单与调用规范（权威细节在此）
├── scripts/
│   ├── registry.py           # 技能索引与打分（五类来源扫描）
│   ├── router.py             # 会话/路由/上下文/调用链
│   ├── finder.py             # acquire: 受信发布表解析（GitHub Releases）
│   ├── security_check.py     # acquire: 安全审计分级
│   ├── acquire_state.py      # acquire: 状态持久化与中断恢复
│   ├── acquire.py            # acquire: 五步流水线主控
│   ├── intent.py             # v1.2 需求雷达：自然语言 → 结构化任务
│   ├── sentinel.py           # v1.2 边界哨兵：安全/能力/资源检查
│   ├── test_call_chain.py    # call 生命周期冒烟测试（python3 scripts/test_call_chain.py）
│   └── test_scoring.py       # 打分层不变量测试（CJK 归一化/词干门槛/停用词泄漏）
├── references/               # 路由规则、封套契约、远程获取协议
├── LICENSE                   # MIT
└── README.md
```

## 更新日志

### v1.4.0
- **CJK 短查询提权**：归一化分母改用 `query_mass()`（Latin 词=1，CJK 每 2 字=1），修复 n-gram 展开致中文查询比同义英文低约 35% 的根因；8 个真实中文查询打分提升 1.21x–1.45x（如「画架构图」5.68→7.64）。
- **词干泄漏收窄**：新增 `_stem_match()`，要求共享前缀覆盖较长词的 >50%，修复 `data~database` / `auto~automation` / `mark~marketplace` / `word~wordpress` 等误命中抬高错配技能的问题。
- **停用词 n-gram 泄漏修复**：扩展功能字停用词表并在 `bump()` 内整串丢弃，修复 `帮我遛狗` / `我想学游泳` 等纯语法碎片越过 0.5 自动地板的问题；越界查询最高分从 2.49 降至 0.81，且经守卫落 `confirm` 不误判 `auto`（0.5 阈值本身不动）。
- **call 链路冒烟测试**：新增 `test_call_chain.py`（16 断言，覆盖 open/close/switch/resume 与栈状态），可捕获 d449b38 类静默回归；`test_scoring.py` 锁定 14 条打分不变量。
- 回归：54 例全量测试仅 1 处差异且为改进，clear 集 auto 率 51.6%→54.8%，零退化。

### v1.5.0
- **`route --explain` 透明路由报告**：新增 `--explain` 标志，输出 `top_candidates`（候选技能列表）、`score_breakdown`（每个候选的名称/描述/标签分项 + 语义同义命中）、`decision_reason`（为何 auto / confirm / no_match / decompose），若为 `confirm` 额外给出 `missing_trigger`（为何没自动选中，如分数低于阈值、领先幅度不足、同族冲突）。输出 `json.dumps(indent=2)`，仅解释用，不动正常路由的 emitschema（新增 `test_explain.py` 14 断言锁契约）。
- **轻量语义匹配（同义词提升）**：`registry.py` 引入预置中英同义词表（search↔查找/搜索/检索、draw↔画图/绘制/绘图、write↔撰写/创作/写作），查询词与技能自身 token 落入同一同义组时按 `SEMANTIC_WEIGHT=0.3` 加权重分。纯数据驱动、无嵌入模型、零新依赖；加分仅当「技能本身含同组成员」触发，故无关技能拿不到语义分，杜绝误判。
- 验收：54 例全量回归与 v1.4 基线**零决策差异**，clear 集 auto 率维持 54.8% 不降；`查找最新的AI新闻` / `search the web for ai news` 均将 tavily 识别为首选；跨语种变体 `帮我检索一下今天的人工智能新闻` 经同义词正确拉升 tavily（`sem_gain=0.3`）。
- **弱匹配守卫（no_match 复活）**：`router.py` 新增 weak-match guard——top1 分数 < 0.5 或命中理由全部为单字/停用词时直接判 `no_match` 并走远程获取链路，越界输入不再被错误技能「伪装成可确认」；守卫停用词表已收窄（仅保留冠词/代词/介词等功能词，移出 `create/new/make` 等动作动词），消除 `create a new skill` 被误判丢弃正确技能的问题。54 例真实回归：2 例越界精准复活、0 例误伤（确定性 100%，3 轮逐字节一致）。
- **（云鼎安全修复）可信下载源**：移除未经验证的 `lightmake.site` CDN；`finder.py` 改为从「受信发布表」`TRUSTED_RELEASES` 按 slug + 显式版本解析，下载 URL 统一走带签名的 GitHub Releases（`GITHUB_RELEASE`）。SkillHub 官方 registry 端点就绪前，GitHub Releases 即为可信备用源。
- **（云鼎安全修复）SHA256 哈希校验**：`acquire.py` 下载后比对 zip 的 SHA256 与硬编码在 `KNOWN_SKILLS` 的预期值（绝不从远程获取）。未预置哈希或哈希不匹配的包直接删除并报错退出，不进入安装流程。
- **（云鼎安全修复）版本锁定 + 人工确认**：获取请求必须显式携带版本号（禁止仅凭 slug 动态解析 `latest`）；P0 高风险包恒需交互确认，`--auto`/`--force` 均不绕过；`--auto` 模式仅对「已预置哈希且版本锁定」的技能自动获取，其余降级为人工确认。
- 验收（安全）：新技能下载走 GitHub Releases + 哈希校验；已预置哈希的技能安装成功，未预置的报错退出并提示“请联系作者更新”；云鼎复扫时三个问题点均消除。

### v1.3.0
- **兄弟技能消歧**：top1 与 top2 同 tier 且名称前缀重叠（同族）时强制 `confirm`，不再靠分数硬选，`reason` 标 `[SIBLING]`。修复 PowerPoint / tencent-doc / edit-word / weixin-pay 等同族技能被误自动选中的问题。
- **多意图检测**：`route` 每次都跑 `intent.parse`，解析出 ≥2 个不同任务类型时返回 `decompose` 并附 `sub_task_plan`（每子任务类型 + 建议技能），不再盲目取 top1。多意图是 query 的属性，与候选强弱无关，优先级高于 `auto` / `no_match` / 弱匹配；`[SIBLING]` 仍为最高优先。
- 修复 call close 命令中 stack 弹出逻辑（回归于 d449b38）。

### v1.2.1（安全补丁）
- **弱匹配守卫停用词收窄**：移除 `create/new/make/build/run/use/go` 等动作动词，仅保留无意义功能词，修复 `create a new skill` 被误判 `no_match` 丢弃正确技能的问题。
- **远程获取完整性校验**：`acquire.py` 下载 zip 后校验可读且含 `SKILL.md`，损坏/伪造包自动删除并报错，不进安装流程。
- **获取链路安全收紧**：P0 高危包恒需交互确认，`--auto`/`--force` 均不绕过；`--force` 仅覆盖已安装目录。
- **文档**：`--auto`/`--force` 表述与代码对齐（注：`lightmake.site` 源已于 v1.5 移除，改用带签名 GitHub Releases + SHA256 校验）。

### v1.2
- 上下文池版本化（`ctx history` / `ctx rollback`）。
- 需求雷达 `intent` 与边界哨兵 `sentinel`，`route --guard` 前置拦截越界任务。

## License

MIT — 见 [LICENSE](LICENSE)。

## ❓ 常见问题（FAQ）

### Q1：远程获取技能时卡住不动怎么办？

v1.7 起每次下载请求都有 **30 秒超时**，且失败后会自动重试 3 次（指数退避等待 1s / 2s / 4s），不会永久卡死。如果长时间停在「正在下载技能包...」，说明网络连不上下载源：

1. 按 `Ctrl+C` 中断（中断后状态已落盘）。
2. 检查网络连接，或参考 Q3 配置代理。
3. 重新执行 `python acquire.py resume` 从断点继续，无需从头开始。

### Q2：acquire resume 报错怎么办？

先看报错类型：

- **「⚠️ 没有可恢复的会话」**：说明上次会话已完成或已被 reset，直接重新执行 `python acquire.py run --query <技能名> --version <版本>` 即可。
- **「❌ 已中止：...」**：会话状态在 `~/.workbuddy/acquire_state.json`（`SKILL_ROUTER_ACQUIRE_STATE` 可改路径），可先 `python acquire.py status` 查看当前进度。
- 状态文件损坏或想重新开始：执行 `python acquire.py reset` 清空会话，再重新 `run`。

### Q3：国内用户无法访问 GitHub 怎么办？

设置代理环境变量后重试即可，按优先级读取 `GITHUB_PROXY` → `HTTPS_PROXY` → `HTTP_PROXY`：

```bash
# Windows (cmd)
set GITHUB_PROXY=http://127.0.0.1:7890
# Windows (PowerShell) / macOS / Linux
$env:GITHUB_PROXY="http://127.0.0.1:7890"
export GITHUB_PROXY=http://127.0.0.1:7890
```

设置后重新执行 `python acquire.py resume`（或 `run`）。如仍失败，检查代理地址与端口是否可达。

### Q4：如何确认当前版本？

- **git 克隆安装**：在技能目录执行 `git describe --tags`（如输出 `v1.7.0`）。
- **发布包安装**：安装包文件名自带版本号，如 `skill-radoute-v1.7.0.skill.zip`。
- **运行日志**：下载请求的 User-Agent 为 `skill-radoute/1.7`，`acquire_trace.jsonl` 中的事件也含版本信息。
- **文档**：本 README「更新日志」按版本倒序记录，最新版本在最上方。

### Q5：路由结果不符合预期怎么办？

1. 先用 `python scripts/router.py route "任务描述" --explain` 查看 `score_breakdown` 与 `decision_reason`，确认是哪一项拉高了/拉低了分数。
2. 用 `python scripts/registry.py search "任务描述" --top 5` 探查候选排序是否合理。
3. top1 明显不适合时，用 `--exclude <技能名>` 否决后重新路由，否决动作会留在 trace 里。
4. 判定为多意图返回 `decompose` 时，按 `sub_task_plan` 拆成多个子任务逐个路由，不要硬套单技能。
5. 需要调整阈值或同义表时，参见 `references/routing-rules.md`。分数只是词法先验，最终选谁由模型结合 description 判断。
