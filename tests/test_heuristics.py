from __future__ import annotations

from pathlib import Path

import yaml

from context_reliability_testing.heuristics import (
    ClassifierDef,
    HeuristicsConfig,
    RuleClassification,
    RuleParser,
    load_heuristics_config,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _sample_config() -> HeuristicsConfig:
    """Load the sample heuristics config from examples/."""
    return load_heuristics_config(EXAMPLES / "heuristics-config.sample.yaml")


def test_parser_list_items_and_paragraphs(tmp_path: Path) -> None:
    md = tmp_path / "ctx.md"
    md.write_text(
        "# Title\n\n"
        "Intro paragraph one.\n"
        "Still intro.\n\n"
        "Second block.\n\n"
        "- List A\n"
        "- List B\n"
        "1. Num one\n"
    )
    rules = RuleParser(_sample_config()).parse(md)
    texts = [r.text for r in rules]
    assert "Intro paragraph one. Still intro." in texts
    assert "Second block." in texts
    assert "- List A" in texts
    assert "- List B" in texts
    assert "1. Num one" in texts


def test_parser_skips_code_blocks(tmp_path: Path) -> None:
    md = tmp_path / "ctx.md"
    md.write_text("- Before\n```\n- fake rule in code\n```\n- After\n")
    rules = RuleParser(_sample_config()).parse(md)
    assert [r.text for r in rules] == ["- Before", "- After"]


def test_parser_skips_headings_not_rules(tmp_path: Path) -> None:
    md = tmp_path / "ctx.md"
    md.write_text("# H1\n## H2\n\nBody only.\n")
    rules = RuleParser(_sample_config()).parse(md)
    assert len(rules) == 1
    assert rules[0].text == "Body only."


def test_classification_negative_positive_informational(tmp_path: Path) -> None:
    md = tmp_path / "ctx.md"
    md.write_text("- Don't use global variables\n- Always run tests\n- This module handles auth\n")
    rules = RuleParser(_sample_config()).parse(md)
    by_text = {r.text: r.classification for r in rules}
    assert by_text["- Don't use global variables"] == RuleClassification.NEGATIVE
    assert by_text["- Always run tests"] == RuleClassification.POSITIVE
    assert by_text["- This module handles auth"] == RuleClassification.INFORMATIONAL


def test_parser_custom_classifier_keywords(tmp_path: Path) -> None:
    cfg = HeuristicsConfig(
        classifiers={
            "negative": ClassifierDef(keywords=["halt"]),
            "positive": ClassifierDef(keywords=["advance"]),
        },
    )
    md = tmp_path / "ctx.md"
    md.write_text("- halt here\n- advance there\n- neutral\n")
    rules = RuleParser(cfg).parse(md)
    by_text = {r.text: r.classification for r in rules}
    assert by_text["- halt here"] == RuleClassification.NEGATIVE
    assert by_text["- advance there"] == RuleClassification.POSITIVE
    assert by_text["- neutral"] == RuleClassification.INFORMATIONAL


def test_load_heuristics_config_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "h.yaml"
    path.write_text(
        yaml.dump(
            {
                "classifiers": {
                    "negative": {"keywords": ["stop"]},
                    "positive": {"keywords": ["go"]},
                },
            }
        )
    )
    cfg = load_heuristics_config(path)
    assert cfg.classifiers["negative"].keywords == ["stop"]


def test_load_sample_config() -> None:
    cfg = _sample_config()
    assert "negative" in cfg.classifiers
    assert "positive" in cfg.classifiers
    assert len(cfg.classifiers["negative"].patterns) > 0


def test_parse_empty_file(tmp_path: Path) -> None:
    md = tmp_path / "e.md"
    md.write_text("")
    assert RuleParser(_sample_config()).parse(md) == []


def test_parse_only_code_blocks(tmp_path: Path) -> None:
    md = tmp_path / "c.md"
    md.write_text("```\nnot a rule\n```\n")
    assert RuleParser(_sample_config()).parse(md) == []


def test_parse_only_headings(tmp_path: Path) -> None:
    md = tmp_path / "h.md"
    md.write_text("# A\n## B\n")
    assert RuleParser(_sample_config()).parse(md) == []


# --- regex pattern coverage ---


def test_classify_contractions_as_negative(tmp_path: Path) -> None:
    md = tmp_path / "ctx.md"
    md.write_text(
        "- You shouldn't modify unrelated files\n"
        "- Can't use global state\n"
        "- Won't accept PRs without tests\n"
    )
    rules = RuleParser(_sample_config()).parse(md)
    assert all(r.classification == RuleClassification.NEGATIVE for r in rules)


def test_classify_rfc2119_negative(tmp_path: Path) -> None:
    md = tmp_path / "ctx.md"
    md.write_text(
        "- You MUST NOT modify the schema\n"
        "- Clients SHALL NOT send raw SQL\n"
        "- Handlers MAY NOT bypass auth\n"
    )
    rules = RuleParser(_sample_config()).parse(md)
    assert all(r.classification == RuleClassification.NEGATIVE for r in rules)


def test_classify_rfc2119_positive(tmp_path: Path) -> None:
    md = tmp_path / "ctx.md"
    md.write_text(
        "- You MUST run linters before commit\n"
        "- All handlers SHALL log the request\n"
        "- Tests are REQUIRED for new code\n"
    )
    rules = RuleParser(_sample_config()).parse(md)
    assert all(r.classification == RuleClassification.POSITIVE for r in rules)


def test_should_not_beats_should(tmp_path: Path) -> None:
    """'should not' must classify as negative, not positive."""
    md = tmp_path / "ctx.md"
    md.write_text("- You should not refactor unrelated code\n")
    rules = RuleParser(_sample_config()).parse(md)
    assert rules[0].classification == RuleClassification.NEGATIVE


def test_cannot_classified_negative(tmp_path: Path) -> None:
    md = tmp_path / "ctx.md"
    md.write_text("- Agents cannot modify protected branches\n")
    rules = RuleParser(_sample_config()).parse(md)
    assert rules[0].classification == RuleClassification.NEGATIVE


def test_forbid_disallow_restrict_negative(tmp_path: Path) -> None:
    md = tmp_path / "ctx.md"
    md.write_text(
        "- We forbid direct DB access from handlers\n"
        "- Disallow hardcoded credentials\n"
        "- Restrict network calls to approved hosts\n"
    )
    rules = RuleParser(_sample_config()).parse(md)
    assert all(r.classification == RuleClassification.NEGATIVE for r in rules)


def test_plain_informational_no_false_positive(tmp_path: Path) -> None:
    """Neutral description should not trigger positive or negative."""
    md = tmp_path / "ctx.md"
    md.write_text("- This module handles database migrations\n")
    rules = RuleParser(_sample_config()).parse(md)
    assert rules[0].classification == RuleClassification.INFORMATIONAL


def test_custom_keywords_still_work(tmp_path: Path) -> None:
    cfg = HeuristicsConfig(
        classifiers={
            "negative": ClassifierDef(keywords=["halt"]),
            "positive": ClassifierDef(keywords=["advance"]),
        },
    )
    md = tmp_path / "ctx.md"
    md.write_text("- halt execution\n- advance queue\n- neutral info\n")
    rules = RuleParser(cfg).parse(md)
    by_text = {r.text: r.classification for r in rules}
    assert by_text["- halt execution"] == RuleClassification.NEGATIVE
    assert by_text["- advance queue"] == RuleClassification.POSITIVE
    assert by_text["- neutral info"] == RuleClassification.INFORMATIONAL
