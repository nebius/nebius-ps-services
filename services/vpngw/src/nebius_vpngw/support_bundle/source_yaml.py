"""Source-preserving, operator-only YAML redaction. Never serialize configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import ScalarToken

from .addresses import AddressAliases, address_spans

PLACEHOLDER = "REDACTED_SECRET"
_KEY = r"(?:psk|pre[-_ ]?shared[-_ ]?key|password|passwd|secrets?|token|access_token|refresh_token|client_secret|private_key|api[-_ ]?key|credentials?|authorization)"
_SECRET_KEY = re.compile(r"(?i)(?:^|[_ -])" + _KEY + r"(?:$|[_ -](?:value|data|pem)$)")
_REFERENCE = re.compile(r"(?:\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)\Z")
_ASSIGNMENT = re.compile(
    r"(?i)[\"']?\b" + _KEY + r"\b[\"']?[ \t]*[:=][ \t]*"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:''|[^'])*'|[^\r\n,#}]+)"
)
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
_PATTERNS = (
    re.compile(r"(?i)--" + _KEY + r"(?:=|[ \t]+)(?P<value>\"(?:\\.|[^\"\\])*\"|'[^']*'|[^\s]+)"),
    re.compile(r"(?i)\bBearer[ \t]+(?P<value>[A-Za-z0-9._~+/=-]+)"),
    re.compile(r"(?P<value>\b(?:AKIA|ASIA)[A-Z0-9]{16}\b)"),
    re.compile(r"(?P<value>\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b)"),
    re.compile(r"(?<=://)(?P<value>[^\s/@:]+:[^\s/@]+)(?=@)"),
)


def reference(value: str) -> bool:
    return bool(_REFERENCE.fullmatch(value))


def _secret(value: str) -> bool:
    value = value.strip().strip("\"'")
    return bool(value) and value not in {PLACEHOLDER, "[REDACTED]"} and not reference(value)


def _shape(text: str, replacement: str = PLACEHOLDER) -> str:
    """Keep every newline and each line's indentation while removing payload."""
    return re.sub(r"[^\s\r\n][^\r\n]*", replacement, text)


def _payload(token: ScalarToken, text: str) -> tuple[int, int]:
    start, end = token.start_mark.index, token.end_mark.index
    if token.style in ("'", '"'):
        return start + 1, end - 1
    if token.style in ("|", ">"):
        newline = text.find("\n", start, end)
        if newline < 0:
            raise ValueError("Unsafe YAML scalar boundary.")
        return newline + 1, end
    return start, end


def _decoded_spans(token: ScalarToken, text: str) -> list[tuple[int, int]] | None:
    """Map ordinary scalar characters and quoted escapes back to original text."""
    start, end = _payload(token, text)
    raw = text[start:end]
    if raw == token.value:
        return [(i, i + 1) for i in range(start, end)]
    if token.style in (None, "|", ">"):
        # Folding/indentation/chomping only changes whitespace. Match the exact
        # non-whitespace runs and retain their original source positions.
        original = list(re.finditer(r"\S+", raw))
        decoded = list(re.finditer(r"\S+", token.value))
        if [m.group() for m in original] != [m.group() for m in decoded]:
            return None
        result = [(start, start)] * len(token.value)
        previous_raw = previous_decoded = 0
        for before, after in zip(original, decoded, strict=True):
            for index in range(previous_decoded, after.start()):
                result[index] = (start + previous_raw, start + before.start())
            for offset in range(len(after.group())):
                result[after.start() + offset] = (
                    start + before.start() + offset,
                    start + before.start() + offset + 1,
                )
            previous_raw, previous_decoded = before.end(), after.end()
        for index in range(previous_decoded, len(token.value)):
            result[index] = (start + previous_raw, end)
        return result
    if "\n" in raw or "\r" in raw or token.style not in ("'", '"'):
        return None
    chars: list[str] = []
    spans = []
    index = start
    escapes = yaml.scanner.Scanner.ESCAPE_REPLACEMENTS
    while index < end:
        finish = index + 1
        char = text[index]
        if token.style == "'" and text[index : index + 2] == "''":
            char, finish = "'", index + 2
        elif token.style == '"' and char == "\\":
            kind = text[index + 1]
            if kind in "xuU":
                finish = index + 2 + {"x": 2, "u": 4, "U": 8}[kind]
                char = chr(int(text[index + 2 : finish], 16))
            elif kind in escapes:
                char, finish = escapes[kind], index + 2
            else:
                return None
        chars.append(char)
        spans.append((index, finish))
        index = finish
    return spans if "".join(chars) == token.value else None


def _mapping_keys(text: str) -> list[list[tuple[str, str]]]:
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    pending = [root] if root is not None else []
    seen = set()
    result = []
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, MappingNode):
            result.append(
                [(key.tag, key.value) for key, _ in node.value if isinstance(key, ScalarNode)]
            )
            pending.extend(child for pair in node.value for child in pair)
        elif isinstance(node, SequenceNode):
            pending.extend(node.value)
    return result


@dataclass
class SourceYaml:
    text: str
    tokens: list[ScalarToken]
    sensitive: set[int]
    literals: tuple[str, ...]

    @classmethod
    def read(cls, raw: bytes) -> SourceYaml:
        try:
            text = raw.decode("utf-8")
            if "\x00" in text:
                raise ValueError("Unsupported YAML encoding.")
            tokens = []
            for index, token in enumerate(yaml.scan(text, Loader=yaml.SafeLoader)):
                if index >= 32768:
                    raise ValueError("YAML structure limit.")
                if isinstance(token, ScalarToken):
                    tokens.append(token)
            root = yaml.compose(text, Loader=yaml.SafeLoader)
            by_end = {token.end_mark.index: i for i, token in enumerate(tokens)}
            sensitive: set[int] = set()
            budget = [32768]

            def visit(node: Node, secret: bool, ancestors: frozenset[int]) -> None:
                budget[0] -= 1
                if budget[0] < 0 or len(ancestors) > 64 or id(node) in ancestors:
                    raise ValueError("YAML structure limit.")
                ancestors = ancestors | {id(node)}
                if isinstance(node, ScalarNode):
                    if secret and _secret(node.value):
                        sensitive.add(by_end[node.end_mark.index])
                elif isinstance(node, MappingNode):
                    for key, value in node.value:
                        visit(key, False, ancestors)
                        visit(
                            value,
                            secret
                            or (
                                isinstance(key, ScalarNode) and bool(_SECRET_KEY.search(key.value))
                            ),
                            ancestors,
                        )
                elif isinstance(node, SequenceNode):
                    for child in node.value:
                        visit(child, secret, ancestors)

            if root is not None:
                visit(root, False, frozenset())
            literals = {tokens[i].value for i in sensitive}
            # Comment credential assignments are outside normal scalar tokens.
            for match in _ASSIGNMENT.finditer(text):
                value = match["value"].strip().strip("\"'")
                if (
                    _secret(value)
                    and not any(
                        _payload(t, text)[0] <= match.start() < _payload(t, text)[1] for t in tokens
                    )
                    and value not in {"|", ">", "|+", "|-", ">+", ">-"}
                    and not value.startswith("*")
                ):
                    literals.add(value)
            if len(literals) > 1024:
                raise ValueError("YAML secret limit.")
            return cls(text, tokens, sensitive, tuple(sorted(literals, key=len, reverse=True)))
        except (yaml.YAMLError, UnicodeError, RecursionError, KeyError, IndexError):
            raise ValueError("Unsafe YAML source snapshot.") from None

    def redact(self, aliases: AddressAliases) -> tuple[bytes, int]:
        # Source edits use character offsets; UTF-8 re-encoding is byte-identical
        # outside edits, including BOM and CRLF. Never normalize source text.
        edits: list[tuple[int, int, str]] = []

        def add(start: int, end: int, replacement: str) -> None:
            if start < end and self.text[start:end] != replacement:
                edits.append((start, end, replacement))

        for index, token in enumerate(self.tokens):
            start, end = _payload(token, self.text)
            if index in self.sensitive:
                add(start, end, _shape(self.text[start:end]))
                continue
            # Find sensitive decoded content too: YAML escapes cannot conceal it.
            replacements: list[tuple[int, int, str]] = []
            for match in _ASSIGNMENT.finditer(token.value):
                a, b = match.span("value")
                value = match["value"]
                stripped = value.strip().strip("\"'")
                if _secret(stripped):
                    if value[:1] in ("'", '"'):
                        a, b = a + 1, b - 1
                    else:
                        b -= len(value) - len(value.rstrip())
                    replacements.append((a, b, PLACEHOLDER))
            for match in _PEM.finditer(token.value):
                replacements.append((match.start(), match.end(), _shape(match.group())))
            if (
                "-----BEGIN " in token.value
                and "PRIVATE KEY-----" in token.value
                and not _PEM.search(token.value)
            ):
                raise ValueError("Incomplete private key material.")
            for literal in self.literals:
                replacements.extend(
                    (m.start(), m.end(), PLACEHOLDER)
                    for m in re.finditer(re.escape(literal), token.value)
                )
            replacements.extend(
                (a, b, aliases.alias(key)) for a, b, key in address_spans(token.value)
            )
            for pattern in _PATTERNS:
                replacements.extend(
                    (m.start("value"), m.end("value"), PLACEHOLDER)
                    for m in pattern.finditer(token.value)
                    if _secret(m["value"])
                )
            if replacements:
                mapping = _decoded_spans(token, self.text)
                if mapping is None:
                    # Literal source matches remain exact for block/plain multiline.
                    if token.value not in self.text[start:end]:
                        raise ValueError("Ambiguous sensitive YAML scalar.")
                    offset = self.text.index(token.value, start, end)
                    mapping = [(i, i + 1) for i in range(offset, offset + len(token.value))]
                for a, b, replacement in replacements:
                    first, last = mapping[a][0], mapping[b - 1][1]
                    if replacement.startswith(PLACEHOLDER) and "\n" in self.text[first:last]:
                        replacement = _shape(self.text[first:last])
                    add(first, last, replacement)

        # Text-level matches cover comments, anchor names and keys as well.
        for literal in self.literals:
            for match in re.finditer(re.escape(literal), self.text):
                add(match.start(), match.end(), _shape(match.group()))
        for match in _ASSIGNMENT.finditer(self.text):
            start, end = match.span("value")
            if any(
                _payload(t, self.text)[0] <= start < _payload(t, self.text)[1] for t in self.tokens
            ):
                continue
            value = match["value"]
            stripped = value.strip().strip("\"'")
            if (
                not _secret(stripped)
                or stripped.startswith("*")
                or re.fullmatch(r"[|>][1-9+-]*", stripped)
            ):
                continue
            if value[:1] in ("'", '"'):
                start, end = start + 1, end - 1
            else:
                end -= len(value) - len(value.rstrip())
            add(start, end, _shape(self.text[start:end]))
        for pattern in _PATTERNS:
            for match in pattern.finditer(self.text):
                if _secret(match["value"]):
                    add(*match.span("value"), PLACEHOLDER)
        for match in _PEM.finditer(self.text):
            add(match.start(), match.end(), _shape(match.group()))
        for match in re.finditer(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", self.text):
            if not any(start <= match.start() and end >= match.end() for start, end, _ in edits):
                raise ValueError("Incomplete private key material.")
        for start, end, key in address_spans(self.text):
            add(start, end, aliases.alias(key))

        # Prefer enclosing secret edits; reject ambiguous partial overlaps.
        selected: list[tuple[int, int, str]] = []
        for start, end, value in sorted(edits, key=lambda edit: (edit[0], -edit[1])):
            if selected and start < selected[-1][1]:
                if end <= selected[-1][1]:
                    continue
                raise ValueError("Overlapping YAML redaction spans.")
            selected.append((start, end, value))
        result = self.text
        for start, end, replacement in reversed(selected):
            result = result[:start] + replacement + result[end:]
        try:
            before, after = _mapping_keys(self.text), _mapping_keys(result)
            if len(before) != len(after):
                raise ValueError("YAML structure changed during redaction.")
            for original, replaced in zip(before, after, strict=True):
                if len(original) != len(replaced):
                    raise ValueError("YAML structure changed during redaction.")
                keys: dict[tuple[str, str], tuple[str, str]] = {}
                for old, new in zip(original, replaced, strict=True):
                    if new in keys and keys[new] != old:
                        raise ValueError("YAML redaction key collision.")
                    keys[new] = old
        except (yaml.YAMLError, RecursionError):
            raise ValueError("Unsafe YAML after redaction.") from None
        aliases.replacements += sum(value.startswith("PUBLIC_IPV") for _, _, value in selected)
        return result.encode("utf-8"), len(selected)


def unsafe_yaml(raw: bytes, literals: tuple[str, ...] = ()) -> bool:
    try:
        source = SourceYaml.read(raw)
        clean, _ = source.redact(AddressAliases())
        return clean != raw or any(
            literal and (literal in source.text or any(literal in t.value for t in source.tokens))
            for literal in literals
        )
    except ValueError:
        return True
