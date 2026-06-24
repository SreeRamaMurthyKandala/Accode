# Accode

An in-house AI coding agent. Accode is a generic coding agent — like Copilot or
Claude Code — with a built-in, first-class toolset for migrating Hive/Hadoop
repositories to Google Cloud (BigQuery + GCS + Airflow).

The control flow is LLM-driven: the model picks tools in a loop instead of
following a hard-coded pipeline. Migration is one workflow among many — the
same loop runs generic coding tasks too.

## How it works

```
   user goal
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  AGENTIC LOOP  (accode/agent/loop.py)                         │
│                                                               │
│   while not done:                                            │
│     model picks tool(s)  ──▶  permission gate  ──▶  run tool  │
│            ▲                                          │       │
│            └──────────────  tool result  ◀────────────┘       │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌─ Tool registry ───────────────────────────────────────────────┐
│  Generic coding tools                                         │
│    read_file  write_file  edit_file  list_files               │
│    search_text  run_bash                                      │
│                                                               │
│  Hive → GCP migration toolset                                 │
│    migration_discovery    migration_convert                   │
│    migration_syntax_check migration_bq_setup                   │
│    migration_bq_validate  migration_run_tests                  │
│    migration_fix          migration_report                    │
└────────────────────────────────────────────────────────────────┘
```

The loop itself is ~90 lines and entirely tool-agnostic. Stage ordering lives
in the system prompt (`accode/agent/prompt.py`) as a workflow the model
follows.

## The migration toolset

Each tool wraps one stage of the migration pipeline. The conversion engine
(scanner, 8 per-type converters, BigQuery validator, syntax checker, test
runner, fix agent, reporter) lives under `accode/engine/`.

| Tool                     | Stage                                        |
|--------------------------|----------------------------------------------|
| `migration_discovery`    | Scan repo, classify files                    |
| `migration_convert`      | Convert files (HQL → BQ SQL, etc.)           |
| `migration_syntax_check` | bash/python parse checks on converted code   |
| `migration_bq_setup`     | Create datasets, run DDL                     |
| `migration_bq_validate`  | BigQuery dry-run on converted SQL            |
| `migration_run_tests`    | Run pytest on migrated tests                 |
| `migration_fix`          | Send failures back to the model for repair   |
| `migration_report`       | Generate `MIGRATION_REPORT.md`               |

- **Run them in order** to migrate a whole repo.
- **Run one on its own** — "just discover what's in this repo", "only dry-run
  the SQL" — and you get that single stage.

Shared state between tools (conversion results, DDL paths) is persisted to
`.accode_state.json` in the output directory: `migration_convert` writes it,
`migration_bq_validate` / `migration_run_tests` update it, and
`migration_report` reads it.

## Install

```bash
cd Accode
pip install -r requirements.txt          # or: pip install -e .
cp config.example.yaml config.yaml       # then edit
export ANTHROPIC_API_KEY=sk-ant-...       # or set anthropic.api_key in config
```

GCP credentials are only needed for the BigQuery stages (`migration_bq_setup`,
`migration_bq_validate`); everything else runs without them.

## Usage

```bash
# one-off task
python -m accode "convert the hive repo at ./hive-sample to GCP and test it"

# a single stage
python -m accode "run discovery on ../some-hive-repo and tell me what's there"

# generic coding, no migration
python -m accode "find every TODO in this project and list them"

# interactive session
python -m accode

# unattended (auto-approve every tool call)
python -m accode --yes "migrate ./legacy-hive"
```

If installed with `pip install -e .`, the `accode` command works directly.

## Web frontend

A browser chat UI over the same agent loop — type a goal, watch tool calls and
results stream in live, and approve permission prompts inline.

```bash
pip install -r frontend/requirements.txt
python frontend/server.py --config config.yaml --cwd .
# then open http://127.0.0.1:8730   (or run frontend/start.bat on Windows)
```

The synchronous agent loop runs in a background thread per chat session and
emits events (assistant text, tool calls, tool results) into a queue; the
server streams them to the browser over SSE. Permission prompts travel the
other way — the loop thread blocks until the browser POSTs a decision.
`--cwd` sets the directory the agent operates in; `--host` / `--port` change
the bind address (default `127.0.0.1:8730`, local-only).

## Permissions

Read-only tools (`read_file`, `list_files`, `search_text`, `migration_discovery`,
`migration_syntax_check`, `migration_bq_validate`, `migration_run_tests`,
`migration_report`) run automatically. Tools that modify files or create cloud
resources (`write_file`, `edit_file`, `run_bash`, `migration_convert`,
`migration_bq_setup`, `migration_fix`) ask first. Override per tool under
`agent.permissions` in config, or approve everything with `--yes`.

## Project layout

```
accode/
├── cli.py                 CLI entrypoint (python -m accode)
├── config.py              one config feeds the loop and the engine
├── context.py             Context passed to every tool
├── state.py               .accode_state.json — shared state between tools
├── paths.py               converter map + source/output path helpers
├── agent/
│   ├── loop.py            the agentic loop
│   ├── llm.py             Anthropic tool-use client
│   ├── prompt.py          system prompt (the canonical migration workflow)
│   ├── registry.py        assembles all tools
│   ├── permissions.py     allow / ask / deny gate
│   └── tooling.py         Tool + ToolResult definitions
├── tools/
│   ├── generic/           read, write, edit, list_files, search, bash
│   └── migration/         the 8 decomposed orchestrator stages
└── engine/                vendored conversion engine (unchanged)
    ├── scanner.py  validator.py  syntax_checker.py  test_runner.py
    ├── fix_agent.py  reporter.py  llm.py
    └── converters/        8 per-file-type converters

frontend/                  web chat UI — FastAPI + SSE + a vanilla-JS SPA
├── server.py              session manager, SSE event stream, permission relay
└── static/                index.html, app.js, styles.css
```

## Adding a tool

1. Create a module that exposes a `TOOL = Tool(...)` (see `tools/generic/read.py`
   for the shape — name, description, JSON input schema, handler, permission).
2. Add it to the relevant `__init__.py` list (`GENERIC_TOOLS` / `MIGRATION_TOOLS`).
3. If the model needs to know *when* to use it, mention it in the system prompt.

The loop, registry and permission gate pick it up automatically.

## Known limitations (v1)

- Not token-streamed — in the web UI tool calls and results appear live as
  they happen, but the model's narration arrives per turn, not token by token.
- No context compaction — very long sessions will grow the message history;
  `agent.max_steps` is the current backstop.
- The fix loop's "max 3 rounds, stop on no progress" cap is a system-prompt
  instruction, not a hard counter. A future version can re-add a hard guard.
- `run_bash` uses the system shell with no sandboxing — keep it on `ask`.
