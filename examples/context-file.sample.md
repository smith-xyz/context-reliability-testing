# Project Guidelines

Example context file (AGENTS.md / CLAUDE.md / .cursorrules).
Use as a reference when writing your own, or as a test fixture for CRT conditions.

## Code Quality

- Always run the test suite before committing changes
- Ensure all public functions have docstrings
- Do not refactor code unrelated to the current task
- Never introduce new dependencies without team approval

## Architecture

- This project uses a hexagonal architecture with ports and adapters
- The `src/core/` directory contains domain logic with no external imports
- Handlers in `src/api/` must not access the database directly

## Testing

- You should write tests for any new functionality
- Don't modify existing test assertions without understanding why they exist
- Can't skip integration tests in CI even if they're slow
- Tests must not depend on external services or network access

## Safety

- Avoid hardcoded credentials or API keys
- Restrict direct SQL queries to the repository layer
- You shouldn't push to main without a passing CI build
- Forbid force-pushing to any shared branch
