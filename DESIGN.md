# Accode — Design Document

**Version:** 0.1
**Status:** Implemented
**Last updated:** 2026-05-20

---

## 1. Executive Summary

**Accode** is an in-house AI coding agent. It is a generic coding agent — in
the family of GitHub Copilot or Claude Code — with a built-in, first-class
toolset for migrating Hive/Hadoop repositories to Google Cloud (BigQuery +
GCS + Airflow).

Accode is the successor to **`hive-to-gcp-agent`**. That project solved one
problem well, but as a *fixed pipeline*: a Python `Orchestrator` class ran
`scan → convert → validate → fix` in a hard-coded sequence, calling the LLM
only as a stateless translator inside each stage. Accode keeps the proven
conversion logic but **inverts the control flow**:

- In `hive-to-gcp-agent`, **Python code decided** what happened next.
- In Accode, **the LLM decides**, by choosing tools inside an agentic loop.

The migration capability is preserved by **decomposing the orchestrator's
stages into independent tools** the agent composes itself. Run them in the
canonical order and the full original pipeline is reproduced; run one alone
and you get a single-stage workflow.

Accode exposes two surfaces over one shared core: a **CLI** and a **web chat
UI**.

### 1.1 Design goals

1. **Generic first.** Accode is a coding agent, not only a migration tool.
   It has the baseline file/search/shell tools every coding agent needs.
2. **Preserve the proven migration capability.** The `hive-to-gcp-agent`
   conversion engine — scanner, eight per-type converters, BigQuery
   validator, fix agent, reporter — is reused *unchanged*.
3. **Decompose, do not duplicate.** The orchestrator is dissolved into tools.
   Its hard-coded ordering moves into the system prompt as instructions.
4. **One core, many surfaces.** The agentic loop is UI-agnostic: it emits
   structured events and asks for permission through pluggable interfaces,
   so the CLI and the web UI share the exact same loop.
5. **Safe by default.** Read-only tools run automatically; tools that mutate
   the filesystem or create cloud resources require approval.
6. **Cost-aware.** Prompt caching on the system prompt and tool schemas;
   token/cost accounting surfaced at the end of every run.

### 1.2 Non-goals (v1)

- Not a hosted, multi-tenant service. The web UI is a local, single-user tool.
- No token-by-token streaming — events stream, narration arrives per turn.
- No context compaction — `agent.max_steps` is the backstop for long runs.
- The fix-loop round cap is a *system-prompt instruction*, not a hard counter.

---

## 2. Background: From Orchestrator to Agent

The single most important design decision is the **inversion of control**.

```
  hive-to-gcp-agent  (fixed pipeline)         Accode  (agentic loop)
  ───────────────────────────────────         ─────────────────────────────

  Orchestrator.run():                         run_agent(goal):
    scan()                                      while not done:
    for f in files: convert(f)  ◀── LLM           response = LLM.turn(tools)
    syntax_check()                                for call in response:
    if fail: fix()              ◀── LLM             result = dispatch(call)
    bq_setup(); bq_validate()                     feed results back
    if fail: fix()              ◀── LLM
    run_tests()                                  the LLM chooses each step;
    if fail: fix()              ◀── LLM          tools are the only way it
    report()                                     can act on the world

  Python decides the order.                    The model decides the order.
  LLM is a translator inside a stage.          LLM is the driver.
```

In the old design the ordering, the looping, the "if it failed, fix it" —
all of it — lived in Python. In Accode that intelligence lives in three
places instead:

- the **model** (it reasons about what to do next),
- the **system prompt** (it encodes the canonical migration workflow),
- the **tools** (each one validates its own preconditions).

This is what makes Accode *general*: the same loop that runs a migration can
also be told "find every TODO in this project" — it just picks different
tools.

---

## 3. System Context

```
                            ┌───────────────────────────┐
   ┌─────────────┐          │          ACCODE           │
   │   Human     │          │                           │
   │  - CLI      │ ───────▶ │   ┌───────────────────┐   │
   │  - Web UI   │          │   │   Agentic loop    │   │
   └─────────────┘          │   └─────────┬─────────┘   │
                            │             │             │
                            │   ┌─────────▼─────────┐   │
   ANTHROPIC_API_KEY ──────▶│   │  Anthropic API    │   │
                            │   │  (Messages, tool  │   │
                            │   │   use, caching)   │   │
                            │   └─────────┬─────────┘   │
                            │             │             │
                            │   ┌─────────▼─────────┐   │
   GCP credentials  ───────▶│   │  BigQuery API     │   │  (migration only)
   (ADC / SA JSON)          │   │  (dry-run + DDL)  │   │
                            │   └─────────┬─────────┘   │
                            │             │             │
                            │   ┌─────────▼─────────┐   │
   Local filesystem ◀──────▶│   │  Tools (read /    │   │
   (repos, output)          │   │   write / shell)  │   │
                            │   └───────────────────┘   │
                            └───────────────────────────┘
```

| Dependency           | Purpose                                          | Required?                  |
|----------------------|--------------------------------------------------|----------------------------|
| Anthropic API        | The agent loop and the migration converters      | **Yes**                    |
| Google BigQuery API  | `migration_bq_setup` / `migration_bq_validate`   | Optional (migration only)  |
| `bash` on `PATH`     | `bash -n` checks in `migration_syntax_check`     | Optional (skipped if absent)|
| `pytest`             | `migration_run_tests`                            | Optional                   |
| FastAPI + uvicorn    | The web frontend                                 | Optional (CLI needs neither)|

---

## 4. High-Level Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                              SURFACES                                 │
│                                                                       │
│   CLI (accode/cli.py)              Web frontend (frontend/server.py)   │
│     ConsoleSink                      QueueSink + SSE                   │
│     ConsolePrompter                  blocking web prompter             │
└───────────────────────────────┬───────────────────────────────────────┘
                                 │  both call the same function
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│                  THE AGENTIC LOOP   (accode/agent/loop.py)            │
│                                                                       │
│   run_agent(goal, ctx, llm, gate, sink, messages, cancel_check)       │
│                                                                       │
│     AgentLLM ──── Anthropic Messages API (tool use + prompt caching)  │
│     PermissionGate ──── allow / ask / deny                            │
│     EventSink ──── structured events out to whichever surface         │
└───────────────────────────────┬───────────────────────────────────────┘
                                 │  dispatches tool calls into
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│                   TOOL REGISTRY   (accode/agent/registry.py)          │
│                                                                       │
│  Generic coding tools (accode/tools/generic/)                         │
│    read_file   write_file   edit_file   list_files   search_text      │
│    run_bash                                                           │
│                                                                       │
│  Hive → GCP migration tools (accode/tools/migration/)                 │
│    migration_discovery    migration_convert    migration_syntax_check │
│    migration_bq_setup     migration_bq_validate                       │
│    migration_run_tests    migration_fix        migration_report       │
└───────────────────────────────┬───────────────────────────────────────┘
                                 │  migration tools wrap
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│              VENDORED CONVERSION ENGINE   (accode/engine/)            │
│                                                                       │
│   scanner   converters/ (8)   validator   syntax_checker              │
│   test_runner   fix_agent   reporter   llm                            │
│                                                                       │
│   Copied unchanged from hive-to-gcp-agent. The orchestrator is the    │
│   ONLY file that did not come across — it was dissolved into tools.   │
└───────────────────────────────────────────────────────────────────────┘
```

Four layers, each ignorant of the one above it:

- **Surfaces** turn a human into a `goal` string and render events back.
- **The loop** turns a `goal` into a sequence of tool calls via the LLM.
- **Tools** are the only way the loop can affect the world.
- **The engine** is the proven migration logic the migration tools wrap.

---

## 5. The Agentic Loop

`accode/agent/loop.py` — function `run_agent()`. This is the heart of Accode
and it is deliberately small (~115 lines). It is completely tool-agnostic:
it never special-cases a tool by name.

### 5.1 Signature

```python
def run_agent(
    goal: str,                      # the user's request for this turn
    ctx: Context,                   # cfg + working directory + console
    llm: AgentLLM,                  # the tool-use Anthropic client
    gate: PermissionGate,           # allow / ask / deny policy
    sink: EventSink,                # where structured events go
    messages: list | None = None,   # prior conversation, for multi-turn
    cancel_check: Callable | None = None,   # cooperative cancellation
) -> list:                          # returns updated messages
```

### 5.2 Flow

```
   run_agent(goal, ...)
         │
         │  if messages ends on a user message → start fresh thread
         │  append { role: "user", content: goal }
         ▼
  ┌────────────────────────── STEP LOOP (max_steps, default 60) ──────────┐
  │                                                                       │
  │   cancel_check() truthy? ──yes──▶ emit "notice" (cancelled) ─▶ break  │
  │        │ no                                                           │
  │        ▼                                                              │
  │   response = llm.turn(system, tool_schemas, messages)                 │
  │        │   └─ Anthropic Messages API: system prompt + 14 tool schemas │
  │        │      + full message history, prompt-cached                   │
  │        ▼                                                              │
  │   append { role: "assistant", content: response.content }             │
  │   for each text block:  sink.emit("assistant_text", ...)              │
  │        │                                                              │
  │        ▼                                                              │
  │   stop_reason == "tool_use" ?                                         │
  │        │ no  ─────────────────────────────────────────────▶ break    │
  │        │ yes                                                          │
  │        ▼                                                              │
  │   for each tool_use block in the response:                            │
  │        │                                                              │
  │        │   sink.emit("tool_call", id, name, input, label)             │
  │        │                                                              │
  │        │   tool = registry[name]      (unknown → error result)        │
  │        │                                                              │
  │        │   gate.check(tool, label)                                    │
  │        │      ├─ deny  ──▶ tool_result(is_error=True, reason)          │
  │        │      └─ allow ──▶ result = tool.handler(input, ctx)           │
  │        │                   (exceptions caught → error result)         │
  │        │                                                              │
  │        │   sink.emit("tool_result", id, name, ok, preview)            │
  │        │   collect { type: "tool_result", tool_use_id, content }      │
  │        ▼                                                              │
  │   append { role: "user", content: [ all tool_results ] }              │
  │        │                                                              │
  │        └──────────────────────── loop ────────────────────────────────┘
  │                                                                       │
  └───────────────────────────────────────────────────────────────────────┘
         │
         │  if the loop exhausted max_steps → emit "notice" (step cap)
         ▼
   sink.emit("turn_done", usage = llm.usage_summary())
   return messages
```

### 5.3 Why this shape

| Decision                                       | What it buys                                            |
|------------------------------------------------|---------------------------------------------------------|
| One flat `while`-style step loop               | Trivial to reason about, debug, and test.               |
| Every tool_use block answered with a tool_result| Satisfies the Anthropic API contract; no dangling calls.|
| All tool exceptions caught in the loop         | A buggy tool degrades to an error message, never a crash.|
| `max_steps` cap                                | Hard bound on a runaway loop and on cost.               |
| `cancel_check` polled per step                 | Cooperative cancellation (used by the web "Stop" button).|
| Sink + gate are injected, not hard-coded       | The same loop serves the CLI and the web UI unchanged.  |
| `messages` returned                            | An interactive session can continue the conversation.   |

### 5.4 `AgentLLM` — the tool-use client

`accode/agent/llm.py`. A thin wrapper over `anthropic.Anthropic`, distinct
from the engine's single-shot `LLMClient`:

- **Prompt caching.** The system prompt and the last tool schema are sent
  with `cache_control: ephemeral`. Every turn after the first re-reads the
  (constant) system prompt + 14 tool definitions at ~10% of input cost.
- **Retries.** 3 attempts — `RateLimitError` backs off 20s × attempt;
  `APIStatusError` / `APIConnectionError` back off 5s.
- **Accounting.** Accumulates input / output / cache-read / cache-write
  tokens; `usage_summary()` renders a token + estimated-cost line.

---

## 6. The Tool System

### 6.1 The `Tool` abstraction

`accode/agent/tooling.py`. Every capability — generic or migration — is one
`Tool`:

```python
@dataclass
class Tool:
    name: str                    # e.g. "migration_convert"
    description: str             # the model reads this to decide WHEN to use it
    input_schema: dict           # JSON Schema for the arguments
    handler: Callable            # (input: dict, ctx: Context) -> ToolResult
    permission: str              # "allow" | "ask" | "deny"
```

A handler returns a `ToolResult(content: str, is_error: bool)`. The loop
never inspects a tool beyond these fields — adding a tool requires no change
to the loop.

### 6.2 The registry

`accode/agent/registry.py` collects `GENERIC_TOOLS + MIGRATION_TOOLS` into a
list and a `name → Tool` dict. The loop passes `[t.api_schema() for t in
registry]` to the API and dispatches `tool_use` blocks through the dict.

### 6.3 The 14 tools

| Tool                     | Permission | Purpose                                              |
|--------------------------|------------|------------------------------------------------------|
| `read_file`              | allow      | Read a file with line numbers (offset/limit).        |
| `write_file`             | ask        | Create or overwrite a file.                          |
| `edit_file`              | ask        | Exact-string replace inside a file.                  |
| `list_files`             | allow      | Glob for files (`**` supported).                     |
| `search_text`            | allow      | Regex search across files.                           |
| `run_bash`               | ask        | Run a shell command in the working directory.        |
| `migration_discovery`    | allow      | Scan + classify a Hive repo.                         |
| `migration_convert`      | ask        | Convert Hive files to GCP equivalents.               |
| `migration_syntax_check` | allow      | `bash -n` / `compile()` on converted output.         |
| `migration_bq_setup`     | ask        | Create BQ datasets + execute DDL (real resources).   |
| `migration_bq_validate`  | allow      | BigQuery dry-run validation (no cost, no data).      |
| `migration_run_tests`    | allow      | Run the converted pytest suite.                      |
| `migration_fix`          | ask        | Repair one converted file from an error.             |
| `migration_report`       | allow      | Render `MIGRATION_REPORT.md`.                        |

The rule for defaults: **reads and read-only checks are `allow`; anything
that writes files or creates cloud resources is `ask`.** Config can override
any of them per tool.

---

## 7. The Migration Toolset — the Orchestrator Decomposed

This section is the answer to the central question: *how does a fixed
pipeline become a set of tools, and still behave like a pipeline?*

### 7.1 The decomposition

Each migration tool wraps exactly one stage of the original
`Orchestrator.run()`:

| Original orchestrator stage           | Accode tool              | Side effects        |
|---------------------------------------|--------------------------|---------------------|
| `scan()`                              | `migration_discovery`    | none (read-only)    |
| `_process_file()` conversion loop     | `migration_convert`      | writes `migrated/`  |
| `_run_syntax_checks()`                | `migration_syntax_check` | none                |
| `ensure_datasets()` + `execute_ddl()` | `migration_bq_setup`     | creates BQ objects  |
| `validate_only()` (BQ dry-run)        | `migration_bq_validate`  | writes an error log |
| `_run_tests()`                        | `migration_run_tests`    | runs pytest         |
| `FixAgent.fix_file()`                 | `migration_fix`          | rewrites one file   |
| `Reporter` + `_patch_report()`        | `migration_report`       | writes the report   |

The orchestrator's `_run_fix_rounds_*` methods — the bounded retry loops —
do **not** become a tool. `migration_fix` is *atomic*: one file, one error,
one pass. The round-based retry is now driven by the agent: call a validation
tool, fix each failure, re-run the validation tool. The "≤3 rounds, stop on
no progress" rule moved from Python into the system prompt.

### 7.2 Replacing in-memory state with a state file

The orchestrator threaded results between stages as in-memory attributes
(`results`, `_ddl_output_paths`). Independent tools cannot share memory, so
Accode persists the equivalent state to **`<output_dir>/.accode_state.json`**:

```
  migration_convert      ── writes ──▶  files[]  (per-file conversion status)
  migration_bq_validate  ── updates ──▶ per-file validation_passed + bq{}
  migration_run_tests    ── updates ──▶ tests{}
  migration_report       ── reads ────  everything, renders the report
```

State schema (`accode/state.py`):

```json
{
  "repo_path":  "...",
  "output_dir": "...",
  "files": [
    { "rel_path", "file_type", "output_path", "status",
      "notes": [], "validation_passed": true|false|null,
      "validation_error": "..."|null }
  ],
  "bq":    { "passed": 0, "failures": [["rel", "error"], ...] },
  "tests": { "ran": true, "passed": 0, "failed": 0, "errors": 0,
             "failures": [["rel", "excerpt"], ...] }
}
```

The filesystem *is* the bus — which is how the orchestrator already worked
(later stages read `migrated/` from disk). `.accode_state.json` simply makes
the structured part of that handoff explicit and tool-independent.

### 7.3 The canonical migration workflow

When the user asks to migrate a Hive repo, the model — guided by the system
prompt — drives the tools in this order. **This ordering is not Python
control flow; it is the LLM following instructions, one tool call per step,
inside the agentic loop of §5.**

```
  user: "migrate <repo> to GCP and test it"
              │
              ▼
   ┌──────────────────────┐
   │ migration_discovery  │  scan + classify → 41 targets, 4 apps
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ migration_convert    │  LLM once per file → migrated/
   │                      │  writes .accode_state.json
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐   fail   ┌─────────────────────────────────┐
   │ migration_syntax_    │ ───────▶ │ migration_fix(SYNTAX) per file   │
   │ check                │ ◀─────── │ then re-run syntax_check         │
   └──────────┬───────────┘  re-check│ ≤ 3 rounds, stop on no progress  │
              │                      └─────────────────────────────────┘
              ▼   (skip 4–5 if BigQuery is not configured)
   ┌──────────────────────┐
   │ migration_bq_setup   │  create datasets + execute DDL
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐   fail   ┌─────────────────────────────────┐
   │ migration_bq_        │ ───────▶ │ migration_fix(BQ_DRY_RUN)        │
   │ validate             │ ◀─────── │ then re-run bq_validate          │
   └──────────┬───────────┘ re-valid │ ≤ 3 rounds, stop on no progress  │
              │                      └─────────────────────────────────┘
              ▼   (skip 6 unless the user asked to test)
   ┌──────────────────────┐   fail   ┌─────────────────────────────────┐
   │ migration_run_tests  │ ───────▶ │ migration_fix(PYTEST)            │
   │                      │ ◀─────── │ then re-run run_tests            │
   └──────────┬───────────┘  re-run  │ ≤ 3 rounds, stop on no progress  │
              ▼                      └─────────────────────────────────┘
   ┌──────────────────────┐
   │ migration_report     │  MIGRATION_REPORT.md
   └──────────────────────┘
```

### 7.4 How ordering survives without an orchestrator

The orchestrator *guaranteed* order in Python. An LLM-driven sequence could,
in principle, skip or reorder a step. Three mechanisms keep it correct:

1. **The system prompt** (`accode/agent/prompt.py`) spells out the canonical
   workflow above, verbatim, including the fix-loop discipline and the
   "isolate each fix loop to its own error class" rule.
2. **Tool descriptions** state preconditions and stage numbers — e.g.
   *"STAGE 5 … PRECONDITION: migration_convert must have run."*
3. **Every tool checks its own preconditions** and fails loud with a
   corrective message. `migration_syntax_check` on a missing output
   directory returns *"Output directory does not exist … Run
   migration_convert first."* — which the model reads and acts on.

Determinism softened in exactly one place: the fix-loop round cap is now an
instruction, not a counter. A hard guard can be re-added inside
`migration_fix` if strict cost bounding is later required.

---

## 8. Component Reference

| Module                          | Responsibility                                                       |
|----------------------------------|----------------------------------------------------------------------|
| `accode/cli.py`                  | CLI entrypoint (`click`); one-shot or interactive session.           |
| `accode/config.py`               | Loads `config.yaml`, fills defaults for both the loop and the engine.|
| `accode/context.py`              | `Context` — cfg + working dir + console; passed to every handler.    |
| `accode/state.py`                | `.accode_state.json` load/save.                                      |
| `accode/paths.py`                | Converter map; source↔output path mapping (`lookup_source`).         |
| `accode/agent/loop.py`           | `run_agent()` — the agentic loop.                                    |
| `accode/agent/llm.py`            | `AgentLLM` — tool-use Anthropic client + caching + cost.             |
| `accode/agent/prompt.py`         | The system prompt — generic guidance + canonical migration workflow. |
| `accode/agent/registry.py`       | Assembles all 14 tools.                                              |
| `accode/agent/tooling.py`        | `Tool`, `ToolResult`, permission constants.                          |
| `accode/agent/permissions.py`    | `PermissionGate` — allow/ask/deny + session memory.                  |
| `accode/agent/events.py`         | `EventSink` / `ConsoleSink` / `QueueSink` / `ConsolePrompter`.        |
| `accode/tools/generic/*`         | 6 generic coding tools.                                              |
| `accode/tools/migration/*`       | 8 migration tools (the decomposed orchestrator).                     |
| `accode/engine/*`                | Vendored conversion engine (unchanged).                              |
| `frontend/server.py`             | FastAPI server — sessions, SSE, permission relay.                    |
| `frontend/static/*`              | The chat SPA (`index.html`, `app.js`, `styles.css`).                 |

---

## 9. Events and Surfaces

The loop never prints and never calls `input()`. It **emits events** to an
`EventSink` and asks for permission through a **prompter**. This is the
abstraction that lets one loop serve two surfaces.

### 9.1 Event kinds

| Event             | Fields                          | Emitted when                       |
|-------------------|---------------------------------|------------------------------------|
| `assistant_text`  | `text`                          | the model produces narration       |
| `tool_call`       | `id, name, input, label`        | before a tool runs                 |
| `tool_result`     | `id, name, ok, preview`         | after a tool runs                  |
| `notice`          | `text`                          | max_tokens, step cap, cancellation |
| `error`           | `text`                          | a loop-level exception (web)       |
| `turn_done`       | `usage`                         | the run finished, with usage line  |
| `permission_request` / `permission_resolved` | emitted by the web prompter, not the loop |

### 9.2 The two surfaces

| Concern          | CLI                          | Web frontend                              |
|------------------|------------------------------|-------------------------------------------|
| Sink             | `ConsoleSink` → rich console | `QueueSink` → thread-safe queue → SSE     |
| Prompter         | `ConsolePrompter` → `input()`| blocking web prompter (see §10)           |
| Conversation     | in-process `messages` list   | per-session `messages` list               |
| Concurrency      | synchronous                  | one background thread per session         |

`PermissionGate` owns *policy* (allow/ask/deny + the set of tools the user
said "always" to). The act of *asking a human* is delegated to the prompter —
which is the only thing that differs between surfaces.

---

## 10. The Web Frontend

`frontend/server.py` + `frontend/static/`. A browser chat UI over the same
`run_agent`. The problem it solves: the agent loop is *synchronous and
blocking* (the Anthropic SDK calls block), but a web server is *async*, and
permission prompts need a round-trip to the browser *mid-run*.

### 10.1 Architecture

```
   Browser (SPA)                 FastAPI server                  Loop thread
   ─────────────                 ──────────────                  ───────────
        │                                                             
        │  POST /api/sessions ───▶  Session()  { llm, gate, queue }    
        │  ◀── { session_id } ──────                                   
        │                                                             
        │  EventSource(/stream) ─▶  SSE generator                      
        │                          drains session.events (queue)       
        │                                                             
        │  POST /message {text} ─▶  session.send()                     
        │                            └─▶ spawn daemon Thread ─────────▶│
        │                                                    run_agent()
        │                              QueueSink.emit(...)             │
        │  ◀═══════ SSE events ◀════ queue ◀════════════════════════════┤
        │   (assistant_text, tool_call, tool_result, notice, turn_done) │
        │                                                              │
        │                          ── ask-tool reached ──              │
        │  ◀── permission_request ◀ _prompt() emits, then BLOCKS ◀──────┤
        │       (SSE)                on an answer queue                 │
        │                                                              │
        │  POST /permission ──────▶ answer_permission()                 │
        │       {id, decision}        └─▶ puts decision on answer queue │
        │                                 └─▶ unblocks the loop ───────▶│
        │                                                       continues
        │  ◀── turn_end (SSE) ◀──── finally: emit turn_end ◀─────────────┘
```

### 10.2 Why a thread, not a subprocess

`hive-to-gcp-agent`'s web UI shelled out to `migrate.py` as a subprocess and
parsed its stdout. Accode runs the loop **in a background thread** instead,
because:

- It needs *structured events*, not parsed log lines — a thread sharing a
  `queue.Queue` gives that directly.
- Permission is *interactive mid-run* — a thread can block on a queue and be
  unblocked by an HTTP handler; coordinating that across a subprocess
  boundary is far more fragile.

A loop-level exception is caught and surfaced as an `error` event; a
`cancel_requested` flag (polled by the loop's `cancel_check`) backs the
"Stop" button. A pending permission times out (deny) after 10 minutes so a
closed browser tab cannot strand a thread forever.

### 10.3 Endpoints

| Endpoint                              | Method | Purpose                              |
|---------------------------------------|--------|--------------------------------------|
| `/`                                   | GET    | The chat SPA                         |
| `/static/*`                           | GET    | SPA assets                           |
| `/api/health`                         | GET    | Liveness                             |
| `/api/sessions`                       | POST   | Create a session                     |
| `/api/sessions/{id}/message`          | POST   | Send a user message (starts a turn)  |
| `/api/sessions/{id}/stream`           | GET    | SSE event stream                     |
| `/api/sessions/{id}/permission`       | POST   | Answer a pending permission request  |
| `/api/sessions/{id}/cancel`           | POST   | Request cooperative cancellation     |

Default bind is `127.0.0.1:8730` — local-only, no auth, single user.

---

## 11. Permission Model

`PermissionGate.check(tool, label)` resolves in this order:

```
   config override (agent.permissions.<tool>)  ─or─  tool.permission default
        │
        ├─ "deny"   ──────────────────────────────────▶ refuse
        ├─ "allow"  ──────────────────────────────────▶ run
        └─ "ask"
              ├─ --yes / auto_approve  ────────────────▶ run
              ├─ tool already "always"-ed this session ▶ run
              └─ prompter(tool, label) → once|always|deny
```

A `deny` returns a tool_result with `is_error=True` and a reason; the model
reads it and adapts. "Always" adds the tool to a per-session allow set.

---

## 12. Configuration

One `config.yaml` feeds two consumers (`accode/config.py` fills every
default, so Accode runs with nothing but an API key):

| Section                | Consumed by        | Keys                                            |
|------------------------|--------------------|-------------------------------------------------|
| `anthropic`            | loop **and** engine| `api_key`, `model`, `max_tokens`                |
| `agent`                | the loop           | `model`, `max_tokens`, `max_steps`, `permissions`|
| `gcp`                  | migration engine   | `project`, `region`, `credentials_file`         |
| `dataset_map`          | migration engine   | Hive DB / variable → BigQuery dataset           |
| `path_map`             | migration engine   | HDFS prefix → GCS prefix                        |
| `query_parameter_types`| migration engine   | typed `@param`s for BQ dry-run                  |
| `output`               | migration tools    | `dir`, `overwrite`                              |
| `dag_xml_dirs` / `test_dirs` | scanner      | folder-name heuristics for classification       |

`config.yaml` is git-ignored (it carries the API key); `config.example.yaml`
ships placeholder values.

---

## 13. The Vendored Engine

`accode/engine/` is the `hive-to-gcp-agent/agent/` package copied in
**unchanged**, with two files removed:

- `orchestrator.py` — dissolved into the migration tools and the system
  prompt. This is the whole point of Accode.
- `config.py` — replaced by `accode/config.py` (a superset).

Everything else is byte-for-byte the original, proven code: `scanner.py`,
the eight converters in `converters/`, `validator.py` (BigQuery dry-run with
parameter-type auto-detection), `syntax_checker.py`, `test_runner.py`,
`fix_agent.py`, `reporter.py`, and the single-shot `llm.py`. All internal
imports are relative, so the package moved without edits.

`accode/paths.py` reconstructs the small amount of glue the orchestrator
held: the `FileType → (converter, extension, dags?)` map, the output-path
computation, and `lookup_source` (reverse-mapping a converted file to its
Hive source so `migration_fix` can show the model the original intent).

---

## 14. End-to-End Walkthrough

A real run — `migrate the daily_shipment_ingest app of sample_hive_repo,
skip BigQuery` — as it actually executed:

```
1. The model calls migration_discovery(repo_path=...)
     engine scanner walks the repo → 41 targets, 4 apps
     tool returns a classified breakdown

2. The model reasons: "scope to daily_shipment_ingest" — 12 files
   The model calls migration_convert(repo_path, files=[...12 paths...])
     for each file: pick converter from paths.CONVERTER_MAP
                    converter.convert() → one Anthropic API call
                    write migrated/<mirrored path>
     write .accode_state.json with 12 file entries
     tool returns "12 converted, 0 errors"

3. The model calls migration_syntax_check(output_dir=...)
     engine SyntaxChecker: compile() on 3 .py, bash -n skipped (no bash)
     tool returns "3 python ok, 2 shell not checked" — no failures

4. The model reasons: "nothing to fix" — skips migration_fix

5. The model calls migration_report(output_dir=...)
     load .accode_state.json → FileResult[] → engine Reporter
     write MIGRATION_REPORT.md

6. stop_reason == "end_turn" → loop ends
   sink emits turn_done: ~$0.067, run complete
```

Each numbered item is one or more iterations of the §5 step loop. The
ordering came from the system prompt; the scoping decision came from the
model; the conversions came from the vendored engine. No orchestrator was
involved — and yet the pipeline ran in pipeline order.

---

## 15. Security Considerations

| Concern                       | Mitigation                                                            |
|--------------------------------|-----------------------------------------------------------------------|
| API key leakage                | Read from env or git-ignored `config.yaml`; never logged or committed.|
| Arbitrary file writes          | `write_file` / `edit_file` / `migration_convert` / `migration_fix` are `ask`. |
| Arbitrary command execution    | `run_bash` is `ask`; runs in the configured working directory only.   |
| Real cloud resource creation   | `migration_bq_setup` is `ask`; `ensure_datasets`/DDL are idempotent.  |
| BigQuery cost                  | `migration_bq_validate` is dry-run only — zero bytes scanned.         |
| Runaway loop / cost            | `agent.max_steps` cap; prompt caching; usage surfaced every run.      |
| Web UI exposure                | Binds `127.0.0.1` by default; no auth — intended for local use only.  |
| LLM output trust               | Output is validated (syntax / dry-run / pytest), never auto-executed by the agent. |

---

## 16. Extensibility

**Add a tool.** Create a module exposing `TOOL = Tool(...)` (see
`tools/generic/read.py`), add it to `GENERIC_TOOLS` or `MIGRATION_TOOLS`, and —
if the model must know *when* to use it — mention it in the system prompt.
The loop, registry, and gate pick it up automatically.

**Add a migration stage.** Wrap the new engine logic in a tool, give it a
clear precondition check and description, and add it to the canonical
workflow in `prompt.py`.

**Target another cloud.** Fork the engine's `validator.py` and the
per-converter system prompts; the loop, tools, registry, and surfaces are
cloud-agnostic.

**Add a surface.** Implement an `EventSink` and a prompter; call `run_agent`.
The IDE-plugin or Slack-bot path is exactly the web frontend's path.

---

## 17. Known Limitations (v1)

- **Not token-streamed.** Tool calls and results appear live; model narration
  arrives per turn, not token by token.
- **No context compaction.** Long sessions grow the message history;
  `agent.max_steps` is the only backstop.
- **Fix-loop cap is advisory.** "≤3 rounds, stop on no progress" is a
  system-prompt instruction, not a hard counter.
- **`run_bash` is unsandboxed** — it uses the system shell. Keep it on `ask`.
- **Web UI is local/single-user** — no auth, no multi-tenant isolation.
- Two harmless `SyntaxWarning`s exist in the vendored `engine/converters/`
  (escape sequences inside prompt strings) — carried over unchanged.

---

## 18. Repository Layout

```
Accode/
├── DESIGN.md                  this document
├── README.md                  user-facing docs
├── pyproject.toml             packaging + `accode` console script
├── requirements.txt           core dependencies
├── config.example.yaml        documented config template
├── config.yaml                local config — git-ignored (holds the API key)
│
├── accode/
│   ├── cli.py                 CLI entrypoint
│   ├── config.py              config loader (loop + engine)
│   ├── context.py             Context object
│   ├── state.py               .accode_state.json
│   ├── paths.py               converter map + path helpers
│   │
│   ├── agent/
│   │   ├── loop.py            the agentic loop  ◀── core
│   │   ├── llm.py             AgentLLM (tool-use client)
│   │   ├── prompt.py          system prompt (canonical workflow)
│   │   ├── registry.py        tool registry
│   │   ├── permissions.py     PermissionGate
│   │   ├── events.py          EventSink / ConsoleSink / QueueSink
│   │   └── tooling.py         Tool / ToolResult
│   │
│   ├── tools/
│   │   ├── generic/           read, write, edit, list_files, search, bash
│   │   └── migration/         the 8 decomposed orchestrator stages
│   │
│   └── engine/                vendored conversion engine (unchanged)
│       ├── scanner.py  validator.py  syntax_checker.py  test_runner.py
│       ├── fix_agent.py  reporter.py  llm.py
│       └── converters/        8 per-file-type converters
│
└── frontend/
    ├── server.py              FastAPI — sessions, SSE, permission relay
    ├── requirements.txt       fastapi + uvicorn
    ├── start.bat
    └── static/                index.html, app.js, styles.css
```
