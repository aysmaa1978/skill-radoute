# 路由规则：打分、判定、降级

目录
- [1. 打分算法](#1-打分算法)
- [2. 决策表](#2-决策表)
- [3. 模型的否决权](#3-模型的否决权)
- [4. 多步任务的路由](#4-多步任务的路由)
- [5. 失败与降级](#5-失败与降级)
- [6. 反模式](#6-反模式)

---

## 1. 打分算法

`registry.py search` 的分数是**词法先验**，不是语义判断。构成如下。

分词（`tokenize`）
- 拉丁词：长度 ≥2，权重 1.0
- 中文单字：权重 0.3（单字歧义大，刻意压低）
- 中文二元组：权重 1.0
- 中文三元组：权重 1.2

匹配（`_overlap`）
- 精确命中：`查询权重 × 文档权重 × idf`
- 拉丁前缀互配（`debug` ↔ `debugging`）：再乘 0.7
- `idf = log(1 + N/(1+df))`，压制在大量技能描述里都出现的高频词

字段加权
| 字段 | 系数 |
|---|---|
| name / dir_name / displayName | 3.0 |
| tags | 1.5 |
| description | 1.0 |
| 技能名整体出现在任务描述里 | 额外 +8.0 |

归一与分层
```
norm  = raw / (sqrt(查询总权重) + 3)
final = norm × TIER_WEIGHT[来源层]
```

| 来源层 | 权重 | 说明 |
|---|---|---|
| project | 1.00 | 当前工作区 `.workbuddy/skills/` |
| user | 0.96 | `~/.workbuddy/skills/` |
| plugin | 0.92 | 插件缓存内置技能 |
| builtin | 0.90 | 客户端自带技能 |
| connector | 0.80 | 连接器技能，依赖外部授权 |
| remote | 0.60 | 已知但未安装 |

分层权重只做同分裁决，量级很小，不会让弱相关的本地技能压过强相关的内置技能。

---

## 2. 决策表

`router.py route` 输出三种 `decision`。

| 条件 | decision | 后续动作 |
|---|---|---|
| `mode=manual` | `confirm` | 列候选 + 理由，等用户点名 |
| `mode=always` | `auto` | 无条件取 top1 |
| `mode=auto` 且 top1 ≥ `--threshold`(1.2) 且 top1/top2 ≥ `--margin`(1.3) | `auto` | 直接执行 |
| `mode=auto` 但未同时满足阈值与领先幅度 | `confirm` | 列 top3 + `why` + 一句话推荐，等确认 |
| 无候选 | `no_match` | 转 `references/remote-acquisition.md` |

`mode` 可在 `session new` 时设默认值，也可在单次 `route` 上用 `--mode` 覆盖。

调参建议
- 任务领域窄、技能库干净：`--threshold 0.8 --margin 1.15`，更多自动。
- 高风险动作（发布、支付、删除、外发）：`--mode manual`，一律确认。
- 已知某技能刚失败：`--exclude <name>` 把它从候选里剔掉再路由。

---

## 3. 模型的否决权

**分数只负责把 60+ 个技能收敛到 3-5 个，最终选谁由模型判定。**

出现下列信号时，无视 top1 直接改选或改为 `confirm`：
- top1 的 `why` 全是零散单字或与任务无关的通用词（典型误配）。
- top1 的 description 明确写了排他条件，而当前任务落在排除范围内。
- top1 属于 `connector` 层但对应连接器未连接。
- 任务描述里有明确的技能点名，而它不在 top1。

改选后仍要 `route` 一次并在 `--exclude` 里排除误配项，让否决动作留在 trace 里，否则回溯时看不到这一步判断。

---

## 4. 多步任务的路由

一个目标拆成多个子任务，**每个子任务单独 route 一次**，不要一次性规划死整条链路。

```
目标: 调研 → 成文 → 配图 → 发布
  route "检索 X 的最新进展"        → tavily        c001
  route "把要点写成公众号文章"      → wechat-publisher c002
    switch --to drawio-skill --keep-open   （c002 挂起）
    route "画一张架构图"           → drawio-skill  c003
    call resume c002                       （回到 c002，上下文原样）
  route "发布到草稿箱"             → wechat-publisher c004
```

嵌套用 `--parent` 或依赖栈顶自动继承，调用树在 `trace` 里以缩进呈现。

并行子任务：各自 `call open`，用不同的上下文 key 写回，避免互相覆盖。

---

## 5. 失败与降级

`call close --status failed` 之后按顺序尝试：

1. **同技能重试**：参数或输入有问题 → `switch --kind retry`，改 inputs 再 `call open`。
2. **换备选技能**：`route <同一任务> --exclude <失败技能>` → `switch --kind fallback`。
3. **升级**：任务超出技能能力边界 → `switch --kind escalate` 到更通用的技能，或拆得更细重新路由。
4. **远程获取**：本地全都不合适 → 走 `remote-acquisition.md`。
5. **回滚**：产出已污染后续步骤 → `switch --kind rollback`，`ctx del` 掉坏槽位，从更早的调用重放。

每一步都要写 `--reason`。trace 里看不到原因的切换等于没有可回溯性。

连续 3 次切换仍未推进时停下，向用户说明卡点，不要继续自动打转。

---

## 6. 反模式

| 反模式 | 后果 | 正确做法 |
|---|---|---|
| 不 `route` 直接 `call open` | 决策依据丢失，回溯时不知道为什么选它 | 任何调用前先 route，哪怕结果显而易见 |
| 把大段原文塞进上下文总线 | context.json 膨胀，读取成本失控 | 落文件，总线只放路径 |
| 用技能名给上下文 key 命名 | 换技能就要改所有读取方 | 按阶段命名（`research.*`） |
| 切换时新建会话 | 上下文与调用链断裂，等于重启 | 用 `switch`，会话始终唯一 |
| 失败后静默换技能 | 审计链缺环 | 先 `call close --status failed`，再 `switch --kind fallback --reason ...` |
| 为了「自动化」把阈值压到 0 | 误配被当成正确路由执行 | 保留 confirm 通道，高风险动作强制 manual |
