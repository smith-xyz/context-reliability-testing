---
description: Audit CRT code, docs, and prompts for unchecked research assumptions
globs:
alwaysApply: false
---

# Research Assumption Checker

CRT is grounded in research on context files. Find places where we assume something that isn't fact-checked.

## Process

1. Collect claims — scan README, USAGE.md, CONTRIBUTING, prompt templates, code comments, and CLI help strings for any statement that implies a research finding or best practice
2. Classify each claim:
   - Cited — links to a paper or reproducible experiment
   - Plausible — consistent with cited research but not directly supported
   - Unchecked — stated as fact with no backing
   - Contradicted — conflicts with a cited source
3. Report — list every non-cited claim with its location and classification

## What counts as an assumption

- "temperature=0 produces deterministic results" — is that true for all providers?
- "more context files = better agent performance" — the research shows variance, not monotonic improvement
- "stripping context_patterns isolates the variable" — assumes no implicit context leaks (env vars, git history, etc.)
- Heuristic classifications (positive/negative/informational) — are the regex categories validated?
- Acceptance via test command proves correctness — tests may be incomplete

## Output format

```
Claim: "<quoted text>"

Location: file:line (or doc section)
Classification: cited / plausible / unchecked / contradicted
Why it matters: what breaks if the assumption is wrong
Suggestion: cite source, add caveat, or reword
```

## How to check

- If a claim cites a paper, verify the citation actually supports the claim as stated
- If a claim has no citation, flag it — don't assume it's wrong, just mark it unchecked
- Check that referenced works are cited consistently (format, location, accuracy of link)
- Don't validate against a fixed list of papers — evaluate each claim on its own terms
