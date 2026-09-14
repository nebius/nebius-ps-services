"""Bounded, safe YAML and standard-field validation shared by skill checks."""

from __future__ import annotations

import unicodedata
from pathlib import Path

MAX_BYTES = 1024 * 1024
STANDARD_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
BOOLEAN_EXTENSIONS = {"disable-model-invocation", "user-invocable", "background"}
STRING_EXTENSIONS = {"argument-hint", "when_to_use", "model", "agent", "effort", "context", "shell"}
LIST_EXTENSIONS = {"arguments", "paths", "disallowed-tools"}
HOST_EXTENSIONS = BOOLEAN_EXTENSIONS | STRING_EXTENSIONS | LIST_EXTENSIONS | {"hooks"}


class FormatError(ValueError):
    """Sanitized input/dependency diagnostic; never contains YAML values."""


def read_text(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise FormatError("must be a regular, non-symlink file")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise FormatError("exceeds the 1 MiB validation limit")
        return raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise FormatError("cannot read UTF-8 file") from exc


def yaml_mapping(text: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise FormatError("PyYAML unavailable; install scripts/requirements.txt in your Python environment") from exc

    class Loader(yaml.SafeLoader):
        depth = 0
        nodes = 0
        mapping_entries = 0
        active_anchors = set()
        checked_mappings = set()
        merge_key = object()

        def compose_node(self, parent, index):
            event = self.peek_event()
            alias = isinstance(event, yaml.AliasEvent)
            anchor = getattr(event, "anchor", None)
            if alias and anchor in self.active_anchors:
                raise FormatError("cyclic YAML aliases are not supported")
            self.depth += 1
            self.nodes += 1
            if self.depth > 40 or self.nodes > 10000:
                raise FormatError("YAML exceeds validation complexity limits")
            if anchor and not alias:
                self.active_anchors.add(anchor)
            try:
                return super().compose_node(parent, index)
            finally:
                self.depth -= 1
                if anchor and not alias:
                    self.active_anchors.remove(anchor)

        def flatten_mapping(self, node):
            self.mapping_entries += len(node.value)
            if self.mapping_entries > 10000:
                raise FormatError("YAML exceeds validation complexity limits")
            # Validate explicit keys before flattening introduces valid inherited
            # overrides. Alias nodes may have already been flattened in place.
            if node not in self.checked_mappings:
                self.checked_mappings.add(node)
                keys = set()
                for key_node, _ in node.value:
                    if key_node.tag == "tag:yaml.org,2002:value":
                        key_node.tag = "tag:yaml.org,2002:str"
                    key = (self.merge_key if key_node.tag == "tag:yaml.org,2002:merge"
                           else self.construct_object(key_node))
                    try:
                        if key in keys:
                            raise FormatError("duplicate YAML mapping key")
                        keys.add(key)
                    except TypeError as exc:
                        raise FormatError("YAML mapping keys must be scalar") from exc
            super().flatten_mapping(node)

        def construct_mapping(self, node, deep=False):
            self.flatten_mapping(node)
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                try:
                    mapping[key] = self.construct_object(value_node, deep=deep)
                except TypeError as exc:
                    raise FormatError("YAML mapping keys must be scalar") from exc
            return mapping

    try:
        data = yaml.load(text, Loader=Loader)
    except (yaml.YAMLError, RecursionError, ValueError) as exc:
        if isinstance(exc, FormatError):
            raise
        raise FormatError("malformed or unsupported YAML") from exc
    if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
        raise FormatError("YAML must be a mapping with string keys")
    return data


def frontmatter(path: Path) -> dict:
    lines = read_text(path).splitlines()
    if not lines or lines[0] != "---":
        raise FormatError("missing YAML front matter at the first line")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise FormatError("front matter is not closed") from exc
    return yaml_mapping("\n".join(lines[1:end]))


def standard_name(name: str) -> bool:
    normalized = unicodedata.normalize("NFKC", name)
    return (1 <= len(normalized) <= 64 and normalized == normalized.lower()
            and not normalized.startswith("-") and not normalized.endswith("-")
            and "--" not in normalized
            and all(c.isalnum() or c == "-" for c in normalized))


def validate_fields(data: dict) -> tuple[list[str], list[str], list[str]]:
    failures = []
    for key, limit in (("name", 64), ("description", 1024), ("compatibility", 500)):
        value = data.get(key)
        if key == "compatibility" and key not in data:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            failures.append(f"{key} must be a non-empty string of at most {limit} characters")
    for key in ("license", "allowed-tools"):
        if key in data and not isinstance(data[key], str):
            failures.append(f"{key} must be a string under the Agent Skills standard")
    if "metadata" in data:
        value = data["metadata"]
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                              for k, v in value.items()):
            failures.append("metadata must map string keys to string values")
    standard_failures = failures
    failures = []
    unknown = data.keys() - STANDARD_FIELDS - HOST_EXTENSIONS
    if unknown:
        failures.append("unrecognized frontmatter extension; review against official host documentation")
    extensions = sorted(data.keys() & HOST_EXTENSIONS)
    for key in extensions:
        value = data[key]
        if key in BOOLEAN_EXTENSIONS and type(value) is not bool:
            failures.append(f"{key} must be a YAML boolean (true or false)")
        elif key in STRING_EXTENSIONS and not isinstance(value, str):
            failures.append(f"{key} must be a string")
        elif key in LIST_EXTENSIONS and not (isinstance(value, str) or
                                             isinstance(value, list) and all(isinstance(v, str) for v in value)):
            failures.append(f"{key} must be a string or list of strings")
        elif key == "hooks" and not isinstance(value, dict):
            failures.append("hooks must be a mapping; native hook behavior requires separate validation")
        if key == "context" and value != "fork":
            failures.append("context must be fork")
        if key == "effort" and (not isinstance(value, str) or value not in {"low", "medium", "high", "xhigh", "max"}):
            failures.append("effort must use a documented Claude effort level")
        if key == "shell" and (not isinstance(value, str) or value not in {"bash", "powershell"}):
            failures.append("shell must be bash or powershell")
    return standard_failures, failures, extensions
