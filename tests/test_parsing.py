"""Unit tests for parsing helpers: read, YAML, Pydantic validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from context_reliability_testing.errors import DataValidationError, FileReadError
from context_reliability_testing.parsing import parse_yaml, read_text, validate_json, validate_model


class _Sample(BaseModel):
    name: str
    count: int = 0


class TestReadText:
    def test_reads_utf8_file(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("héllo\n", encoding="utf-8")
        assert read_text(f) == "héllo\n"

    def test_raises_file_read_error_for_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileReadError, match="cannot read"):
            read_text(tmp_path / "nope.txt")

    def test_context_appears_in_error(self, tmp_path: Path) -> None:
        with pytest.raises(FileReadError, match="config") as exc_info:
            read_text(tmp_path / "nope.txt", "config")
        assert "config file" in str(exc_info.value)

    def test_preserves_cause(self, tmp_path: Path) -> None:
        with pytest.raises(FileReadError) as exc_info:
            read_text(tmp_path / "nope.txt")
        assert isinstance(exc_info.value.__cause__, OSError)

    def test_path_field_is_resolved(self, tmp_path: Path) -> None:
        with pytest.raises(FileReadError) as exc_info:
            read_text(tmp_path / "nope.txt")
        assert exc_info.value.path.is_absolute()


class TestParseYaml:
    def test_parses_valid_yaml(self) -> None:
        assert parse_yaml("key: value\n", Path("f.yaml")) == {"key": "value"}

    def test_parses_list(self) -> None:
        assert parse_yaml("- a\n- b\n", Path("f.yaml")) == ["a", "b"]

    def test_returns_none_for_empty(self) -> None:
        assert parse_yaml("", Path("f.yaml")) is None

    def test_raises_data_validation_error_for_broken_yaml(self) -> None:
        with pytest.raises(DataValidationError, match="invalid YAML"):
            parse_yaml("{broken: [", Path("f.yaml"))

    def test_error_includes_path(self) -> None:
        with pytest.raises(DataValidationError, match="config.yaml"):
            parse_yaml("{broken", Path("config.yaml"))

    def test_preserves_cause(self) -> None:
        import yaml

        with pytest.raises(DataValidationError) as exc_info:
            parse_yaml("{broken: [", Path("f.yaml"))
        assert isinstance(exc_info.value.__cause__, yaml.YAMLError)


class TestValidateModel:
    def test_validates_good_data(self) -> None:
        result = validate_model(_Sample, {"name": "x", "count": 5})
        assert result.name == "x"
        assert result.count == 5

    def test_raises_data_validation_error_for_bad_data(self) -> None:
        with pytest.raises(DataValidationError, match="invalid"):
            validate_model(_Sample, {"wrong": "field"})

    def test_context_appears_in_error(self) -> None:
        with pytest.raises(DataValidationError, match="run config"):
            validate_model(_Sample, {}, context="run config")

    def test_defaults_to_model_name(self) -> None:
        with pytest.raises(DataValidationError, match="_Sample"):
            validate_model(_Sample, {})

    def test_preserves_cause(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(DataValidationError) as exc_info:
            validate_model(_Sample, {})
        assert isinstance(exc_info.value.__cause__, ValidationError)


class TestValidateJson:
    def test_validates_good_json(self) -> None:
        result = validate_json(_Sample, '{"name": "x", "count": 3}')
        assert result.name == "x"
        assert result.count == 3

    def test_raises_data_validation_error_for_bad_json(self) -> None:
        with pytest.raises(DataValidationError, match="invalid"):
            validate_json(_Sample, '{"wrong": true}')

    def test_context_appears_in_error(self) -> None:
        with pytest.raises(DataValidationError, match="results JSON"):
            validate_json(_Sample, "{}", context="results JSON")

    def test_preserves_cause(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(DataValidationError) as exc_info:
            validate_json(_Sample, "{}")
        assert isinstance(exc_info.value.__cause__, ValidationError)
