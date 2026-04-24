---
description: Search arXiv for recent papers on agent context files and prompt engineering
globs:
alwaysApply: false
---

# arXiv Context Scan

Uses the `arxiv-context-scan` skill to search and synthesize results.

```bash
.cursor/skills/arxiv-context-scan/scripts/arxiv-search.sh [--days N] [--max N] [kw ...]
```

| User says | Run |
|-----------|-----|
| Any new papers on context files? | `scripts/arxiv-search.sh` |
| Research on AGENTS.md last month | `scripts/arxiv-search.sh --days 30 AGENTS.md` |
| Papers on prompt tuning for code agents | `scripts/arxiv-search.sh prompt tuning "code agent"` |

On network failure, ask the user to run the command in a terminal and paste output.
