"""Expression parsing for Cognos report expressions and DAX (sections 5.1, 16.2).

One recursive-descent parser serves both dialects: the token shapes differ,
the arithmetic does not. Anything the parser cannot read is flagged PARSE_FAIL
and handled as opaque rather than guessed at.

Normalisation rules a steward would check in the Phase 1 sample (review finding
R-20, specification section 14 falsifier):

* ``DIVIDE(a, b, 0)`` is the standard DAX idiom for a ratio. It normalises to
  ``a / b`` like the Cognos division does, and the alternate-result argument is
  kept as a *null rule* rather than as arithmetic, so a DAX estate that writes
  ``DIVIDE(..., 0)`` everywhere does not show every ratio as a THRESHOLD
  conflict against Cognos.
* Only functions that leave the number unchanged are stripped as formatting.
  ``int()``, ``trunc()`` and ``floor()`` change the number (a truncating bucket
  is not the raw ratio), so they stay in the arithmetic shape even though the
  configuration lists ``int`` among the formatting functions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..config import FORMATTING_FUNCTIONS, PARSER_VERSION, TIME_INTELLIGENCE_FUNCTIONS

AGGREGATE_FUNCTIONS = {
    "agg_sum": "SUM", "agg_avg": "AVG", "agg_count": "COUNT", "agg_countd": "COUNT DISTINCT",
    "agg_max": "MAX", "agg_min": "MIN", "agg_median": "MEDIAN",
    "total": "SUM", "sum": "SUM", "sumx": "SUM",
    "average": "AVG", "avg": "AVG", "averagex": "AVG",
    "count": "COUNT", "countrows": "COUNT", "counta": "COUNT",
    "count_distinct": "COUNT DISTINCT", "distinctcount": "COUNT DISTINCT",
    "maximum": "MAX", "max": "MAX", "maxx": "MAX",
    "minimum": "MIN", "min": "MIN", "minx": "MIN",
    "median": "MEDIAN", "stddev": "STDDEV", "variance": "VARIANCE",
    "percentile": "PERCENTILE", "aggregate": "AGGREGATE",
}
FILTER_FUNCTIONS = {"calculate", "calculatetable", "filter", "keepfilters", "allexcept"}
PASSTHROUGH_FUNCTIONS = {"calculate", "calculatetable", "keepfilters"}
# Tool-specific function names collapse to one token so the same calculation
# fingerprints identically whether it arrived as a Cognos expression or DAX
# (section 16.2: fingerprinting rules are unchanged across tools).
CANONICAL_FUNCTIONS = {
    "total": "agg_sum", "sum": "agg_sum", "sumx": "agg_sum",
    "average": "agg_avg", "avg": "agg_avg", "averagex": "agg_avg",
    "count": "agg_count", "counta": "agg_count", "countrows": "agg_count",
    "distinctcount": "agg_countd", "count_distinct": "agg_countd",
    "maximum": "agg_max", "max": "agg_max", "maxx": "agg_max",
    "minimum": "agg_min", "min": "agg_min", "minx": "agg_min",
    "median": "agg_median", "stddev": "agg_stddev", "variance": "agg_variance",
}
SCOPE_FUNCTIONS = {"values", "all", "allselected", "distinct"}
# Functions that change the number and therefore must never be stripped as
# formatting, whatever the configuration says (R-20b). ``convert`` is stripped
# only when its target type keeps the value (see ``_is_value_changing``).
VALUE_CHANGING_FUNCTIONS = {"int", "trunc", "truncate", "floor", "ceiling", "ceil"}
STRIPPABLE_FUNCTIONS = set(FORMATTING_FUNCTIONS) - VALUE_CHANGING_FUNCTIONS
INTEGER_TYPE_TOKENS = {"int", "integer", "int64", "bigint", "smallint", "whole"}
# Null-handling functions: their presence is a null rule the Stage 6 model must
# document (section 5.4), so it is recorded alongside the shape.
NULL_FUNCTIONS = {"coalesce", "ifnull", "nvl", "isnull", "nullif", "blank", "isblank"}
OPAQUE_MARKERS = ("#", "<#", "sql(", "macro", "$parameter", "javascript")
PROMPT_RE = re.compile(r"\?[A-Za-z0-9_ ]+\?")

_NUMBER = re.compile(r"\d+(\.\d+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_COMPARISON = ("<>", "!=", ">=", "<=", "=", ">", "<")


class ParseError(ValueError):
    pass


@dataclass
class ParsedExpression:
    status: str                         # PARSED | PARSE_FAIL
    ast: dict[str, Any] = field(default_factory=dict)
    filter_ast: list[dict[str, Any]] = field(default_factory=list)
    filter_shape: str = ""
    aggregation: str = ""
    operand_refs: list[str] = field(default_factory=list)
    filter_refs: list[str] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)
    shape: str = ""
    time_modifier: str = ""
    note: str = ""
    parser_version: str = PARSER_VERSION
    # How the calculation treats a missing or zero denominator: the DAX
    # alternate result of a 3-argument DIVIDE, or a COALESCE-style wrapper.
    # Kept out of the fingerprint and surfaced as a documented null rule (R-20a).
    null_rule: str = ""

    @property
    def operand_columns(self) -> list[str]:
        return [r.rsplit(".", 1)[-1] for r in self.operand_refs]

    @property
    def filter_columns(self) -> list[str]:
        return [r.rsplit(".", 1)[-1] for r in self.filter_refs]


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

@dataclass
class Token:
    kind: str       # ref | num | str | ident | op | punct
    value: str
    extra: Any = None


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
            continue
        if ch == "[":
            end = text.find("]", i + 1)
            if end == -1:
                raise ParseError("unterminated [ identifier")
            parts = [text[i + 1:end]]
            j = end + 1
            while j < n and text[j] == "." and j + 1 < n and text[j + 1] == "[":
                end = text.find("]", j + 2)
                if end == -1:
                    raise ParseError("unterminated [ identifier")
                parts.append(text[j + 2:end])
                j = end + 1
            tokens.append(Token("ref", ".".join(parts), parts))
            i = j
            continue
        if ch == "'":
            end = text.find("'", i + 1)
            if end == -1:
                raise ParseError("unterminated quote")
            body = text[i + 1:end]
            j = end + 1
            if j < n and text[j] == "[":
                close = text.find("]", j + 1)
                if close == -1:
                    raise ParseError("unterminated DAX column reference")
                column = text[j + 1:close]
                tokens.append(Token("ref", f"{body}.{column}", [body, column]))
                i = close + 1
                continue
            tokens.append(Token("str", body))
            i = j
            continue
        if ch == '"':
            end = text.find('"', i + 1)
            if end == -1:
                raise ParseError("unterminated quote")
            tokens.append(Token("str", text[i + 1:end]))
            i = end + 1
            continue
        if ch == "?":
            match = PROMPT_RE.match(text, i)
            if match:
                tokens.append(Token("prompt", match.group(0).strip("?")))
                i = match.end()
                continue
            raise ParseError("stray ?")
        match = _NUMBER.match(text, i)
        if match:
            tokens.append(Token("num", match.group(0)))
            i = match.end()
            continue
        match = _IDENT.match(text, i)
        if match:
            word = match.group(0)
            j = match.end()
            # DAX bare table reference: Table[Column]
            if j < n and text[j] == "[":
                close = text.find("]", j + 1)
                if close == -1:
                    raise ParseError("unterminated column reference")
                column = text[j + 1:close]
                tokens.append(Token("ref", f"{word}.{column}", [word, column]))
                i = close + 1
                continue
            tokens.append(Token("ident", word))
            i = j
            continue
        for op in _COMPARISON:
            if text.startswith(op, i):
                tokens.append(Token("op", op))
                i += len(op)
                break
        else:
            if ch in "+-*/":
                tokens.append(Token("op", ch))
                i += 1
            elif ch in "(),":
                tokens.append(Token("punct", ch))
                i += 1
            elif ch == "#":
                raise ParseError("macro marker")
            else:
                raise ParseError(f"unexpected character {ch!r}")
    return tokens


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> Token:
        token = self.peek()
        if token is None:
            raise ParseError("unexpected end of expression")
        self.pos += 1
        return token

    def accept_ident(self, *words: str) -> bool:
        token = self.peek()
        if token and token.kind == "ident" and token.value.lower() in words:
            self.pos += 1
            return True
        return False

    def accept_punct(self, value: str) -> bool:
        token = self.peek()
        if token and token.kind == "punct" and token.value == value:
            self.pos += 1
            return True
        return False

    def expect_punct(self, value: str) -> None:
        if not self.accept_punct(value):
            raise ParseError(f"expected {value!r}")

    # -- grammar --------------------------------------------------------
    def parse(self) -> dict:
        node = self.parse_or()
        # Cognos "expr for [dim], [dim]" scopes an aggregate to a dimension.
        if self.accept_ident("for"):
            dims = [self.parse_primary()]
            while self.accept_punct(","):
                dims.append(self.parse_primary())
            node = {"t": "for", "expr": node, "dims": dims}
        if self.peek() is not None:
            raise ParseError(f"trailing tokens at {self.peek().value!r}")
        return node

    def parse_or(self) -> dict:
        node = self.parse_and()
        while self.accept_ident("or"):
            node = {"t": "op", "op": "or", "args": [node, self.parse_and()]}
        return node

    def parse_and(self) -> dict:
        node = self.parse_not()
        while self.accept_ident("and"):
            node = {"t": "op", "op": "and", "args": [node, self.parse_not()]}
        return node

    def parse_not(self) -> dict:
        if self.accept_ident("not"):
            return {"t": "op", "op": "not", "args": [self.parse_not()]}
        return self.parse_comparison()

    def parse_comparison(self) -> dict:
        node = self.parse_additive()
        token = self.peek()
        if token and token.kind == "op" and token.value in _COMPARISON:
            self.pos += 1
            return {"t": "op", "op": _normalize_operator(token.value),
                    "args": [node, self.parse_additive()]}
        if self.accept_ident("between"):
            low = self.parse_additive()
            if not self.accept_ident("and"):
                raise ParseError("between without and")
            high = self.parse_additive()
            return {"t": "op", "op": "between", "args": [node, low, high]}
        if self.accept_ident("in"):
            self.expect_punct("(")
            items = [self.parse_or()]
            while self.accept_punct(","):
                items.append(self.parse_or())
            self.expect_punct(")")
            return {"t": "op", "op": "in", "args": [node] + items}
        if self.accept_ident("is"):
            negated = self.accept_ident("not")
            if not self.accept_ident("null", "missing"):
                raise ParseError("is without null")
            return {"t": "op", "op": "is_not_null" if negated else "is_null", "args": [node]}
        return node

    def parse_additive(self) -> dict:
        node = self.parse_multiplicative()
        while True:
            token = self.peek()
            if token and token.kind == "op" and token.value in ("+", "-"):
                self.pos += 1
                node = {"t": "op", "op": token.value,
                        "args": [node, self.parse_multiplicative()]}
            else:
                return node

    def parse_multiplicative(self) -> dict:
        node = self.parse_unary()
        while True:
            token = self.peek()
            if token and token.kind == "op" and token.value in ("*", "/"):
                self.pos += 1
                node = {"t": "op", "op": token.value, "args": [node, self.parse_unary()]}
            else:
                return node

    def parse_unary(self) -> dict:
        token = self.peek()
        if token and token.kind == "op" and token.value == "-":
            self.pos += 1
            return {"t": "op", "op": "neg", "args": [self.parse_unary()]}
        return self.parse_primary()

    def parse_primary(self) -> dict:
        token = self.next()
        if token.kind == "num":
            return {"t": "num", "v": token.value}
        if token.kind == "str":
            return {"t": "str", "v": token.value}
        if token.kind == "prompt":
            return {"t": "prompt", "name": token.value}
        if token.kind == "ref":
            parts = token.extra or token.value.split(".")
            return {"t": "ref", "raw": token.value,
                    "table": parts[-2] if len(parts) > 1 else "",
                    "column": parts[-1]}
        if token.kind == "punct" and token.value == "(":
            node = self.parse_or()
            self.expect_punct(")")
            return node
        if token.kind == "ident":
            word = token.value.lower()
            if word == "case":
                return self.parse_case()
            if word in ("null", "true", "false"):
                return {"t": "const", "v": word}
            if word == "distinct":
                return {"t": "fn", "name": "distinct", "args": [self.parse_primary()]}
            if self.accept_punct("("):
                args = []
                if not self.accept_punct(")"):
                    args.append(self.parse_or())
                    while self.accept_punct(","):
                        args.append(self.parse_or())
                    self.expect_punct(")")
                return {"t": "fn", "name": word, "args": args}
            return {"t": "ident", "v": word}
        raise ParseError(f"unexpected token {token.value!r}")

    def parse_case(self) -> dict:
        whens = []
        else_node = None
        subject = None
        if not (self.peek() and self.peek().kind == "ident"
                and self.peek().value.lower() in ("when", "end")):
            subject = self.parse_or()
        while self.accept_ident("when"):
            condition = self.parse_or()
            if not self.accept_ident("then"):
                raise ParseError("case when without then")
            whens.append([condition, self.parse_or()])
        if self.accept_ident("else"):
            else_node = self.parse_or()
        if not self.accept_ident("end"):
            raise ParseError("case without end")
        node = {"t": "case", "whens": whens, "else": else_node}
        if subject is not None:
            node["subject"] = subject
        return node


def _normalize_operator(op: str) -> str:
    return {"!=": "<>", "=": "=="}.get(op, op)


# --------------------------------------------------------------------------
# Normalization and shape
# --------------------------------------------------------------------------

def _is_value_changing(name: str, raw_args: list) -> bool:
    """``convert(x, integer)`` truncates; ``convert(x, double)`` does not (R-20b)."""
    if name in VALUE_CHANGING_FUNCTIONS:
        return True
    if name == "convert" and len(raw_args) > 1:
        target = raw_args[1]
        token = ""
        if isinstance(target, dict):
            token = str(target.get("v") or target.get("name") or "").lower()
        return token in INTEGER_TYPE_TOKENS
    return False


def _strip_formatting(node: dict) -> dict:
    """Formatting functions do not change the number, so they leave the shape."""
    if not isinstance(node, dict):
        return node
    if node.get("t") == "fn":
        name = node["name"]
        raw_args = node.get("args", [])
        args = [_strip_formatting(a) for a in raw_args]
        if name in STRIPPABLE_FUNCTIONS and args and not _is_value_changing(name, raw_args):
            return args[0]
        if name in PASSTHROUGH_FUNCTIONS and name != "divide" and len(args) == 1:
            return args[0]
        return {**node, "args": args}
    if node.get("t") == "op":
        return {**node, "args": [_strip_formatting(a) for a in node.get("args", [])]}
    if node.get("t") == "case":
        return {
            **node,
            "whens": [[_strip_formatting(c), _strip_formatting(v)] for c, v in node["whens"]],
            "else": _strip_formatting(node["else"]) if node.get("else") else None,
        }
    if node.get("t") == "for":
        return {**node, "expr": _strip_formatting(node["expr"]),
                "dims": [_strip_formatting(d) for d in node["dims"]]}
    return node


def _canonicalize(node: Any) -> Any:
    """Rewrite tool-specific spellings into one canonical arithmetic shape."""
    if isinstance(node, list):
        return [_canonicalize(i) for i in node]
    if not isinstance(node, dict):
        return node
    kind = node.get("t")
    if kind == "fn":
        name = node["name"]
        raw_args = node.get("args", [])
        # count(distinct x) and DISTINCTCOUNT(x) are the same aggregation. Test the
        # raw argument before recursion, which would strip the distinct marker.
        if name in ("count", "counta") and len(raw_args) == 1:
            inner = raw_args[0]
            if isinstance(inner, dict) and inner.get("t") == "fn" and inner["name"] == "distinct":
                return {"t": "fn", "name": "agg_countd",
                        "args": [_canonicalize(a) for a in inner.get("args", [])]}
        args = [_canonicalize(a) for a in raw_args]
        # DIVIDE(a, b) and DIVIDE(a, b, alternate) are both the ratio a / b; the
        # alternate result is a null rule, collected separately (R-20a).
        if name == "divide" and len(args) in (2, 3):
            return {"t": "op", "op": "/", "args": args[:2]}
        if name in SCOPE_FUNCTIONS and len(args) == 1:
            return args[0]
        return {"t": "fn", "name": CANONICAL_FUNCTIONS.get(name, name), "args": args}
    if kind == "op":
        return {**node, "args": [_canonicalize(a) for a in node.get("args", [])]}
    if kind == "case":
        return {
            **node,
            "whens": [[_canonicalize(c), _canonicalize(v)] for c, v in node.get("whens", [])],
            "else": _canonicalize(node["else"]) if node.get("else") else None,
        }
    if kind == "for":
        return {**node, "expr": _canonicalize(node["expr"]),
                "dims": [_canonicalize(d) for d in node.get("dims", [])]}
    return node


def collect_null_rules(node: Any, into: list[str]) -> None:
    """Null rules the Stage 6 model must document, read off the raw AST.

    Read before canonicalization, because that step folds the 3-argument DIVIDE
    into a plain division and would lose the alternate-result argument.
    """
    if isinstance(node, list):
        for item in node:
            collect_null_rules(item, into)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("t")
    if kind == "fn":
        name = node["name"]
        args = node.get("args", [])
        if name == "divide" and len(args) == 3:
            into.append(f"divide-alternate:{shape_of(args[2])}")
        elif name in NULL_FUNCTIONS:
            into.append(f"{name}({','.join(shape_of(a) for a in args[1:])})")
        collect_null_rules(args, into)
        return
    for key in ("args", "dims", "expr", "subject", "else"):
        if key in node and node[key] is not None:
            collect_null_rules(node[key], into)
    if kind == "case":
        for condition, value in node.get("whens", []):
            collect_null_rules(condition, into)
            collect_null_rules(value, into)


def split_filters(node: Any) -> tuple[Any, list[Any]]:
    """Split a DAX CALCULATE into its measure and its filter arguments.

    Section 16.2: CALCULATE filter arguments map to ``filter_fp``, so they shape
    the variant rather than the metric.
    """
    if not isinstance(node, dict) or node.get("t") != "fn":
        return node, []
    if node["name"] not in ("calculate", "calculatetable"):
        return node, []
    args = node.get("args", [])
    if not args:
        return node, []
    measure, nested = split_filters(args[0])
    return measure, nested + list(args[1:])


def collect_refs(node: Any, into: list[str], filters: list[str], in_filter: bool = False) -> None:
    if isinstance(node, list):
        for item in node:
            collect_refs(item, into, filters, in_filter)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("t")
    if kind == "ref":
        target = filters if in_filter else into
        if node["raw"] not in target:
            target.append(node["raw"])
        return
    if kind == "fn":
        name = node["name"]
        args = node.get("args", [])
        if name in FILTER_FUNCTIONS and len(args) > 1:
            collect_refs(args[0], into, filters, in_filter)
            for extra in args[1:]:
                collect_refs(extra, into, filters, True)
            return
        collect_refs(args, into, filters, in_filter)
        return
    if kind == "case":
        for condition, value in node.get("whens", []):
            collect_refs(condition, into, filters, in_filter)
            collect_refs(value, into, filters, in_filter)
        if node.get("else"):
            collect_refs(node["else"], into, filters, in_filter)
        if node.get("subject"):
            collect_refs(node["subject"], into, filters, in_filter)
        return
    if kind == "for":
        collect_refs(node["expr"], into, filters, in_filter)
        collect_refs(node["dims"], into, filters, in_filter)
        return
    if kind == "op":
        collect_refs(node.get("args", []), into, filters, in_filter)


def find_aggregation(node: Any) -> str:
    """The outermost aggregate wins; a top-level division is a ratio."""
    if not isinstance(node, dict):
        return ""
    kind = node.get("t")
    if kind == "op" and node.get("op") == "/":
        return "RATIO"
    if kind == "fn":
        name = node["name"]
        if name == "divide":
            return "RATIO"
        if name == "count" and node.get("args"):
            first = node["args"][0]
            if isinstance(first, dict) and first.get("t") == "fn" and first["name"] == "distinct":
                return "COUNT DISTINCT"
        if name in AGGREGATE_FUNCTIONS:
            if name in ("calculate", "calculatetable"):
                return find_aggregation(node["args"][0]) if node.get("args") else ""
            return AGGREGATE_FUNCTIONS[name]
        for arg in node.get("args", []):
            found = find_aggregation(arg)
            if found:
                return found
        return ""
    for key in ("args", "whens", "dims"):
        if key in node:
            for item in node[key]:
                found = find_aggregation(item if not isinstance(item, list) else item[1])
                if found:
                    return found
    if kind == "for":
        return find_aggregation(node["expr"])
    if kind == "case":
        for _condition, value in node.get("whens", []):
            found = find_aggregation(value)
            if found:
                return found
    return ""


def find_time_modifier(node: Any) -> str:
    """Time-intelligence functions normalize to a modifier token (section 16.2)."""
    if isinstance(node, list):
        for item in node:
            found = find_time_modifier(item)
            if found:
                return found
        return ""
    if not isinstance(node, dict):
        return ""
    if node.get("t") == "fn":
        token = TIME_INTELLIGENCE_FUNCTIONS.get(node["name"])
        if token:
            return token
        return find_time_modifier(node.get("args", []))
    for key in ("args", "dims"):
        if key in node:
            found = find_time_modifier(node[key])
            if found:
                return found
    if node.get("t") == "for":
        return find_time_modifier(node["expr"])
    if node.get("t") == "case":
        for condition, value in node.get("whens", []):
            found = find_time_modifier(condition) or find_time_modifier(value)
            if found:
                return found
    return ""


def shape_of(node: Any, ref_names: dict[str, str] | None = None) -> str:
    """Canonical arithmetic shape: structure, operators and literals, refs by name.

    Commutative operands are ordered so ``a + b`` and ``b + a`` agree. Literals
    stay in the shape on purpose: a hard-coded threshold is part of the
    calculation, so ``> 60`` and ``> 59`` must not collapse into one metric.
    """
    if not isinstance(node, dict):
        return "?"
    kind = node.get("t")
    if kind == "ref":
        name = (ref_names or {}).get(node["raw"], node["column"])
        return f"col({name.lower()})"
    if kind == "num":
        value = node["v"]
        return f"lit({float(value):g})"
    if kind == "str":
        return f"lit('{node['v'].lower()}')"
    if kind == "const":
        return f"const({node['v']})"
    if kind == "prompt":
        return "prompt()"
    if kind == "ident":
        return f"id({node['v']})"
    if kind == "fn":
        args = [shape_of(a, ref_names) for a in node.get("args", [])]
        if node["name"] in ("+", "*"):
            args = sorted(args)
        return f"{node['name']}({','.join(args)})"
    if kind == "op":
        op = node["op"]
        args = [shape_of(a, ref_names) for a in node.get("args", [])]
        if op in ("+", "*", "and", "or"):
            args = sorted(args)
        return f"{op}({','.join(args)})"
    if kind == "case":
        whens = sorted(f"{shape_of(c, ref_names)}=>{shape_of(v, ref_names)}"
                       for c, v in node.get("whens", []))
        tail = shape_of(node["else"], ref_names) if node.get("else") else "none"
        return f"case({'|'.join(whens)};else={tail})"
    if kind == "for":
        dims = sorted(shape_of(d, ref_names) for d in node.get("dims", []))
        return f"for({shape_of(node['expr'], ref_names)};{','.join(dims)})"
    return "?"


def collect_literals(node: Any, into: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            collect_literals(item, into)
        return
    if not isinstance(node, dict):
        return
    if node.get("t") in ("num", "str"):
        into.append(str(node["v"]))
        return
    for key in ("args", "dims"):
        if key in node:
            collect_literals(node[key], into)
    if node.get("t") == "case":
        for condition, value in node.get("whens", []):
            collect_literals(condition, into)
            collect_literals(value, into)
        if node.get("else"):
            collect_literals(node["else"], into)
    if node.get("t") == "for":
        collect_literals(node["expr"], into)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def parse_expression(text: str, language: str = "cognos",
                     aggregation_hint: str = "") -> ParsedExpression:
    """Parse one calculation expression into a normalized AST."""
    raw = (text or "").strip()
    if not raw:
        return ParsedExpression(status="PARSE_FAIL", note="empty expression",
                                aggregation=(aggregation_hint or "").upper())
    lowered = raw.lower()
    if any(marker in lowered for marker in OPAQUE_MARKERS):
        return ParsedExpression(
            status="PARSE_FAIL",
            note="expression embeds a report prompt, macro or raw SQL",
            aggregation=(aggregation_hint or "").upper(),
        )
    try:
        ast = Parser(tokenize(raw)).parse()
    except ParseError as exc:
        return ParsedExpression(status="PARSE_FAIL", note=f"parse error: {exc}",
                                aggregation=(aggregation_hint or "").upper())
    except RecursionError:
        return ParsedExpression(status="PARSE_FAIL", note="expression nests too deeply",
                                aggregation=(aggregation_hint or "").upper())

    stripped = _strip_formatting(ast)
    null_rules: list[str] = []
    collect_null_rules(stripped, null_rules)
    measure_ast, filter_args = split_filters(stripped)
    # Time-intelligence arguments become a modifier token, not a filter, so
    # year-over-year variants group with their base measure.
    time_modifier = find_time_modifier(filter_args) or find_time_modifier(measure_ast)
    filter_args = [a for a in filter_args if not find_time_modifier(a)]
    normalized = _canonicalize(measure_ast)
    normalized_filters = [_canonicalize(a) for a in filter_args]
    operands: list[str] = []
    filters: list[str] = []
    collect_refs(normalized, operands, filters)
    for filter_node in normalized_filters:
        collect_refs(filter_node, filters, filters, True)
    literals: list[str] = []
    collect_literals(normalized, literals)
    aggregation = find_aggregation(normalized) or (aggregation_hint or "").upper()
    if _contains_prompt(normalized) or any(_contains_prompt(f) for f in normalized_filters):
        return ParsedExpression(
            status="PARSE_FAIL", ast=normalized,
            note="expression embeds a report-level prompt",
            aggregation=aggregation, operand_refs=operands, filter_refs=filters,
        )
    return ParsedExpression(
        status="PARSED", ast=normalized, filter_ast=normalized_filters,
        aggregation=aggregation, operand_refs=operands, filter_refs=filters,
        literals=literals, shape=shape_of(normalized),
        filter_shape="&".join(sorted(shape_of(f) for f in normalized_filters)),
        time_modifier=time_modifier,
        null_rule="&".join(sorted(set(null_rules))),
    )


def _contains_prompt(node: Any) -> bool:
    if isinstance(node, list):
        return any(_contains_prompt(i) for i in node)
    if not isinstance(node, dict):
        return False
    if node.get("t") == "prompt":
        return True
    for key in ("args", "dims"):
        if key in node and _contains_prompt(node[key]):
            return True
    if node.get("t") == "case":
        for condition, value in node.get("whens", []):
            if _contains_prompt(condition) or _contains_prompt(value):
                return True
        if node.get("else") and _contains_prompt(node["else"]):
            return True
    if node.get("t") == "for" and _contains_prompt(node["expr"]):
        return True
    return False
