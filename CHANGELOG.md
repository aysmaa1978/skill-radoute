# CHANGELOG

skill-radoute 完整版本历史（README 只保留最近 3 个版本，全部历史在此）。

## 版本速览

| 版本 | 主题 | 一句话 |
|---|---|---|
| [v3.0.0](#v300) | 自然语言编排 | 20 类动作解析、一句话生成工作流、交互构建、文档打磨 |
| [v2.1.0](#v210) | 镜像 + 反馈学习 | 国内镜像加速、路由越用越准 |
| [v2.0.0](#v200) | 多技能编排引擎 | 工作流 / 并行执行 / 动态加载 |
| [v1.8.0](#v180) | 性能优化 | 增量扫描 / 路由缓存 / 内存驻留 |
| [v1.7.0](#v170) | 体验补全 | 中文化报错 / 超时重试 / FAQ / quickstart |
| [v1.6.0](#v160) | 稳定性修复 | resume 崩溃 / P0 自动放行 / current_skill 清空 |
| [v1.5.0](#v150) | 透明路由 + 安全 | explain 报告 / 同义词匹配 / 云鼎安全修复 |
| [v1.4.0](#v140) | 中文打分修复 | CJK 提权 / 词干收窄 / 停用词泄漏 |
| [v1.3.0](#v130) | 决策修正 | 兄弟技能消歧 / 多意图检测 |
| [v1.2.1](#v121) | 安全补丁 | 守卫收窄 / 完整性校验 |
| [v1.2](#v12) | 前置层 | 上下文版本化 / intent + sentinel |

---

## v3.0.0

自然语言解析引擎 + 交互式工作流构建（针对 v2.1 评测反馈打磨，目标 4.9）。

- **NLP 动作词表（20 类）**：`intent.py` 在原有 10 类基础上新增 规划/头脑风暴/审阅/提取/转换/问答/测试/调试/下载/安装，含依赖图、建议技能、模板 target、组合意图标签。
- **`workflow.parse_workflow(text)`**：一句话解析为可运行模板（依赖链自动串接 `input/output`），与 `parse_template` 输出同构，可直接 `workflow run`。
- **`workflow from-text` / `workflow build`**：自然语言生成与交互式构建，均支持 `--save <名称>` 落盘 YAML（`SKILL_ROUTER_WORKFLOW_DIR` 可改保存目录）。
- **文档**：README 更新日志精简至最近 3 版（历史移至 CHANGELOG.md）、FAQ 扩展至 22 条、新增「快速场景」章节、SKILL.md 补充新命令。
- **体验**：异常提示统一「原因 + 解决建议」；反馈数据接入工作流技能推荐。
- **测试**：新增 `test_workflow_nlp.py`（20 动作逐一识别 + 24 条自然语言用例准确率 100% + yaml 往返/保存/from-text/build），原 5 套测试全绿。纯标准库零新依赖，向后兼容。

## v2.1.0

国内镜像源适配（P0）+ 路由反馈学习（P1）。

- **镜像**：`finder.py` `MIRRORS` 三源自动切换（`SKILL_RADOUTE_MIRROR` 可覆盖）；`acquire.py` 代理自动启用与指引；`quickstart.bat` 镜像配置步骤。
- **反馈学习**：`learning.py` 本地 `feedback.json`；`score_skill` 相似度 > 0.8 加权（chosen +1.5×weight / excluded −2.0×weight）；`router.py feedback list|clear|stats`；反馈变化自动失效路由缓存。
- **测试**：`test_learning.py`（38 断言）；原 4 套全绿。

## v2.0.0

从「路由器」升级为「多技能编排引擎」。

- 工作流编排（YAML/JSON 模板、串行执行、上下文自动传递、失败回滚 + resume）。
- 并行执行引擎（依赖图分层、`route --parallel`、ThreadPoolExecutor）。
- 动态技能加载（索引驻留、按需加载、LRU 5）。
- 测试：`test_workflow.py`（40 断言），原 4 套全绿。

## v1.8.0

性能优化。

- 注册表增量扫描 + 扫描缓存。
- 路由决策缓存（LRU 128，指纹自动失效）。
- 技能索引内存驻留 + 文件变更监听（WATCH_INTERVAL）。

## v1.7.0

体验补全。

- 中文化报错信息。
- 下载超时（30s）+ 自动重试 3 次（指数退避）+ 进度打印。
- README FAQ、quickstart.bat（ASCII-only）、口语化触发词。

## v1.6.0

稳定性修复（发布包 + 自更新链路打通）。

- 修复 resume 崩溃。
- P0 自动放行漏洞修复。
- current_skill 清空修复。
- 记录 v1.6.0 发布包 SHA256 至 `acquire.KNOWN_SKILLS`（自举约定）。

## v1.5.0

透明路由报告 + 轻量语义匹配 + 云鼎安全修复。

- `route --explain`：top_candidates / score_breakdown / decision_reason / missing_trigger（`test_explain.py` 14 断言）。
- 语义同义词提升（search↔查找/搜索/检索 等，`SEMANTIC_WEIGHT=0.3`）。
- 弱匹配守卫复活 `no_match`（54 例回归 0 误伤）。
- 云鼎安全修复：可信下载源（GitHub Releases，移除 lightmake.site）、SHA256 哈希校验、版本锁定 + P0 人工确认。

## v1.4.0

中文打分修复。

- CJK 短查询提权（`query_mass()` 归一化，中文查询提升 1.21x–1.45x）。
- 词干泄漏收窄（`_stem_match()` 前缀覆盖 >50%）。
- 停用词 n-gram 泄漏修复（`帮我遛狗` 类不再误判）。
- `test_call_chain.py`（16 断言）+ `test_scoring.py`（14 断言）。

## v1.3.0

决策修正。

- 兄弟技能消歧（同族强制 confirm，`[SIBLING]`）。
- 多意图检测（≥2 类返回 decompose + sub_task_plan）。
- 修复 call close 出栈逻辑（d449b38 回归）。

## v1.2.1

安全补丁。

- 弱匹配守卫停用词收窄（保留功能词，移出动作动词）。
- 远程获取完整性校验（zip 可读且含 SKILL.md）。
- P0 高危包恒需交互确认（--auto/--force 均不绕过）。

## v1.2

- 上下文池版本化（`ctx history` / `ctx rollback`）。
- 需求雷达 `intent` 与边界哨兵 `sentinel`，`route --guard` 前置拦截。
