# skill-radoute v3.0.1 技能包

> 技能包：`skill-radoute-v3.0.1.skill.zip`
> 发布：2026-08-14 ｜ 类型：规则引擎修复版（#R1）

## 包信息

| 项 | 值 |
|---|---|
| 名称 | skill-radoute（元技能路由器 / 多技能编排引擎） |
| 版本 | **v3.0.1** |
| 大小 | 124,575 字节（26 个文件） |
| SHA256 | `sha256:92b7096156fcb6f11a092a2f52ebaf150a2f1be397af23cc82f1a33a689f8e08` |
| 环境要求 | Python 3.10+，纯标准库，零第三方依赖 |
| 下载 | https://github.com/aysmaa1978/skill-radoute/releases/download/v3.0.1/skill-radoute-v3.0.1.skill.zip |
| 源码标签 | git tag `v3.0.1`（commit `841cc7d`） |

## 本版变更（v3.0.1）

**规则引擎修复（#R1）—— 消除 `intent.parse` 单意图误拆：**

1. **英文关键词整词匹配**：关键词匹配改为词边界正则（中文保持子串匹配），消除子串假阳性：
   - `latest` 不再误命中 `test` → `search the web for latest AI research papers` 恢复单意图（应为 tavily）
   - `poster` 不再误命中 `post` → `design a minimalist poster with philosophy` 恢复单意图（应为 algorithmic-poster-philosophy）
   - `implementation` 不再误命中 `implement` → `plan a multi step implementation task` 恢复单意图（应为 writing-plans）
2. **成对动作合并**：`find/install + 技能/skill/skillhub` 视为单一获取目标 → `find and install a skill from skillhub` 恢复单意图（应为 find-skills）
3. 真多意图（`and/then/并/再` 并列的独立任务）不受影响，回归用例已锁定。

来源：v3.0.0 规则引擎测试报告（54 例全量路由决策，3 轮逐字节一致，确定性 100%；内核匹配质量与 v1.5 持平，接地 clear top1 相关率 0.733）。

## 功能总览

- **路由（router）**：会话 / 打分路由（auto/confirm/manual）/ 上下文总线 / 调用链审计（trace/replay）
- **远程获取（acquire）**：检索 → 安全审计（P0/P1/P2）→ 确认 → 安装 → 注册，支持国内镜像源自动切换与代理
- **需求雷达与边界哨兵（intent/sentinel）**：20 类动作词表，自然语言 → 结构化任务；安全/能力/资源三边界
- **工作流编排（workflow）**：YAML/JSON 模板、`from-text` 一句话生成、`build` 交互构建、失败回滚 + resume、并行执行
- **路由反馈学习（feedback）**：本地 `~/.workbuddy/feedback.json`，打分加权（chosen +1.5×weight / excluded −2.0×weight）
- **动态技能加载**：索引驻留 + LRU 按需加载

## 包内文件清单（26 项）

```
SKILL.md                    技能清单与调用规范
acquire.py                  远程获取五步流水线主控
acquire_state.py            获取状态持久化
finder.py                   受信发布表 + 镜像源多路切换
intent.py                   自然语言解析（20 类动作词表）
learning.py                 路由反馈学习（本地存储）
registry.py                 技能索引与打分
router.py                   会话/路由/上下文/调用链（v3.0.1）
security_check.py           安全审计分级
sentinel.py                 边界哨兵
sync_to_skillhub.py         SkillHub 本地同步
workflow.py                 工作流编排引擎（from-text/build/run/resume）
test_acquire.py / test_call_chain.py / test_explain.py /
test_learning.py / test_scoring.py / test_workflow.py /
test_workflow_nlp.py        7 套测试（全部通过）
contracts.md / remote-acquisition.md / routing-rules.md   参考协议文档
README.md / README.en.md   使用文档
CHANGELOG.md                完整版本历史
workflow.example.yaml       工作流模板示例
```

## 安装与校验

```bash
# 校验包完整性
sha256sum skill-radoute-v3.0.1.skill.zip
# 期望值：92b7096156fcb6f11a092a2f52ebaf150a2f1be397af23cc82f1a33a689f8e08

# 安装到用户级技能目录
unzip skill-radoute-v3.0.1.skill.zip -d ~/.workbuddy/skills/skill-radoute

# 验证版本
python3 ~/.workbuddy/skills/skill-radoute/scripts/router.py status
# （router __version__ = 3.0.1）

# 跑测试
python3 ~/.workbuddy/skills/skill-radoute/scripts/test_workflow_nlp.py
```

## 已知事项

- 本机测试环境索引为 49 技能（v1.5 基线为 66），17 个期望技能缺失属环境漂移，不影响本包代码质量。
- 历轮四类词法弱点（功能兄弟漏判 / 词干泄漏 / CJK 单概念弱化 / 历史明显错配）仍在 backlog，未在本版处理。
