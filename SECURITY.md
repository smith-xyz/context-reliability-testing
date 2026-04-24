# Security

CRT runs arbitrary agent CLIs and acceptance commands (e.g. tests) inside git worktrees. Treat run configs, task prompts, and driver commands as trusted input from whoever authored them.

If you find a security issue in CRT itself (path traversal, unsafe subprocess handling, etc.), please open a private advisory on the repository’s Security tab or contact the maintainers directly. Do not file public issues for undisclosed vulnerabilities.
