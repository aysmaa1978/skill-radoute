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

- **工作流编排（v2.0）**：不止选一个技能，而是把多技能协作定义成可执行的工作流。
  - 模板驱动：YAML/JSON 声明 `steps`（skill + intent + input/output 上下文槽位），`router.py workflow run <模板名>` 一条命令串行执行，步骤间自动传递上下文。
  - 失败可恢复：任一步失败自动回滚该步写入（前序结果保留），`router.py workflow resume` 从断点续跑。
  - 示例模板见 `workflow.example.yaml`（调研并发布 / 写作配图 / 翻译校对）。

- **并行执行（v2.0）**：多意图且子任务互不依赖时并行，总耗时约等于最慢子任务。
  - `intent.parse` 按依赖图分层：`parallelizable: true` 可并行，`parallel_groups` 给出分层执行计划。
  - `route --parallel` 强制并行；`router.run_parallel()` 基于 ThreadPoolExecutor 并发执行，异常各自捕获不互相阻断。

- **动态技能加载（v2.0）**：内存只驻留轻量索引，技能完整内容按需加载。
  - 路由决策只加载候选 top3 元数据（零磁盘读取）；执行时才加载完整 SKILL.md + scripts。
  - LRU 保留最近 5 个已加载技能，超出自动卸载；`registry.py cache stats / load / evict` 管理。

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

## 快速场景

按场景抄命令即可上手（`$S` = `<技能目录>/scripts`，`$PY` = `python3`）。

### 场景一：一句话生成并执行调研→整理→发布工作流（v3.0）

```bash
# 从自然语言直接生成工作流模板并保存
"$PY" "$S/router.py" workflow from-text "搜索AI进展，整理成要点，写成公众号文章" --save research-publish
# 开会话并执行（skill 会被逐一调用，上下文自动传递）
"$PY" "$S/router.py" session new --goal "调研发布" --mode auto
"$PY" "$S/router.py" workflow run research-publish
```

### 场景二：交互式搭一个「翻译→校对」工作流（v3.0）

```bash
"$PY" "$S/router.py" workflow build --save translate-review
# 按提示依次输入：名称 / skill / intent / input / output，回车跳过可选字段
```

### 场景三：不知道用哪个技能时，先查后路由

```bash
"$PY" "$S/registry.py" search "帮我整理AI资料画架构图" --top 5   # 只看打分排序
"$PY" "$S/router.py" route "帮我整理AI资料画架构图" --explain     # 看决策理由
"$PY" "$S/router.py" route "帮我整理AI资料画架构图" --exclude drawio-skill  # 否决后重试
```

### 场景四：本地没合适技能，远程装一个

```bash
# 国内网络先配镜像/代理（二选一，见 FAQ Q6/Q7）
set SKILL_RADOUTE_MIRROR=https://hub.fastgit.xyz,https://gitclone.com
"$PY" "$S/acquire.py" run --query "海报" --auto
```

### 场景五：路由越用越准（反馈学习）

```bash
"$PY" "$S/router.py" feedback stats         # 查看已积累的反馈
# 路由结果不理想时记录反馈（chosen=选中的，excluded=否决的）
python -c "import sys; sys.path.insert(0, '$S'); import learning; \
learning.record_feedback('帮我写公众号文章', ['drawio-skill'], 'wechat-publisher')"
"$PY" "$S/router.py" feedback list          # 确认已记录
```

## 更新日志

> 只保留最近 3 个版本，完整历史见 [CHANGELOG.md](CHANGELOG.md)。

### v3.0.0（自然语言编排 + 交互式工作流）

把「说一句话就能跑多技能工作流」变成现实，并针对 v2.1 评测（综合 4.7）打磨文档与体验。

- **自然语言解析引擎（NLP）**：`intent.py` 动作词表从 10 类扩展至 **20 类**（新增 规划/头脑风暴/审阅/提取/转换/问答/测试/调试/下载/安装），并给出依赖图与建议技能；`workflow.parse_workflow(text)` 把一句话直接解析成可运行的工作流模板，依赖链自动串接上下文。
- **交互式工作流构建**：`router.py workflow from-text "搜索并整理AI进展"` 从自然语言生成模板；`router.py workflow build` 逐步问答构建模板；两者都支持 `--save <名称>` 落盘为 YAML。
- **文档与规范**：README 更新日志精简至最近 3 版（历史移至 `CHANGELOG.md`）、FAQ 扩展至 20+ 条、新增「快速场景」章节。
- **体验打磨**：异常提示统一「原因 + 解决建议」两步式；本地反馈数据接入工作流技能推荐（`registry.py search` 加权）。
- 测试：新增 `test_workflow_nlp.py`（20 动作识别 + 24 条自然语言用例准确率 100% + build/from-text/--save），原 5 套测试全绿；纯标准库零新依赖，接口向后兼容。

### v2.1.0（国内镜像源适配 + 路由反馈学习）

- **P0 镜像适配**：`finder.py` 多源自动切换（`MIRRORS`：github.com / hub.fastgit.xyz / gitclone.com，失败自动切换并提示；`SKILL_RADOUTE_MIRROR` 环境变量可覆盖）；`acquire.py` 自动启用 `HTTP_PROXY`/`HTTPS_PROXY`/`GITHUB_PROXY` 并给出无代理指引；`quickstart.bat` 新增镜像配置步骤。
- **P1 反馈学习**：新增 `learning.py`（本地 `~/.workbuddy/feedback.json`，绝不上传）；`registry.score_skill` 任务相似度 > 0.8 时 chosen +1.5×weight、excluded −2.0×weight；`router.py feedback list|clear|stats`；反馈变化自动失效路由缓存。
- 测试：新增 `test_learning.py`（38 断言），原 4 套测试全绿。

### v2.0.0（多技能编排引擎）

从「路由器」升级为「多技能编排引擎」：不止选一个技能，而是**定义、编排、执行多技能工作流**。以 v1.7（中文化/超时重试/FAQ/quickstart）+ v1.8（增量扫描/路由缓存/内存驻留）为底座。

- **工作流编排（workflow）**：`workflow.py` 支持 YAML/JSON 模板定义多步工作流（skill + intent + 上下文输入输出），`router.py workflow run <模板名>` 一条命令串行执行，步骤间自动传递上下文；任一步失败自动回滚该步写入并提示 `workflow resume`，可从断点续跑（不重复已完成步骤）。
- **并行执行引擎（parallel）**：`intent.py` 按依赖图给子任务分层（无依赖 `parallelizable: true`，有依赖如 调研→写作 `false`）；`route` 输出自带 `parallel_groups` 分层计划，`route --parallel` 强制并行；`router.run_parallel()` 基于 ThreadPoolExecutor 并发执行，总耗时约等于最慢子任务。
- **动态技能加载（dynamic loading）**：路由只加载候选 top3 元数据（索引驻留、零磁盘读取）；执行时按需加载完整内容（SKILL.md + scripts），LRU 保留最近 5 个自动卸载；`registry.py cache stats / load / evict` 管理。
- 向后兼容：`route` / `acquire` / `call` 公开接口不变；纯标准库，零新依赖。

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

### Q6：如何配置国内镜像源加速下载？

设置 `SKILL_RADOUTE_MIRROR` 环境变量即可覆盖默认镜像列表（默认 `github.com` → `hub.fastgit.xyz` → `gitclone.com`，逗号或空白分隔多个）：

```bash
# Windows (cmd)
setx SKILL_RADOUTE_MIRROR "https://hub.fastgit.xyz,https://gitclone.com"
# PowerShell / macOS / Linux
$env:SKILL_RADOUTE_MIRROR="https://hub.fastgit.xyz,https://gitclone.com"
export SKILL_RADOUTE_MIRROR="https://hub.fastgit.xyz,https://gitclone.com"
```

下载失败时 `finder.py` 会自动切换下一个镜像并打印「⚠️ GitHub 连接超时，切换至国内镜像源...」。`quickstart.bat` 也内置了该配置的可选步骤。

### Q7：设置了代理还是不生效？

`acquire.py` 按 `GITHUB_PROXY` → `HTTPS_PROXY` → `HTTP_PROXY` 优先级读取。检查：

1. 变量名拼写正确（`HTTPS_PROXY` 不是 `HTTPS_PROXY_`，注意 `HTTP_PROXY` 在某些环境会被系统程序忽略，用 `HTTPS_PROXY` 更稳）。
2. 代理地址格式：`http://127.0.0.1:7890`（带协议头）。
3. 启动时打印「✅ 已检测到代理配置」才说明读到了；没有该提示说明变量没进进程环境，重启终端再试。

### Q8：路由反馈学习是什么？数据安全吗？

v2.1 起，路由可以「越用越准」：记录某类任务否决了哪些技能、最终选了哪个，下次相似任务（相似度 > 0.8）打分时自动加权（chosen +1.5×weight、excluded −2.0×weight）。数据只存本机 `~/.workbuddy/feedback.json`，**绝不上传**；随时可用 `router.py feedback clear` 一键清空。详见 SKILL.md「路由反馈学习」一节。

### Q9：feedback 文件损坏或想重置怎么办？

反馈文件在 `~/.workbuddy/feedback.json`（`SKILL_RADOUTE_FEEDBACK` 可改路径）。文件损坏时程序自动按空表处理，不会崩溃；彻底重置就执行 `router.py feedback clear`，或直接删除该文件。

### Q10：如何从一句话生成工作流？

```bash
"$PY" "$S/router.py" workflow from-text "搜索AI进展并整理成要点"            # 打印 YAML 模板
"$PY" "$S/router.py" workflow from-text "搜索AI进展并整理成要点" --save rp   # 保存为 YAML
```

`parse_workflow` 基于 `intent.py` 的 20 类动作词表拆分子任务，自动把依赖链（如 调研→整理）串成 `input/output` 上下文传递。

### Q11：workflow build 交互构建怎么用？

```bash
"$PY" "$S/router.py" workflow build            # 逐步问答
"$PY" "$S/router.py" workflow build --save x   # 构建完直接保存
```

按提示依次输入名称、每个步骤的 skill/intent/input/output（可选字段直接回车跳过），最后回车结束（或输入 n 停止添加步骤）。

### Q12：工作流执行失败怎么恢复？

任一步失败会自动**回滚该步写入**（前序步骤结果保留），然后：

```bash
"$PY" "$S/router.py" workflow resume    # 从失败断点续跑，不重复已完成步骤
```

如果提示「没有可恢复的工作流」，说明已完成或从未运行，直接 `workflow run <模板名>` 重跑即可。

### Q13：模板文件应该放在哪里？

`workflow run` 按序查找：`./workflows/<名>` → `./<名>` → `~/.workbuddy/workflows/<名>` → 技能目录 `workflows/<名>`（支持 `.yaml`/`.yml`/`.json`，可省略扩展名）。`--save` 保存时写到 `SKILL_ROUTER_WORKFLOW_DIR`（若设置）→ `./workflows/`（若存在）→ `~/.workbuddy/workflows/`。

### Q14：如何触发多技能并行执行？

当任务包含多个互不依赖的子任务（如「写文章并画架构图」）时，`intent.parse` 会标记 `parallelizable: true`；用 `router.py route "任务" --parallel` 强制并行执行。有依赖链的任务（调研→写作）必须串行，不会误并行。

### Q15：为什么路由结果是 confirm 而不是 auto？

`route --explain` 会给出 `decision_reason` 与 `missing_trigger`，常见原因：top1 分数低于自动阈值、领先 top2 幅度不足、同族技能冲突、多意图判定为 decompose。按 FAQ Q5 的步骤用 `--exclude` 否决或拆分任务即可。

### Q16：sentinel 误拦/误预警怎么办？

安全黑名单是硬阻断（`proceed:false`），确认任务确实合规后可调整规则文件 `~/.workbuddy/sentinel_rules.json`（`SKILL_ROUTER_SENTINEL_RULES` 可改路径）；能力/资源类预警只提示不阻断，可忽略。改完规则无需重启。

### Q17：Windows 控制台输出中文乱码？

这是终端编码问题，不是数据问题（文件本身是 UTF-8）：

```bash
chcp 65001                  # cmd 切换到 UTF-8
$env:PYTHONIOENCODING="utf-8"   # PowerShell 下强制 Python UTF-8 输出
```

### Q18：如何给项目跑测试？

```bash
python3 scripts/test_acquire.py        # 安全修复契约（16 断言）
python3 scripts/test_scoring.py        # 打分不变量
python3 scripts/test_call_chain.py     # call 生命周期冒烟
python3 scripts/test_explain.py        # route --explain 契约
python3 scripts/test_workflow.py       # v2.0 编排引擎契约（40 断言）
python3 scripts/test_workflow_nlp.py   # v3.0 NLP 解析（20 动作 + 24 用例准确率 100%）
python3 scripts/test_learning.py       # v2.1 反馈学习（38 断言）
```

全部通过输出末尾应包含 `PASS` 或 `0 failed`。

### Q19：技能安装到哪个目录？怎么卸载？

`acquire.py` 安装到 `~/.workbuddy/skills/<技能名>/`；卸载直接删除该目录，然后执行 `registry.py scan` 刷新索引（避免残留缓存）。

### Q20：如何确认当前版本？

- git 安装：`git describe --tags`（如 `v2.1.0`）。
- 发布包安装：文件名自带版本号（`skill-radoute-v2.1.0.skill.zip`）。
- 文档：README「更新日志」与 `CHANGELOG.md` 按版本倒序记录。

### Q21：git push 报认证失败（Invalid username or token）？

GitHub 已禁用密码认证，必须用 Personal Access Token（PAT，需 `repo` 权限）。两种方式：

1. 在系统设置好凭据后正常 `git push`（GCM 会弹浏览器登录）。
2. 临时用令牌：`git push https://<用户名>:<令牌>@github.com/<用户>/<仓库>.git main`（令牌会出现在命令历史，用完建议轮换）。

### Q22：反馈数据如何接入工作流推荐？

`registry.search` 打分时已自动应用本地反馈加权（v2.1 起）；v3.0 起 `workflow from-text` 生成模板时，步骤的技能推荐同样走 `intent.TYPE_SKILLS`，若该技能在反馈中被否决过，路由阶段会体现减分。想让推荐更准，就多用 `learning.record_feedback` 积累反馈（见场景五）。
