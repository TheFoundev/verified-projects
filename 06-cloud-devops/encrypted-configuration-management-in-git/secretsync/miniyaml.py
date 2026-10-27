"""A deliberately small YAML-subset parser.

secretsync is stdlib-first: it needs to read ``.sops.yaml``-style
configuration without pulling in PyYAML.  The supported subset is the
canonical layout that ``secretsync init`` writes:

* block mappings nested by 2-space indentation
* block sequences (``- item`` / ``- key: value``)
* flow sequences of scalars (``[age1..., age1...]``)
* plain / single-quoted / double-quoted scalars
* ``true`` / ``false`` / ``null`` / integers / floats
* full-line and trailing comments

Anything outside the subset raises :class:`YamlError` instead of
silently mis-parsing, so configuration mistakes fail loudly.
"""

from __future__ import annotations

import re

__all__ = ["YamlError", "loads"]


class YamlError(ValueError):
    """Raised when a document falls outside the supported subset."""


_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_.\-]+|\"[^\"]+\"|'[^']+')\s*:(?:\s+(?P<rest>.*))?$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?$")


def _strip_comment(line: str) -> str:
    """Remove a trailing comment, honouring quotes."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == "'":
            return inner.replace("''", "'")
        return (
            inner.replace("\\\\", "\\")
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
        )
    return value


def _parse_scalar(token: str, lineno: int):
    token = token.strip()
    if token == "":
        return None
    if token[0] in "[{":
        return _parse_flow(token, lineno)
    if token[0] in ("'", '"'):
        try:
            return _unquote(token)
        except IndexError:  # pragma: no cover - defensive
            raise YamlError(f"line {lineno}: unterminated quote") from None
    lowered = token.lower()
    if lowered in ("null", "~"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if _INT_RE.match(token):
        return int(token)
    if _FLOAT_RE.match(token):
        return float(token)
    return token


def _split_flow(body: str, lineno: int) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote = None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf).strip())
            buf = []
        elif ch in "]}":
            raise YamlError(f"line {lineno}: unbalanced flow collection")
        else:
            buf.append(ch)
    if quote:
        raise YamlError(f"line {lineno}: unterminated quote in flow collection")
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_flow(token: str, lineno: int):
    if token.startswith("["):
        if not token.endswith("]"):
            raise YamlError(f"line {lineno}: unterminated flow sequence")
        return [_parse_scalar(p, lineno) for p in _split_flow(token[1:-1], lineno)]
    if token.startswith("{"):
        if token.rstrip() == "{}":
            return {}
        raise YamlError(
            f"line {lineno}: flow mappings are not supported by the mini parser"
        )
    return token  # pragma: no cover - defensive


class _Line:
    __slots__ = ("indent", "text", "lineno")

    def __init__(self, indent: int, text: str, lineno: int):
        self.indent = indent
        self.text = text
        self.lineno = lineno


def _prepare(source: str) -> list[_Line]:
    lines: list[_Line] = []
    for lineno, raw in enumerate(source.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError(f"line {lineno}: tab indentation is not supported")
        text = _strip_comment(raw).rstrip()
        if not text.strip():
            continue
        if text.lstrip().startswith("---"):
            continue
        indent = len(text) - len(text.lstrip(" "))
        lines.append(_Line(indent, text.strip(), lineno))
    return lines


def loads(source: str):
    """Parse a YAML-subset document into Python objects."""
    lines = _prepare(source)
    if not lines:
        return None
    value, index = _parse_block(lines, 0, lines[0].indent)
    if index != len(lines):
        raise YamlError(f"line {lines[index].lineno}: unexpected content")
    return value


def _parse_block(lines: list[_Line], index: int, indent: int):
    if lines[index].text == "-" or lines[index].text.startswith("- "):
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_sequence(lines: list[_Line], index: int, indent: int):
    items = []
    while index < len(lines):
        line = lines[index]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise YamlError(f"line {line.lineno}: bad indentation in sequence")
        if not (line.text == "-" or line.text.startswith("- ")):
            break
        rest = line.text[1:].strip()
        if rest == "":
            raise YamlError(f"line {line.lineno}: empty sequence item is unsupported")
        if _KEY_RE.match(rest) and not rest.startswith(("[", "'", '"')):
            # inline mapping entry inside a sequence item: "- key: value"
            item, index = _parse_inline_item(lines, index, indent, rest)
            items.append(item)
            continue
        items.append(_parse_scalar(rest, line.lineno))
        index += 1
    return items, index


def _parse_inline_item(lines: list[_Line], index: int, indent: int, rest: str):
    """Parse ``- key: value`` plus any nested block belonging to it."""
    match = _KEY_RE.match(rest)
    assert match is not None  # guarded by caller
    key = _unquote(match.group("key"))
    node: dict = {}
    if match.group("rest"):
        node[key] = _parse_scalar(match.group("rest"), lines[index].lineno)
    else:
        node[key] = None
    index += 1
    child_indent = indent + 2
    while index < len(lines) and lines[index].indent >= child_indent:
        if lines[index].indent != child_indent:
            raise YamlError(f"line {lines[index].lineno}: bad indentation")
        sub, index = _parse_mapping(lines, index, child_indent)
        node.update(sub)
    return node, index


def _parse_mapping(lines: list[_Line], index: int, indent: int):
    mapping: dict = {}
    while index < len(lines):
        line = lines[index]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise YamlError(f"line {line.lineno}: bad indentation in mapping")
        if line.text.startswith("- "):
            break
        match = _KEY_RE.match(line.text)
        if not match:
            raise YamlError(f"line {line.lineno}: cannot parse mapping line {line.text!r}")
        key = _unquote(match.group("key"))
        if key in mapping:
            raise YamlError(f"line {line.lineno}: duplicate key {key!r}")
        rest = match.group("rest")
        if rest:
            if rest in ("|", ">", "|-", ">-"):
                raise YamlError(
                    f"line {line.lineno}: block scalars are not supported by the mini parser"
                )
            mapping[key] = _parse_scalar(rest, line.lineno)
            index += 1
            continue
        # nested block or empty value
        if index + 1 < len(lines) and lines[index + 1].indent > indent:
            value, index = _parse_block(lines, index + 1, lines[index + 1].indent)
            mapping[key] = value
        else:
            mapping[key] = None
            index += 1
    return mapping, index
