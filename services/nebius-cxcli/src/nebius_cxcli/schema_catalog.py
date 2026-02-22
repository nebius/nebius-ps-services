"""Helpers for introspecting config schema fields for CLI guidance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import PydanticUndefined

from .schema import ConfigV1


@dataclass(frozen=True)
class SchemaFieldEntry:
    path: str
    required: bool
    type_name: str
    default_value: str | None


def _unwrap_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    if str(origin).endswith("Annotated"):
        args = get_args(annotation)
        return args[0] if args else annotation
    return annotation


def _model_from_annotation(annotation: Any) -> type[BaseModel] | None:
    annotation = _unwrap_annotation(annotation)
    origin = get_origin(annotation)
    if origin is None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
        return None

    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if len(args) == 1:
        inner = _unwrap_annotation(args[0])
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return inner
    return None


def _type_name(annotation: Any) -> str:
    annotation = _unwrap_annotation(annotation)
    origin = get_origin(annotation)
    if origin is None:
        if hasattr(annotation, "__name__"):
            return annotation.__name__
        return str(annotation)

    args = get_args(annotation)
    origin_name = getattr(origin, "__name__", str(origin).replace("typing.", ""))
    if args:
        return f"{origin_name}[{', '.join(_type_name(arg) for arg in args)}]"
    return origin_name


def _default_repr(field: Any) -> str | None:
    if field.default_factory is not None:
        return "<factory>"
    if field.default is PydanticUndefined:
        return None
    return repr(field.default)


def resolve_model_at_path(path: str) -> type[BaseModel]:
    """Resolve dot-path (e.g., infra.mk8s) to a schema model."""
    normalized = path.strip()
    if not normalized or normalized in {"config", "."}:
        return ConfigV1

    current: type[BaseModel] = ConfigV1
    for segment in normalized.split("."):
        field = current.model_fields.get(segment)
        if field is None:
            for _, candidate in current.model_fields.items():
                if candidate.alias == segment:
                    field = candidate
                    break
        if field is None:
            raise ValueError(f"Unknown schema path segment '{segment}' in '{path}'")
        nested_model = _model_from_annotation(field.annotation)
        if nested_model is None:
            raise ValueError(f"Path '{path}' points to scalar field '{segment}', not an object")
        current = nested_model
    return current


def list_schema_fields(path: str) -> list[SchemaFieldEntry]:
    """List fields recursively under the provided schema path."""
    model = resolve_model_at_path(path)
    base = path.strip()
    if not base or base in {"config", "."}:
        base = ""

    entries: list[SchemaFieldEntry] = []

    def walk(model_type: type[BaseModel], prefix: str, *, parent_required: bool) -> None:
        for name, field in model_type.model_fields.items():
            display_name = field.alias or name
            current_path = f"{prefix}.{display_name}" if prefix else display_name
            field_required = parent_required and field.is_required()
            entries.append(
                SchemaFieldEntry(
                    path=current_path,
                    required=field_required,
                    type_name=_type_name(field.annotation),
                    default_value=_default_repr(field),
                )
            )
            nested_model = _model_from_annotation(field.annotation)
            if nested_model is not None:
                walk(nested_model, current_path, parent_required=field_required)

    walk(model, base, parent_required=True)
    return entries
