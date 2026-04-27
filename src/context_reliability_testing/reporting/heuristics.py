"""Heuristic rule classifier for context files.

Regex/keyword classifier. Not semantic — false positives on embedded negation,
double-negation, etc.
Taxonomy (negative/positive/informational) per Zhang et al. (arXiv:2604.11088).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, model_validator


class RuleClassification(StrEnum):
    NEGATIVE = "negative"
    POSITIVE = "positive"
    INFORMATIONAL = "informational"


@dataclass
class ParsedRule:
    text: str
    classification: RuleClassification
    line_number: int


class ClassifierDef(BaseModel):
    """Regex patterns and/or plain keywords for one classification bucket."""

    patterns: list[str] = []
    keywords: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_to_empty(cls, values: dict) -> dict:  # type: ignore[type-arg]
        for field in ("patterns", "keywords"):
            if values.get(field) is None:
                values[field] = []
        return values


class HeuristicsConfig(BaseModel):
    classifiers: dict[str, ClassifierDef]


class RuleParser:
    """Parses markdown into classified rules. Negative patterns checked before positive."""

    def __init__(self, config: HeuristicsConfig) -> None:
        self.config = config
        self._compiled: dict[str, list[re.Pattern[str]]] = {}
        for name, cdef in self.config.classifiers.items():
            self._compiled[name] = [re.compile(p, re.IGNORECASE) for p in cdef.patterns]

    def parse(self, context_file: Path) -> list[ParsedRule]:
        """Parse a markdown context file into classified rules."""
        content = context_file.read_text()
        rules: list[ParsedRule] = []
        in_code_block = False
        current_rule_lines: list[str] = []
        current_start_line = 0

        for line_num, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()

            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            if stripped.startswith("#"):
                if current_rule_lines:
                    rules.append(self._make_rule(current_rule_lines, current_start_line))
                    current_rule_lines = []
                continue

            if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
                if current_rule_lines:
                    rules.append(self._make_rule(current_rule_lines, current_start_line))
                current_rule_lines = [stripped]
                current_start_line = line_num
                continue

            if not stripped:
                if current_rule_lines:
                    rules.append(self._make_rule(current_rule_lines, current_start_line))
                    current_rule_lines = []
                continue

            if not current_rule_lines:
                current_start_line = line_num
            current_rule_lines.append(stripped)

        if current_rule_lines:
            rules.append(self._make_rule(current_rule_lines, current_start_line))

        return rules

    def _make_rule(self, lines: list[str], start_line: int) -> ParsedRule:
        text = " ".join(lines)
        classification = self._classify(text)
        return ParsedRule(text=text, classification=classification, line_number=start_line)

    def _classify(self, text: str) -> RuleClassification:
        neg_def = self.config.classifiers.get("negative", ClassifierDef())
        pos_def = self.config.classifiers.get("positive", ClassifierDef())

        if self._matches(text, "negative", neg_def):
            return RuleClassification.NEGATIVE
        if self._matches(text, "positive", pos_def):
            return RuleClassification.POSITIVE
        return RuleClassification.INFORMATIONAL

    def _matches(self, text: str, name: str, cdef: ClassifierDef) -> bool:
        if any(pat.search(text) for pat in self._compiled.get(name, [])):
            return True
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in cdef.keywords)


def load_heuristics_config(path: Path) -> HeuristicsConfig:
    """Load a user-provided heuristics config YAML."""
    import yaml

    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"heuristics config must be a YAML mapping, got {type(data).__name__}")
    return HeuristicsConfig.model_validate(data)
