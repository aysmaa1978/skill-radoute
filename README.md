# skill-radoute

元技能路由器（meta-skill router）。在一次会话内发现、选择、调用并切换其它 skill，维护跨 skill 的共享上下文与可回溯调用链。

## 解决的问题

把「选哪个技能、怎么把上一步结果交给下一步、切换时状态怎么办、事后怎么复盘」从模型的临时判断，变成有文件记录的可控流程。

- **注册发现**：扫描 project / user / plugin / builtin / connector 五类来源，建索引并按任务描述打分排序。
- **统一路由**：`auto`（置信度达标才自动）／`always`（无条件 top1）／`manual`（人工点名）三种模式。
- **上下文总线**：把上游产出按命名槽位注入下一步，缺失依赖显式暴露。
- **可回溯**：调用链、切换历史、否决动作全部落盘，可渲染、可重放、可导出。

边界：路由器只做选择、传值、记账。任何实际业务发生在被路由到的那个 skill 里，且不修改任何既有 skill 的文件。

## 快速开始

```bash
git clone <repo-url> && cd skill-radoute
PY="python3"                     # 或你的 Python 3.10+ 解释器
"$PY" scripts/registry.py scan    # 建索引（装过新技能后重跑）
"$PY" scripts/router.py session new --goal "你的目标" --mode auto
"$PY" scripts/router.py route "子任务描述"
```

状态目录默认 `<cwd>/.workbuddy/router/`，可用环境变量 `SKILL_ROUTER_HOME` 改写。命令、字段与判定细则见 [SKILL.md](SKILL.md) 和 `references/`。

## 目录结构

```
skill-radoute/
├── SKILL.md                    # 技能清单与调用规范
├── scripts/
│   ├── registry.py             # 技能索引与打分
│   └── router.py               # 会话/路由/上下文/调用链
└── references/                 # 路由规则、封套契约、远程获取
```

## License

MIT — 见 [LICENSE](LICENSE)。
