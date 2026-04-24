# Usage guide

## Dependencies

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Git

```bash
git clone https://github.com/smith-xyz/context-reliability-testing
cd context-reliability-testing
uv sync --extra dev
```

Verify the install:

```bash
uv run crt --help
```

## Agent setup

CRT invokes your agent as a subprocess. You need a CLI agent that accepts a prompt and works in a given directory.

### Supported agents

#### Claude Code

Requires: `claude` CLI installed and authenticated (`claude auth login` or Vertex AI credentials).

Include `--output-format json` so CRT can extract token usage, cost, and turn count from the result. Without it, only wall time is captured.

```yaml
driver:
  command: ["claude", "--dangerously-skip-permissions", "--output-format", "json", "-p"]
  prompt_mode: arg
```

With Vertex AI:

```yaml
driver:
  command: ["claude", "--dangerously-skip-permissions", "--output-format", "json", "--model", "vertex/claude-sonnet-4-20250514", "-p"]
  prompt_mode: arg
```

CRT auto-detects both `json` (single envelope, recommended) and `stream-json` (NDJSON) formats and extracts `input_tokens`, `output_tokens`, `total_cost_usd`, and `num_turns` from the result event. Note: `stream-json` requires `--verbose` when used with `-p`.

#### Cursor Agent

Requires: `agent` CLI (`curl https://cursor.com/install -fsSL | bash`) and auth (`cursor auth login`).

```yaml
driver:
  command: ["agent", "--no-interactive"]
  prompt_mode: arg
```

#### Custom script

Any executable that reads `$CRT_PROMPT` and works in the current directory:

```yaml
driver:
  command: ["./my-agent.sh"]
  prompt_mode: env
```

### Prompt delivery modes

| Mode    | Behavior                                        |
|---------|-------------------------------------------------|
| `arg`   | Prompt appended as the last CLI argument        |
| `stdin` | Prompt piped to stdin                           |
| `env`   | Prompt set in `$CRT_PROMPT` only                |

### Environment variables

CRT sets these for every agent invocation:

| Variable           | Description                                |
|--------------------|--------------------------------------------|
| `CRT_WORKSPACE`    | Absolute path to the isolated worktree     |
| `CRT_MODEL`        | Model name from run config                 |
| `CRT_MAX_TURNS`    | Max tool-use iterations                    |
| `CRT_PROMPT`       | The task prompt                            |
| `CRT_RESULT_FILE`  | Path to write optional sidecar metrics     |

### Metric capture

CRT extracts metrics from agent output in this order:

1. **Auto-detect from stdout** — if the agent outputs `--output-format json` (recommended) or `stream-json`, CRT parses tokens, cost, and turns automatically.
2. **Sidecar file** — if the agent writes JSON to `$CRT_RESULT_FILE`, CRT reads it as a fallback. Useful for custom wrapper scripts.
3. **None** — if neither is available, CRT records wall time only. Tokens/cost show as 0.

Metrics captured (when available):

| Metric | Source field | Description |
|--------|-------------|-------------|
| Input tokens | `usage.input_tokens` | Prompt/context tokens |
| Output tokens | `usage.output_tokens` | Generated tokens |
| Cost | `total_cost_usd` | API cost in USD |
| Turns | `num_turns` | Agent conversation turns |
| Tool calls | counted from `assistant` events | Individual tool invocations |

**Important**: metric capture only works in headless mode (the default). With `--stream`, stdout is inherited by the agent for display and CRT cannot parse it.

### Prompt templates

CRT wraps each task's `prompt` in a configurable template before sending it to the agent. Use `prompt_template` in your run config:

```yaml
prompt_template: |
  You are working in a Git repository. Your task:

  {prompt}

  When done, this command verifies your work: {acceptance_cmd}
```

Available variables:

| Variable          | Source                        |
|-------------------|-------------------------------|
| `{prompt}`        | The task's `prompt` field     |
| `{acceptance_cmd}`| The task's acceptance command |
| `{task_id}`       | The task's `id` field         |

If omitted, the raw task prompt is sent as-is (`{prompt}`).

### Safety

CRT runs agents in isolated git worktrees cloned from your repo. Push URLs are disabled (`PUSH_DISABLED_BY_CRT`) so agents cannot push to the real remote.

Still, review your driver command flags before running. Agents with broad permissions (e.g. `--dangerously-skip-permissions`) can make network calls, install packages, or access your environment. Consider restricting tool access where your agent supports it:

```yaml
# Claude Code with restricted tools
command: ["claude", "--output-format", "json", "--allowedTools", "Edit,Read,Bash", "-p"]
```

## Verify your agent

Before running a full evaluation, confirm your agent works standalone. CRT invokes the exact command from your driver config — if it fails outside CRT, it will fail inside too.

Test your agent in an empty directory:

```bash
# Claude Code
claude --output-format json --dangerously-skip-permissions -p "Reply with the word HELLO"

# Cursor Agent
agent --no-interactive "Reply with the word HELLO"
```

If this fails (auth errors, missing binary, exit code != 0), fix it before running CRT. Common issues:

| Symptom | Fix |
|---------|-----|
| `command not found` | Install the agent CLI and ensure it's on `$PATH` |
| Auth / credential error | Run the agent's auth flow (e.g., `claude auth login`) |
| Exit code 1 with no output | Check `--dangerously-skip-permissions` or equivalent flag |
| Output appears but exit code != 0 | Some agents exit non-zero on warnings — CRT records this as `error` |

Use `--dry-run` to validate CRT config without invoking agents:

```bash
uv run crt run --config crt-config.yaml --tasks crt-tasks.yaml --dry-run
```

## Quick start

Scaffold configs from an existing repo:

```bash
uv run crt init /path/to/your/repo --test-cmd "pytest -x"
```

This detects context files (AGENTS.md, CLAUDE.md, .cursorrules, .cursor/rules/) and generates:
- `crt-config.yaml` — run config with detected files wired into conditions
- `crt-tasks.yaml` — sample task (replace with real tasks)
- `crt_assertions.py` — sample quality assertions (customize for your project)

Dry run to validate:

```bash
uv run crt run --config crt-config.yaml --tasks crt-tasks.yaml --dry-run
```

## Running

### `crt run`

The single command for all evaluations. Provide tasks via `--tasks` (explicit YAML) or `--range` (auto-derive from git history).

```bash
# Explicit tasks
uv run crt run --config crt-config.yaml --tasks my-tasks.yaml

# Auto-derive from git history
uv run crt run --config crt-config.yaml --range HEAD~5..HEAD --acceptance-cmd "go test ./..."
```

By default, CRT runs in headless mode with a live progress table showing elapsed time, pass/fail, and cost per trial. This captures full metrics (tokens, cost, turns). Use `--stream` to see raw agent output (disables metric capture):

```bash
# Default: headless with metrics
uv run crt run --config crt-config.yaml --tasks my-tasks.yaml

# Debug: see agent output directly (no metrics)
uv run crt run --config crt-config.yaml --tasks my-tasks.yaml --stream
```

#### Task progression modes

When using sequential tasks (from `--range` or tasks with `task_order`):

| Mode         | Behavior                                              |
|--------------|-------------------------------------------------------|
| `continuous` | Agent builds on its own output — divergence compounds |
| `anchored`   | Each task resets to the real commit state              |

```bash
uv run crt run --config crt-config.yaml --range HEAD~5..HEAD --mode anchored
```

### Writing tasks

Each task needs an `id`, `prompt`, and `acceptance` criteria:

```yaml
- id: fix-panic-on-nil-config
  prompt: |
    The server crashes at startup when config.yaml is missing optional
    fields. Handle nil values in loadConfig() instead of panicking.
  acceptance:
    type: test_command
    command: go test ./cmd/... -run TestLoadConfig -count=1
  metadata:
    difficulty: easy
    category: bugfix
```

Acceptance types:

| Type           | What it checks                                  |
|----------------|-------------------------------------------------|
| `test_command` | Runs a shell command; pass = exit code 0        |
| `diff_check`   | Verifies `expected_files` were modified         |
| `manual`       | Skips automated check (for human review)        |

### Assertions — measuring output quality

Acceptance checks (e.g. `make test`) tell you if the agent's code compiles and passes tests. Assertions go further — they measure *how well* the agent did the work. Did it write tests? Did it avoid hardcoded values? Did it touch a reasonable number of files?

Add an `assertions` field to any task pointing to a Python file with standard pytest functions:

```yaml
- id: add-ipv6-support
  prompt: ...
  acceptance:
    type: test_command
    command: make test
  assertions: crt_assertions.py
```

```python
# crt_assertions.py
def test_agent_wrote_tests(trial):
    test_files = [f for f in trial.changed_files if "_test.go" in f]
    assert test_files, "Agent didn't create any test files"

def test_no_hardcoded_ports(trial):
    for line in trial.added_lines:
        assert ":8080" not in line, f"Hardcoded port: {line}"

def test_reasonable_scope(trial):
    assert len(trial.changed_files) <= 5, "Agent touched too many files"
```

The `trial` fixture (type: `TrialContext`) provides:

| Property / Method | Description |
|-------------------|-------------|
| `trial.diff` | Raw `git diff HEAD` string |
| `trial.changed_files` | List of modified file paths |
| `trial.added_lines` | Lines added by agent (cached) |
| `trial.removed_lines` | Lines removed by agent (cached) |
| `trial.passed` | Acceptance check result |
| `trial.task_id` | Task ID |
| `trial.condition` | Active condition name |
| `trial.artifact_dir` | Path to persistent artifact directory |
| `trial.read_file(path)` | Read a file from the worktree |
| `trial.file_exists(path)` | Check if file exists in worktree |
| `trial.file_contains(path, regex)` | Regex search in a worktree file |

#### Artifact directory

Each trial produces a persistent artifact directory at `out/artifacts/<task>-<condition>-<trial>/` containing:
- `diff.patch` — full git diff
- `changed_files.txt` — one file per line
- `context.json` — serialized trial context (for re-running assertions)
- `junit.xml` — assertion test results (when assertions are configured)

Re-run assertions standalone against any past trial:

```bash
CRT_TRIAL_CONTEXT=out/artifacts/add-ipv6-support-no_context-1/context.json \
  pytest crt_assertions.py -p context_reliability_testing.pytest_plugin
```

Requires the `assertions` extra: `pip install context-reliability-testing[assertions]`

#### Report output

When assertions are configured, SUMMARY.md includes a per-assertion, per-condition pass rate table:

```
## Assertion results
| Assertion              | no_context    | with_agent_md |
| ---------------------- | ------------- | ------------- |
| test_agent_wrote_tests | 0/1 (0%)      | 1/1 (100%)    |
| test_no_hardcoded_ports| 1/1 (100%)    | 1/1 (100%)    |
```

### Configuring conditions

Conditions control which context files the agent sees. CRT strips ALL files matching `context_patterns`, then restores only the ones listed in the active condition:

```yaml
context_patterns:
  - "AGENTS.md"
  - "CLAUDE.md"
  - ".cursorrules"
  - ".cursor/rules/*.md"

conditions:
  no_context:
    context_files: []
  full_context:
    context_files:
      - AGENTS.md
      - .cursor/rules/*.md
```

By default, files are preserved from the repo worktree. To inject **custom context files** (e.g., a hand-written AGENTS.md to test against the repo's original), set `source_dir` on the condition:

```yaml
conditions:
  no_context:
    context_files: []
  repo_agents_md:
    context_files: [AGENTS.md]           # keeps the repo's own file
  custom_agents_md:
    context_files: [AGENTS.md]
    source_dir: ./fixtures/custom/       # injects ./fixtures/custom/AGENTS.md instead
```

This lets you A/B test different versions of context files against the same tasks.

### Results

After a run, CRT outputs:
- `results.json` — raw trial data (pass/fail, tokens, wall time per trial)
- `SUMMARY.md` — comparison table across conditions

Compare two runs for regressions:

```bash
uv run crt compare --baseline out/baseline.json --current out/results.json
```

## Configuration reference

### Run config fields

| Field              | Required | Default     | Description                                     |
|--------------------|----------|-------------|-------------------------------------------------|
| `agent.model`      | yes      | —           | Model identifier (report label)                 |
| `agent.temperature`| no       | `0`         | 0 = deterministic; raise for multi-trial variance |
| `agent.max_steps`  | no       | `50`        | Max tool-use iterations before CRT kills the run |
| `agent.annotations`| no       | `{}`        | Free-form labels saved in results.json          |
| `conditions`       | yes      | —           | Named context variants (see above)              |
| `conditions.*.source_dir` | no | —          | Local dir with custom context files to inject   |
| `context_patterns` | no       | `[]`        | Globs for context files to strip/restore        |
| `heuristics_config`| no       | `null`      | Path to heuristics YAML for rule classification |
| `trials`           | no       | `1`         | Runs per (task, condition) pair                 |
| `prompt_template`  | no       | `{prompt}`  | Template wrapping task prompts (see above)      |
| `output_dir`       | no       | `out/`      | Where results land                              |
| `repo.url`         | no       | —           | Git remote to clone                             |
| `repo.commit`      | no       | `main`      | Branch, tag, or SHA to pin                      |
| `driver.command`   | *        | —           | Argv-style command to launch the agent          |
| `driver.builtin`   | *        | `stub`      | Built-in driver name (`stub` for testing)       |
| `driver.prompt_mode`| no      | `arg`       | How the prompt is delivered to the agent         |

\* Exactly one of `driver.command` or `driver.builtin` is required.

### CLI flags

```
crt init [DIRECTORY]  --test-cmd --model --output
crt run               --config --tasks --range --acceptance-cmd --mode --output --seed --dry-run --stream --keep-worktrees/--cleanup
crt compare           --baseline --current
```

All commands support `--verbose` / `-v` for debug logging.
