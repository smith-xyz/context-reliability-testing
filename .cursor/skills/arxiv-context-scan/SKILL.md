---
name: arxiv-context-scan
description: >-
  Know how to search arXiv for context-file research and synthesize findings.
  Use when the user mentions research, papers, arXiv, or evidence around
  context files and prompt engineering for coding agents.
---

# arXiv Context File Research

## Finding papers

arXiv is the primary source for context-file research. Relevant categories: `cs.SE`, `cs.AI`, `cs.CL`. Baseline search terms: context, instructions, prompt.

Run searches via the bundled script:

```bash
scripts/arxiv-search.sh [--days N] [--max N] [kw ...]
```

Defaults: 14 days lookback, 20 results. User keywords get OR'd with baseline terms.

## Synthesizing results

Don't just list papers. For each relevant result:

- State the finding in one sentence
- Note whether it supports, contradicts, or is orthogonal to CRT's assumptions
- Flag methodology limitations (sample size, synthetic benchmarks, single model, etc.)
- Call out if the paper's definition of "context" differs from CRT's (repo-level files vs system prompts vs few-shot examples)

Group findings by theme (effectiveness measurement, prompt sensitivity, context length effects) rather than listing chronologically.
