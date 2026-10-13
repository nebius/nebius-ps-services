"""Color comments at build time without changing the displayed source text."""

from __future__ import annotations

import html
import io
from pathlib import PurePath
import re
import shlex
import tokenize


def source_language(source: str) -> str:
    """Select the language of a complete source listing, never guess its content."""
    languages = {".py": "python", ".cu": "cpp", ".cpp": "cpp"}
    try:
        return languages[PurePath(source).suffix]
    except KeyError:
        raise ValueError(f"{source}: unsupported code source extension") from None


def _quoted_end(text: str, start: int, *, raw: bool = False) -> int:
    quote = text[start]
    index = start + 1
    while index < len(text):
        if text[index] == quote:
            return index + 1
        index += 2 if text[index] == "\\" and not raw else 1
    raise ValueError("unterminated quoted string")


def _python_comments(text: str) -> list[tuple[int, int]]:
    offsets = [0]
    for line in io.StringIO(text).readlines():
        offsets.append(offsets[-1] + len(line))
    ranges = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            start, end = token.start, token.end
            ranges.append(
                (offsets[start[0] - 1] + start[1], offsets[end[0] - 1] + end[1])
            )
        elif token.type == tokenize.ERRORTOKEN and not token.string.isspace():
            raise ValueError(f"invalid Python token on line {token.start[0]}")
    return ranges


def _c_comments(text: str) -> list[tuple[int, int]]:
    # C++ removes escaped newlines before recognizing comments and strings.
    positions = [i for i in range(len(text))]
    splices = list(re.finditer(r"\\\r?\n", text))
    for match in reversed(splices):
        del positions[match.start() : match.end()]
    logical = "".join(text[i] for i in positions)
    ranges = []
    index = 0
    while index < len(logical):
        start = index
        if logical.startswith('R"', index):
            opening = re.match(r'R"([^\s()\\]{0,16})\(', logical[index:])
            if not opening:
                raise ValueError("invalid C++ raw string delimiter")
            closing = ")" + opening[1] + '"'
            end = logical.find(closing, index + opening.end())
            if end < 0:
                raise ValueError("unterminated C++ raw string")
            index = end + len(closing)
        elif logical[index] in "\"'":
            # A separator in a C++ numeric literal is not a character quote.
            if logical[index] == "'" and re.search(r"\b\d[\w.']*$", logical[:index]):
                index += 1
            else:
                index = _quoted_end(logical, index)
        elif logical.startswith("//", index):
            end = logical.find("\n", index)
            index = len(logical) if end < 0 else end
            ranges.append((positions[start], positions[index - 1] + 1))
        elif logical.startswith("/*", index):
            end = logical.find("*/", index + 2)
            if end < 0:
                raise ValueError("unterminated C++ block comment")
            index = end + 2
            ranges.append((positions[start], positions[index - 1] + 1))
        else:
            index += 1
    return ranges


def _promql_comments(text: str) -> list[tuple[int, int]]:
    ranges = []
    index = 0
    while index < len(text):
        if text[index] in "\"'`":
            index = _quoted_end(text, index, raw=text[index] == "`")
        elif text[index] == "#":
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            ranges.append((index, end))
            index = end
        else:
            index += 1
    return ranges


class _ShellComments:
    """Track shell words, quotes and expansions without executing the example."""

    def __init__(self, text: str):
        self.text = text
        self.ranges: list[tuple[int, int]] = []

    def scan(self, index: int = 0, closing: str = "", *, literal: bool = False) -> int:
        text = self.text
        word_start = True
        command_start = True
        # Each case statement owns pattern-closing ')' characters. They do not
        # close the surrounding command substitution or subshell.
        case_bodies: list[bool] = []
        heredocs: list[tuple[str, bool]] = []
        while index < len(text):
            char = text[index]
            keyword = re.match(
                r"(case|esac|if|then|elif|else|while|until|do|!|\{)(?=$|[\s;|&()<>])",
                text[index:],
            )
            if not literal and keyword and word_start:
                if keyword[1] not in ("case", "esac") and command_start:
                    index += len(keyword[1])
                    word_start = False
                    continue
                if keyword[1] == "case" and command_start:
                    case_bodies.append(False)
                    index += 4
                    word_start = command_start = False
                    continue
                if (
                    keyword[1] == "esac"
                    and case_bodies
                    and (command_start or not case_bodies[-1])
                ):
                    case_bodies.pop()
                    index += 4
                    word_start = command_start = False
                    continue
            if not literal and case_bodies and char == ")":
                case_bodies[-1] = True
                word_start = command_start = True
                index += 1
                continue
            if not literal and case_bodies and text.startswith((";;", ";&"), index):
                case_bodies[-1] = False
            if closing and not case_bodies and text.startswith(closing, index):
                return index + len(closing)
            if char == "\\":
                if text[index : index + 2] != "\\\n":
                    word_start = False
                index += 2
                continue
            if text.startswith("$((", index):
                index = self.scan(index + 3, "))", literal=True)
            elif text.startswith("$(", index):
                index = self.scan(index + 2, ")")
            elif text.startswith("${", index):
                index = self.scan(index + 2, "}", literal=True)
            elif char == "`":
                index = self.scan(index + 1, "`")
            elif char == "'":
                index = _quoted_end(
                    text, index, raw=not (index and text[index - 1] == "$")
                )
            elif char == '"':
                index = self.double_quote(index + 1)
            elif char == "$" and index + 1 < len(text) and text[index + 1] == "#":
                index += 2
            elif not literal and char == "#" and word_start:
                end = text.find("\n", index)
                end = len(text) if end < 0 else end
                self.ranges.append((index, end))
                index = end
                continue
            elif not literal and text.startswith("<<<", index):
                index += 3
                word_start = True
                continue
            elif not literal and text.startswith("<<", index):
                match = re.match(
                    r"<<(-?)[ \t]*((?:[^\s;|&<>\\'\"]|\\.|'[^']*'|\"[^\"]*\")+)",
                    text[index:],
                )
                if not match:
                    raise ValueError("invalid shell here-document delimiter")
                delimiter = shlex.split(match[2])[0]
                heredocs.append((delimiter, bool(match[1])))
                index += match.end()
            elif char == "\n" and heredocs:
                index += 1
                for delimiter, strip_tabs in heredocs:
                    while True:
                        end = text.find("\n", index)
                        line = text[index:] if end < 0 else text[index:end]
                        index = len(text) if end < 0 else end + 1
                        if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                            break
                        if end < 0:
                            raise ValueError("unterminated shell here-document")
                heredocs.clear()
                word_start = True
                continue
            elif char == "(" and not literal:
                index = self.scan(index + 1, ")")
                if case_bodies and not case_bodies[-1]:
                    case_bodies[-1] = True
                    word_start = command_start = True
                    continue
            else:
                word_start = char.isspace() or (not literal and char in ";|&()<>")
                if not literal and char in "\n;|&":
                    command_start = True
                elif not char.isspace():
                    command_start = False
                index += 1
                continue
            word_start = command_start = False
        if closing or heredocs or case_bodies:
            raise ValueError("unterminated shell quote, expansion or here-document")
        return index

    def double_quote(self, index: int) -> int:
        text = self.text
        while index < len(text):
            if text[index] == '"':
                return index + 1
            if text[index] == "\\":
                index += 2
            elif text.startswith("$((", index):
                index = self.scan(index + 3, "))", literal=True)
            elif text.startswith("$(", index):
                index = self.scan(index + 2, ")")
            elif text.startswith("${", index):
                index = self.scan(index + 2, "}", literal=True)
            elif text[index] == "`":
                index = self.scan(index + 1, "`")
            else:
                index += 1
        raise ValueError("unterminated shell double quote")


def render_code(text: str, language: str = "", *, source: str = "") -> str:
    """Return escaped source with comment spans; all original text stays intact."""
    try:
        if language in ("", "text"):
            return html.escape(text)
        if language == "python":
            ranges = _python_comments(text)
        elif language in ("cpp", "cuda"):
            ranges = _c_comments(text)
        elif language == "promql":
            ranges = _promql_comments(text)
        elif language == "bash":
            scanner = _ShellComments(text)
            scanner.scan()
            ranges = scanner.ranges
        else:
            raise ValueError(f"unsupported code language: {language}")
    except (ValueError, tokenize.TokenError, IndentationError, SyntaxError) as error:
        raise ValueError(f"{source or 'code block'}: {language}: {error}") from error
    rendered = []
    position = 0
    for start, end in ranges:
        if not position <= start < end <= len(text):
            raise ValueError(f"{source or 'code block'}: invalid comment offsets")
        rendered.append(html.escape(text[position:start]))
        rendered.append(
            '<span class="code-comment">' + html.escape(text[start:end]) + "</span>"
        )
        position = end
    rendered.append(html.escape(text[position:]))
    return "".join(rendered)
