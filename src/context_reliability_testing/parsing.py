"""Structured data loading: read, parse YAML, validate Pydantic models."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from .errors import DataValidationError, FileReadError

TModel = TypeVar("TModel", bound=BaseModel)


def read_text(path: Path, context: str = "") -> str:
    """Read UTF-8 text or raise FileReadError."""
    p = path.resolve()
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileReadError(p, context) from exc


def parse_yaml(text: str, path: Path) -> object | None:
    """Parse YAML or raise DataValidationError."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DataValidationError.yaml_parse(path.resolve(), exc) from exc


def validate_model(model: type[TModel], data: object, context: str = "") -> TModel:
    """Validate data against a Pydantic model or raise DataValidationError."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise DataValidationError.schema(context or model.__name__, exc) from exc


def validate_json(model: type[TModel], text: str, context: str = "") -> TModel:
    """Validate JSON text against a Pydantic model or raise DataValidationError."""
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        raise DataValidationError.schema(context or model.__name__, exc) from exc
