# Contributing

The spirit of this project is to codify some of the research around context files. I felt there was a big assumption that context files just work and everyone will see equal results if they just add context files. I challenge that assumption; some studies seem to show a clear variance that the choice of words, the number of words, etc., can cause an agent workflow to be less effective. If we're going this way with agent workflows, we might as well try to have a TDD for context files. Any contribution is welcome, but I do not want hype or random features. Please try to stay grounded in the research on this topic.

## Setup

```bash
uv sync --extra dev --extra assertions
```

## Checks

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run pytest -q
```

Open a PR with a short description of the change. Prefer focused diffs over drive-by refactors.
