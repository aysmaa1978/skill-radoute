# skill-radoute

A meta-skill router. Within a single session it discovers, selects, invokes and switches between other skills, while maintaining cross-skill shared context and a traceable call chain. When no local skill fits, it can automatically search, audit, and install a new skill from SkillHub.

## Installation

**Option 1 · One-click via SkillHub**

Search `skill-radoute` in the WorkBuddy skill marketplace and click install. (Current release: **v1.4.0** — CJK short-query boost, stem-leak narrowing, stopword n-gram leak fix, call-chain smoke test.)

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
│   ├── finder.py             # acquire: trusted release-table resolution (GitHub Releases)
│   ├── security_check.py     # acquire: security audit grading
│   ├── acquire_state.py      # acquire: state persistence & resume
│   ├── acquire.py            # acquire: five-step pipeline controller
│   ├── intent.py             # v1.2 intent radar: natural language → structured task
│   ├── sentinel.py           # v1.2 boundary sentinel: security/capability/resource check
│   ├── test_call_chain.py    # call lifecycle smoke test (python3 scripts/test_call_chain.py)
│   └── test_scoring.py       # scoring invariants (CJK normalization/stem gate/stopword leak)
├── references/               # routing rules, envelope contract, remote-acquisition protocol
├── LICENSE                   # MIT
└── README.md
```

## Changelog

### v1.5.0
- **`route --explain` transparent routing report**: new `--explain` flag emits `top_candidates`, `score_breakdown` (per-candidate name/desc/tag breakdown + synonym hit), and `decision_reason` (why auto / confirm / no_match / decompose); when `confirm`, also `missing_trigger` (why not auto-selected). Output via `json.dumps(indent=2)`; the normal routing emit schema is byte-for-byte unchanged (`test_explain.py`, 14 assertions).
- **Lightweight semantic matching (synonym lift)**: `registry.py` adds a preset zh/en synonym table (search↔查找/搜索/检索, draw↔画图/绘制/绘图, write↔撰写/创作/写作); when a query term and a skill's own token fall in the same synonym group, a `SEMANTIC_WEIGHT=0.3` lift applies. Pure data-driven, no embedding model, zero new deps; the lift only fires when the skill itself contains a group member, so unrelated skills gain no semantic score (no false positives).
- **Weak-match guard (no_match revival)**: `router.py` adds a weak-match guard — when top1's score is < 0.5 or all its match reasons are single-CJK/stopword tokens, the decision becomes `no_match` and the caller falls through to the remote-acquisition flow, so out-of-scope input is no longer misrouted as a confirmable wrong skill. The guard's stopword list is narrowed (only function words kept; action verbs like create/new/make removed), which eliminates the `create a new skill` false-negative that dropped the correct skill. Across 54 real regression cases: 2 out-of-scope inputs correctly revived to no_match, 0 false negatives (100% determinism, 3 identical rounds).
- **(Yunding security fix) Trusted download source**: removed the unverified `lightmake.site` CDN; `finder.py` now resolves from a trusted release table `TRUSTED_RELEASES` by slug + explicit version, and the download URL always uses signed GitHub Releases (`GITHUB_RELEASE`). GitHub Releases is the trusted fallback until SkillHub's official registry endpoint is available.
- **(Yunding security fix) SHA256 hash verification**: after download, `acquire.py` compares the zip's SHA256 against the hardcoded `KNOWN_SKILLS` expected value (never fetched from the network). Packages without a preset hash, or with a hash mismatch, are deleted and aborted — they never enter the install flow.
- **(Yunding security fix) Version pinning + human confirmation**: acquisition requests must carry an explicit version (no `latest` slug-only resolution); P0 high-risk packages always require interactive confirmation (`--auto`/`--force` never bypass); `--auto` only auto-installs skills that are both hash-preset and version-locked, everything else degrades to manual confirmation.
- Acceptance: 54-case full regression shows zero decision diff vs the v1.4 baseline, clear-set auto rate holds at 54.8%; new-skill download goes through GitHub Releases + hash check; preset-hash skills install, non-preset abort with "请联系作者更新"; Yunding re-scan clears all three findings.

### v1.4.0
- **CJK short-query boost**: the normalization denominator now uses `query_mass()` (Latin word = 1, every 2 CJK chars = 1), fixing the root cause where n-gram expansion made Chinese queries score ~35% lower than synonymous English ones; 8 real Chinese queries gained 1.21x–1.45x (e.g. "draw architecture diagram" 5.68→7.64).
- **Stem-leak narrowing**: added `_stem_match()`, requiring the shared prefix to cover >50% of the longer word, fixing false matches like `data~database` / `auto~automation` / `mark~marketplace` / `word~wordpress` that inflated wrong-skill scores.
- **Stopword n-gram leak fix**: expanded the function-word stop list and drop whole stopword strings inside `bump()`, fixing pure-syntax fragments like `帮我遛狗` / `我想学游泳` crossing the 0.5 auto floor; top out-of-scope score dropped from 2.49 to 0.81 and is guarded into `confirm` instead of a wrong `auto` (the 0.5 threshold itself is unchanged).
- **Call-chain smoke test**: added `test_call_chain.py` (16 assertions covering open/close/switch/resume and stack state) that catches d449b38-class silent regressions; `test_scoring.py` locks 14 scoring invariants.
- Regression: across 54 full-suite cases only 1 diff, and it is an improvement; clear-set auto rate 51.6%→54.8%, zero degradation.

### v1.3.0
- **Sibling disambiguation**: when top1 and top2 share the same tier and overlapping name prefixes (same family), the decision is forced to `confirm` instead of being decided by score, with `reason` tagged `[SIBLING]`. Fixes same-family skills (PowerPoint / tencent-doc / edit-word / weixin-pay) being auto-selected incorrectly.
- **Multi-intent detection**: `route` now always runs `intent.parse`; when ≥2 distinct task types are parsed it returns `decompose` with a `sub_task_plan` (per-subtask type + suggested skills) instead of blindly taking top1. Multi-intent is a property of the query, independent of candidate strength, so it outranks `auto` / `no_match` / weak-match; `[SIBLING]` still has the highest priority.
- Fixed the stack-pop logic in the `call close` command (regression introduced in d449b38).

### v1.2.1 (Security Patch)
- **Narrowed weak-match stopwords**: removed action verbs such as `create/new/make/build/run/use/go`; kept only meaningless function words, fixing `create a new skill` being misclassified as `no_match` and dropping the correct skill.
- **Remote-acquisition integrity check**: after `acquire.py` downloads a zip, it verifies the archive is readable and contains `SKILL.md`; corrupt/forged packages are auto-deleted with an error and never enter the install flow.
- **Tightened acquisition safety**: P0 high-risk packages always require interactive confirmation; `--auto`/`--force` never bypass it; `--force` only overwrites an installed directory.
- **Docs**: aligned `--auto`/`--force` wording with the code (note: the `lightmake.site` source was removed in v1.5 in favor of signed GitHub Releases + SHA256 verification).

### v1.2
- Context-pool versioning (`ctx history` / `ctx rollback`).
- Intent radar `intent` and boundary sentinel `sentinel`; `route --guard` pre-blocks out-of-scope tasks.

## License

MIT — see [LICENSE](LICENSE).
