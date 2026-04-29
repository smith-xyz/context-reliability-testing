"""Unit tests for error hierarchy, classmethods, and structured fields."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from context_reliability_testing.errors import (
    ConfigError,
    ContextFileError,
    CRTError,
    DataValidationError,
    DriverConfigError,
    FileReadError,
    InternalError,
    PreflightError,
    WorkspaceError,
)


class TestErrorHierarchy:
    """Every CRTError subclass satisfies its inheritance contracts."""

    ALL_CLASSES = [
        ConfigError,
        PreflightError,
        WorkspaceError,
        DriverConfigError,
        DataValidationError,
        FileReadError,
        ContextFileError,
        InternalError,
    ]

    def test_all_subclasses_are_crt_error(self) -> None:
        for cls in self.ALL_CLASSES:
            if cls is FileReadError:
                exc = cls(Path("/tmp/x"))
            elif cls is ContextFileError:
                exc = cls("rel")
            else:
                exc = cls("test")
            assert isinstance(exc, CRTError), f"{cls.__name__} is not a CRTError"

    def test_config_error_is_value_error(self) -> None:
        assert isinstance(ConfigError("x"), ValueError)

    def test_preflight_error_is_runtime_error(self) -> None:
        assert isinstance(PreflightError("x"), RuntimeError)

    def test_driver_config_error_is_value_error(self) -> None:
        assert isinstance(DriverConfigError("x"), ValueError)

    def test_data_validation_error_is_value_error(self) -> None:
        assert isinstance(DataValidationError("x"), ValueError)

    def test_internal_error_is_runtime_error(self) -> None:
        assert isinstance(InternalError("x"), RuntimeError)

    def test_workspace_error_not_runtime_or_value(self) -> None:
        exc = WorkspaceError("x")
        assert not isinstance(exc, (ValueError, RuntimeError))


class _Dummy(BaseModel):
    name: str


class TestErrorClassmethods:
    """Factory classmethods produce correct type and message content."""

    def test_preflight_baseline(self) -> None:
        exc = PreflightError.baseline("t1", "tests failed", hint="Fix baseline.")
        assert isinstance(exc, PreflightError)
        assert "t1" in str(exc)
        assert "tests failed" in str(exc)
        assert "Fix baseline." in str(exc)

    def test_preflight_smoke_test_without_output(self) -> None:
        exc = PreflightError.smoke_test("timeout", hint="Check config.")
        assert isinstance(exc, PreflightError)
        assert "timeout" in str(exc)
        assert "Check config." in str(exc)
        assert "Agent output" not in str(exc)

    def test_preflight_smoke_test_with_output(self) -> None:
        exc = PreflightError.smoke_test("err", hint="h", output="raw stuff")
        assert "raw stuff" in str(exc)
        assert "Agent output" in str(exc)

    def test_driver_config_unknown_builtin(self) -> None:
        exc = DriverConfigError.unknown_builtin("magic")
        assert isinstance(exc, DriverConfigError)
        assert "'magic'" in str(exc)
        assert "unknown builtin driver" in str(exc)

    def test_data_validation_yaml_parse(self) -> None:
        try:
            yaml.safe_load("{broken: [")
        except yaml.YAMLError as ye:
            exc = DataValidationError.yaml_parse(Path("config.yaml"), ye)
            assert isinstance(exc, DataValidationError)
            assert "invalid YAML" in str(exc)
            assert "config.yaml" in str(exc)

    def test_data_validation_schema(self) -> None:
        try:
            _Dummy.model_validate({"wrong": "field"})
        except ValidationError as ve:
            exc = DataValidationError.schema("run config", ve)
            assert isinstance(exc, DataValidationError)
            assert "invalid run config" in str(exc)

    def test_data_validation_not_mapping(self) -> None:
        exc = DataValidationError.not_mapping(Path("f.yaml"), "list")
        assert isinstance(exc, DataValidationError)
        assert "expected YAML mapping" in str(exc)
        assert "list" in str(exc)
        assert "f.yaml" in str(exc)


class TestStructuredFields:
    """Structured fields available for programmatic access."""

    def test_file_read_error_path_field(self) -> None:
        exc = FileReadError(Path("/tmp/x"), "config")
        assert exc.path == Path("/tmp/x")

    def test_file_read_error_context_in_message(self) -> None:
        exc = FileReadError(Path("/tmp/x"), "config")
        assert "config file" in str(exc)

    def test_file_read_error_no_context(self) -> None:
        exc = FileReadError(Path("/tmp/x"))
        assert "cannot read file" in str(exc)
        assert "cannot read  file" not in str(exc)

    def test_context_file_error_rel_field(self) -> None:
        exc = ContextFileError(".cursor/rules.md")
        assert exc.rel == ".cursor/rules.md"
        assert ".cursor/rules.md" in str(exc)
        assert "context file not found" in str(exc)
