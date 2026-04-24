"""Tests for TrialBundle, TrialContext, and AssertionRunner."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from context_reliability_testing.assertions import AssertionRunner
from context_reliability_testing.models import AssertionOutcome
from context_reliability_testing.trial_bundle import TrialBundle
from context_reliability_testing.trial_context import TrialContext

SAMPLE_DIFF = textwrap.dedent("""\
    diff --git a/foo.go b/foo.go
    index abc..def 100644
    --- a/foo.go
    +++ b/foo.go
    @@ -1,3 +1,5 @@
     package main
    +
    +import "fmt"
    -func old() {}
    +func new() { fmt.Println("hello") }
""")


# ---------------------------------------------------------------------------
# TrialContext
# ---------------------------------------------------------------------------


class TestTrialContext:
    def _make(self, tmp_path: Path, diff: str = SAMPLE_DIFF) -> TrialContext:
        return TrialContext(
            artifact_dir=tmp_path / "artifacts",
            worktree=tmp_path,
            diff=diff,
            changed_files=["foo.go"],
            task_id="test-task",
            condition="baseline",
            trial_number=1,
            passed=True,
        )

    def test_added_lines(self, tmp_path: Path) -> None:
        ctx = self._make(tmp_path)
        assert 'import "fmt"' in ctx.added_lines
        assert 'func new() { fmt.Println("hello") }' in ctx.added_lines

    def test_removed_lines(self, tmp_path: Path) -> None:
        ctx = self._make(tmp_path)
        assert "func old() {}" in ctx.removed_lines

    def test_added_excludes_header(self, tmp_path: Path) -> None:
        ctx = self._make(tmp_path)
        assert not any(line.startswith("++ b/") for line in ctx.added_lines)

    def test_frozen(self, tmp_path: Path) -> None:
        ctx = self._make(tmp_path)
        with pytest.raises(AttributeError):
            ctx.task_id = "mutated"  # type: ignore[misc]

    def test_read_file(self, tmp_path: Path) -> None:
        (tmp_path / "hello.txt").write_text("world")
        ctx = self._make(tmp_path)
        assert ctx.read_file("hello.txt") == "world"

    def test_file_exists(self, tmp_path: Path) -> None:
        (tmp_path / "exists.txt").write_text("yes")
        ctx = self._make(tmp_path)
        assert ctx.file_exists("exists.txt")
        assert not ctx.file_exists("nope.txt")

    def test_file_contains(self, tmp_path: Path) -> None:
        (tmp_path / "code.go").write_text('net.SplitHostPort("addr")')
        ctx = self._make(tmp_path)
        assert ctx.file_contains("code.go", r"net\.SplitHostPort")
        assert not ctx.file_contains("code.go", r"net\.JoinHostPort")

    def test_file_contains_missing_file(self, tmp_path: Path) -> None:
        ctx = self._make(tmp_path)
        assert not ctx.file_contains("nonexistent.go", r"anything")

    def test_empty_diff(self, tmp_path: Path) -> None:
        ctx = self._make(tmp_path, diff="")
        assert ctx.added_lines == []
        assert ctx.removed_lines == []


# ---------------------------------------------------------------------------
# TrialBundle
# ---------------------------------------------------------------------------


class TestTrialBundle:
    def _make(self, tmp_path: Path) -> TrialBundle:
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        return TrialBundle(
            output_dir=tmp_path / "out",
            task_id="my-task",
            condition="no_context",
            trial_number=1,
            worktree=worktree,
            passed=True,
        )

    def test_artifact_dir_path(self, tmp_path: Path) -> None:
        bundle = self._make(tmp_path)
        assert bundle.artifact_dir == tmp_path / "out" / "artifacts" / "my-task-no_context-1"

    def test_write_creates_directory(self, tmp_path: Path) -> None:
        bundle = self._make(tmp_path)
        bundle._diff = SAMPLE_DIFF
        bundle._changed_files = ["foo.go"]
        bundle.write()
        assert (bundle.artifact_dir / "diff.patch").exists()
        assert (bundle.artifact_dir / "changed_files.txt").read_text().strip() == "foo.go"

    def test_write_empty_diff_no_files(self, tmp_path: Path) -> None:
        bundle = self._make(tmp_path)
        bundle.write()
        assert bundle.artifact_dir.exists()
        assert not (bundle.artifact_dir / "diff.patch").exists()

    def test_write_context_json(self, tmp_path: Path) -> None:
        bundle = self._make(tmp_path)
        bundle._diff = "some diff"
        bundle._changed_files = ["a.py"]
        path = bundle.write_context_json()
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["task_id"] == "my-task"
        assert data["condition"] == "no_context"
        assert data["diff"] == "some diff"
        assert data["changed_files"] == ["a.py"]
        assert data["passed"] is True

    def test_to_context(self, tmp_path: Path) -> None:
        bundle = self._make(tmp_path)
        bundle._diff = SAMPLE_DIFF
        bundle._changed_files = ["foo.go"]
        ctx = bundle.to_context()
        assert isinstance(ctx, TrialContext)
        assert ctx.task_id == "my-task"
        assert ctx.changed_files == ["foo.go"]
        assert ctx.passed is True


# ---------------------------------------------------------------------------
# JUnit XML parsing
# ---------------------------------------------------------------------------


class TestJunitParsing:
    def test_pass_and_fail(self, tmp_path: Path) -> None:
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuite tests="3">
              <testcase name="test_pass" classname="tests"/>
              <testcase name="test_fail" classname="tests">
                <failure message="assert False">AssertionError</failure>
              </testcase>
              <testcase name="test_skip" classname="tests">
                <skipped message="skipped"/>
              </testcase>
            </testsuite>
        """)
        xml_path = tmp_path / "results.xml"
        xml_path.write_text(xml)
        results = AssertionRunner._parse_junit(xml_path)
        assert len(results) == 2
        assert results[0] == AssertionOutcome(name="test_pass", passed=True)
        assert results[1] == AssertionOutcome(
            name="test_fail", passed=False, message="assert False"
        )

    def test_error_testcase(self, tmp_path: Path) -> None:
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuite tests="1">
              <testcase name="test_crash" classname="tests">
                <error message="ImportError"/>
              </testcase>
            </testsuite>
        """)
        xml_path = tmp_path / "results.xml"
        xml_path.write_text(xml)
        results = AssertionRunner._parse_junit(xml_path)
        assert len(results) == 1
        assert results[0].passed is False
        assert "ERROR:" in results[0].message

    def test_missing_xml_raises(self, tmp_path: Path) -> None:
        from context_reliability_testing.assertions import AssertionError_

        with pytest.raises(AssertionError_, match="JUnit XML"):
            AssertionRunner._parse_junit(tmp_path / "nope.xml")

    def test_empty_suite(self, tmp_path: Path) -> None:
        xml = '<?xml version="1.0"?><testsuite tests="0"/>'
        xml_path = tmp_path / "results.xml"
        xml_path.write_text(xml)
        results = AssertionRunner._parse_junit(xml_path)
        assert results == []


# ---------------------------------------------------------------------------
# AssertionRunner integration
# ---------------------------------------------------------------------------


class TestAssertionRunnerIntegration:
    def test_run_with_passing_assertions(self, tmp_path: Path) -> None:
        assertions_file = tmp_path / "test_pass.py"
        assertions_file.write_text(
            textwrap.dedent("""\
                def test_has_changes(trial):
                    assert trial.changed_files
            """)
        )

        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        ctx = TrialContext(
            artifact_dir=artifact_dir,
            worktree=tmp_path,
            diff=SAMPLE_DIFF,
            changed_files=["foo.go"],
            task_id="t1",
            condition="c1",
            trial_number=1,
            passed=True,
        )
        ctx_json = artifact_dir / "context.json"
        ctx_json.write_text(
            json.dumps(
                {
                    "artifact_dir": str(artifact_dir),
                    "worktree": str(tmp_path),
                    "diff": SAMPLE_DIFF,
                    "changed_files": ["foo.go"],
                    "task_id": "t1",
                    "condition": "c1",
                    "trial_number": 1,
                    "passed": True,
                }
            )
        )

        runner = AssertionRunner()
        results = runner.run(str(assertions_file), ctx)
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].name == "test_has_changes"

    def test_run_with_failing_assertion(self, tmp_path: Path) -> None:
        assertions_file = tmp_path / "test_fail.py"
        assertions_file.write_text(
            textwrap.dedent("""\
                def test_always_fails(trial):
                    assert False, "intentional failure"
            """)
        )

        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        ctx = TrialContext(
            artifact_dir=artifact_dir,
            worktree=tmp_path,
            diff="",
            changed_files=[],
            task_id="t1",
            condition="c1",
            trial_number=1,
            passed=True,
        )
        ctx_json = artifact_dir / "context.json"
        ctx_json.write_text(
            json.dumps(
                {
                    "artifact_dir": str(artifact_dir),
                    "worktree": str(tmp_path),
                    "diff": "",
                    "changed_files": [],
                    "task_id": "t1",
                    "condition": "c1",
                    "trial_number": 1,
                    "passed": True,
                }
            )
        )

        runner = AssertionRunner()
        results = runner.run(str(assertions_file), ctx)
        assert len(results) == 1
        assert results[0].passed is False
        assert "intentional failure" in results[0].message

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        from context_reliability_testing.assertions import AssertionError_

        ctx = TrialContext(
            artifact_dir=tmp_path,
            worktree=tmp_path,
            diff="",
            changed_files=[],
            task_id="t1",
            condition="c1",
            trial_number=1,
            passed=True,
        )
        runner = AssertionRunner()
        with pytest.raises(AssertionError_, match="not found"):
            runner.run(str(tmp_path / "nonexistent.py"), ctx)
