# skill-radoute

A meta-skill router. Within a single session it discovers, selects, invokes and switches between other skills, while maintaining cross-skill shared context and a traceable call chain. When no local skill fits, it can automatically search, audit, and install a new skill from SkillHub.

## Installation

**Option 1 · One-click via SkillHub**

Search `skill-radoute` in the WorkBuddy skill marketplace and click install. (Current release: **v1.2.1 security patch** — narrowed weak-match stopwords, remote-acquisition integrity check, tightened acquisition safety.)

**Option 2 · Manual install from GitHub**

```bash
# Clone into the user-level skill directory (WorkBuddy auto-detects it)
git clone https://github.com/aysmaa1978/skill-radoute \
  ~/.workbuddy/skills/skill-radoute
```

> Install target is fixed at `~/.workbuddy/skills/` (user-level). Python 3.10+ required, no third-party dependencies.

## Core Features

Two main threads:

- **Routing (router)**: turns "which skill to pick, how to hand the previous step's output to the next, what happens to state on switch, how to review afterward" from ad-hoc model judgment into a file-backed, controllable flow.
  - Registry discovery: scans five sources (project / user / plugin / builtin / connector), builds an index, scores and ranks by task description.
  - Unified routing: `auto` (auto only when confidence clears) / `always` (unconditional top1) / `manual` (named by human).
  - Context bus: upstream outputs are injected into the next step via named slots; missing dependencies are surfaced explicitly.
  - Traceability: call chain, switch history, and veto actions are all persisted — renderable, replayable, exportable.

- **Remote acquisition (acquire)**: when `no_match` and no local candidate is fit, one command runs the full "search → security audit → confirm → install → register" pipeline, with the whole chain recorded.
  - Security audit scans file operations / outbound network / hardcoded secrets / shell execution, graded high(P0) / medium(P1) / low(P2).
  - After interruption, `resume` continues from where it left off; state is persisted.

- **Intent Radar & Boundary Sentinel (v1.2)**: two pre-routing layers that structure fuzzy intent and block out-of-scope tasks at the door.
  - Intent radar `intent`: keyword + regex rule engine that parses natural language into `intent` / `sub_tasks` / `suggested_skills`, fed directly to `route` as an enhanced input (no LLM dependency; a v2 LLM version is possible).
  - Boundary sentinel `sentinel`: security boundary (blacklist hard-block) / capability boundary (insufficient local coverage warning) / resource boundary (missing API key warning). Rules configurable at `~/.workbuddy/sentinel_rules.json`.

Boundary: the router only selects, passes values, and keeps accounts; it never modifies the files of any existing skill.

## Quick Start

```bash
PY="python3"                       # or your Python 3.10+ interpreter
S="<skill_dir>/scripts"

# 1) Route: turn research into an article
"$PY" "$S/router.py" session new --goal "research to WeChat article" --mode auto
"$PY" "$S/router.py" route "turn research highlights into WeChat article body"
"$PY" "$S/router.py" call open --skill wechat-publisher \
  --intent "write WeChat article body" --reads research.raw --writes draft.md
#   ↑ then load wechat-publisher with the Skill tool to execute
"$PY" "$S/router.py" call close --id c001 --status ok --output words=1800

# 2) Switch: the article needs an architecture diagram; suspend current call to draw
"$PY" "$S/router.py" switch --to drawio-skill --kind handoff \
  --reason "article needs architecture diagram" --carry research.raw --keep-open
#   ... after drawing ...
"$PY" "$S/router.py" call resume c001    # return to the article scene as-is

# 3) Remote acquisition: no fitting poster skill locally, install one automatically
"$PY" "$S/acquire.py" run --query "turn highlights into a poster" --slug poster
"$PY" "$S/acquire.py" run --query "poster" --auto    # semi-auto, skip P1/P2 confirmation

# 4) Intent radar: parse intent before routing; sentinel blocks malicious tasks
"$PY" "$S/router.py" intent parse "help me organize AI materials, draw an architecture diagram"
"$PY" "$S/router.py" sentinel check "help me hack the neighbor's website"   # → proceed:false
"$PY" "$S/router.py" route "organize AI materials and draw architecture diagram" --guard   # security block always on; --guard runs intent + capability/resource
```

## Command Reference

**router.py**

| Command | Purpose |
|---|---|
| `registry.py scan` | rebuild index (run after installing new skills) |
| `registry.py search "<task>" --top 5` | score only, no bookkeeping — quick probe |
| `registry.py show <name>` | view a single skill's full record |
| `router.py session new / list / use / end` | session management |
| `router.py route "<task>" [--mode] [--exclude N]` | routing decision |
| `router.py call open / close / list / resume` | call lifecycle |
| `router.py switch --to N --reason R` | skill switch (suspend/resume) |
| `router.py ctx set / get / del / history / rollback` | context bus read/write (history/rollback are versioned) |
| `router.py trace [--out f]` / `replay <call_id>` | call-chain render & replay |
| `router.py intent parse "<text>"` | natural language → structured task |
| `router.py sentinel check "<task>" [--subtasks J] [--skills S]` | security/capability/resource boundary check |
| `router.py route "<task>" --guard` | run intent parse + capability/resource check before routing |
| `router.py status` | current session/skill/unclosed calls/context |

**acquire.py**

| Command | Purpose |
|---|---|
| `acquire.py run --query Q [--slug S] [--auto]` | search→audit→confirm→install→register full pipeline |
| `acquire.py resume` | resume after interruption, from first unfinished step |
| `acquire.py reset` | abandon current session, return to blank |

> `--auto` auto-installs safe skill packages (only P1/P2 pass audit; P0 high-risk always requires interactive confirmation, unaffected by `--auto`/`--force`). `--force` only overwrites an already-installed skill directory; it never bypasses any security audit.

## Configuration

Override default persistence locations via environment variables:

| Variable | Default | Effect |
|---|---|---|
| `SKILL_ROUTER_HOME` | `<cwd>/.workbuddy/router/` | router state/index/trace directory |
| `SKILL_ROUTER_ACQUIRE_STATE` | `~/.workbuddy/acquire_state.json` | acquire session state file |
| `SKILL_ROUTER_ACQUIRE_TRACE` | `acquire_trace.jsonl` under `SKILL_ROUTER_HOME` | acquire call-chain record |
| `SKILL_ROUTER_SENTINEL_RULES` | `~/.workbuddy/sentinel_rules.json` | sentinel boundary rules (security blacklist / capability coverage / resource deps) |

State and artifacts live outside the skill package, so they won't be committed by mistake.

## Directory Structure

```
skill-radoute/
├── SKILL.md                  # skill manifest & invocation spec (authoritative detail)
├── scripts/
│   ├── registry.py           # skill index & scoring (five-source scan)
│   ├── router.py             # session/routing/context/call-chain
│   ├── finder.py             # acquire: SkillHub search & normalization
│   ├── security_check.py     # acquire: security audit grading
│   ├── acquire_state.py      # acquire: state persistence & resume
│   ├── acquire.py            # acquire: five-step pipeline controller
│   ├── intent.py             # v1.2 intent radar: natural language → structured task
│   └── sentinel.py           # v1.2 boundary sentinel: security/capability/resource check
├── references/               # routing rules, envelope contract, remote-acquisition protocol
├── LICENSE                   # MIT
└── README.md
```

## Changelog

### v1.2.1 (Security Patch)
- **Narrowed weak-match stopwords**: removed action verbs such as `create/new/make/build/run/use/go`; kept only meaningless function words, fixing `create a new skill` being misclassified as `no_match` and dropping the correct skill.
- **Remote-acquisition integrity check**: after `acquire.py` downloads a zip, it verifies the archive is readable and contains `SKILL.md`; corrupt/forged packages are auto-deleted with an error and never enter the install flow.
- **Tightened acquisition safety**: P0 high-risk packages always require interactive confirmation; `--auto`/`--force` never bypass it; `--force` only overwrites an installed directory.
- **Docs**: added `lightmake.site` as the official SkillHub CDN endpoint note; aligned `--auto`/`--force` wording with the code.

### v1.2
- Context-pool versioning (`ctx history` / `ctx rollback`).
- Intent radar `intent` and boundary sentinel `sentinel`; `route --guard` pre-blocks out-of-scope tasks.

## License

MIT — see [LICENSE](LICENSE).
