"""Wrap the Hungarian user-facing string literals inside functions for translation (round 9, decision 19.24).

    python tools/i18n_wrap_py.py central/web.py ...          # rewrite in place
    python tools/i18n_wrap_py.py --check central/web.py      # only list

- "text" -> _("text");  f"text {x} more" -> _("text {x} more").format(x=x)
- only literals inside functions (module-level tables are translated where they are shown), with an accent or a
  „quote” in them (so identifiers, keys and SQL stay untouched);
- never: docstrings, logging calls, comparison operands, subscripts, dict keys, default arguments, text() SQL,
  regexes, and literals already inside _( ... ).
The module gets `from .i18n import gettext as _` when it has none.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PROSE = re.compile("[ÁÉÍÓÖŐÚÜŰáéíóöőúüű„”–…]")
SENTENCE = re.compile(r"^[A-Z][a-z]+(?: [\w,()-]+)+[.?!]$")  # an unaccented Hungarian sentence: "Nincs benne link."
SQL = re.compile(r"\b(SELECT|UPDATE|INSERT|DELETE|FROM|WHERE)\b")


def prose(text: str) -> bool:
    if not re.search(r"[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű]{2,}", text):
        return False  # punctuation only ("…", " -–:.")
    return bool(PROSE.search(text) or (SENTENCE.match(text) and not SQL.search(text)))
SKIP_CALLS = {"info", "warning", "error", "exception", "debug", "critical", "text", "compile", "search", "match",
              "sub", "fullmatch", "findall", "split", "strip", "rstrip", "lstrip", "_", "N_", "gettext", "startswith",
              "endswith", "replace", "get", "strftime", "execute", "print"}


def name_of(expr: str, used: set[str]) -> str:
    base = re.sub(r"\W", "", expr.split("(")[0].split("[")[0].split(".")[-1]) or "v"
    if not re.match(r"[A-Za-z_]", base) or base in ("format",):
        base = "v"
    name, n = base, 2
    while name in used:
        name, n = f"{base}{n}", n + 1
    used.add(name)
    return name


def literal(msg: str, indent: int, width: int = 100) -> str:
    """A string literal, split into implicitly joined pieces on several lines when it is long."""
    def one(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if len(msg) + indent <= width:
        return one(msg)
    pieces, cur = [], ""
    for word in re.split(r"(?<= )", msg):
        if cur and len(cur) + len(word) > width - indent:
            pieces.append(cur)
            cur = ""
        cur += word
    pieces.append(cur)
    return ("\n" + " " * indent).join(one(p) for p in pieces)


class Finder(ast.NodeVisitor):
    def __init__(self, src: str) -> None:
        self.src = src
        self.lines = src.splitlines(keepends=True)
        self.spans: list[tuple[int, int, str]] = []
        self.depth = 0
        self.blocked: set[int] = set()

    def _offset(self, line: int, col: int) -> int:
        # ast columns are UTF-8 byte offsets
        before = sum(len(x) for x in self.lines[: line - 1])
        return before + len(self.lines[line - 1].encode()[:col].decode())

    def block(self, node: ast.AST) -> None:
        for n in ast.walk(node):
            self.blocked.add(id(n))

    def visit_FunctionDef(self, node):  # noqa: N802
        for d in node.args.defaults + node.args.kw_defaults:
            if d is not None:
                self.block(d)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            self.block(node.body[0])
        for dec in node.decorator_list:
            self.block(dec)
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Compare(self, node):  # noqa: N802
        self.block(node)
        self.generic_visit(node)

    def visit_Subscript(self, node):  # noqa: N802
        self.block(node.slice)
        self.generic_visit(node)

    def visit_Dict(self, node):  # noqa: N802
        for k in node.keys:
            if k is not None:
                self.block(k)
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa: N802
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
        if name in SKIP_CALLS:
            self.block(node)
        self.generic_visit(node)

    def visit_Constant(self, node):  # noqa: N802
        if self.depth and isinstance(node.value, str) and id(node) not in self.blocked and prose(node.value):
            a = self._offset(node.lineno, node.col_offset)
            b = self._offset(node.end_lineno, node.end_col_offset)
            self.spans.append((a, b, "_(" + self.src[a:b] + ")"))

    def visit_JoinedStr(self, node):  # noqa: N802
        if not self.depth or id(node) in self.blocked:
            return
        text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
        if not prose(text):
            return
        used: set[str] = set()
        parts, args = [], []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(v.value.replace("{", "{{").replace("}", "}}"))
            else:
                expr = ast.get_source_segment(self.src, v.value) or "v"
                nm = name_of(expr, used)
                conv = {115: "!s", 114: "!r", 97: "!a"}.get(v.conversion, "")
                spec = ""
                if v.format_spec is not None:
                    spec = ":" + "".join(x.value for x in v.format_spec.values if isinstance(x, ast.Constant))
                parts.append("{" + nm + conv + spec + "}")
                args.append(f"{nm}={expr}")
        msg = "".join(parts)
        lit = literal(msg, node.col_offset + 3)
        a = self._offset(node.lineno, node.col_offset)
        b = self._offset(node.end_lineno, node.end_col_offset)
        joined = ", ".join(args)
        sep = ("\n" + " " * (node.col_offset + 2)) if len(joined) > 60 or "\n" in lit else ""
        new = f"_({lit})" + (f".format({sep}{joined})" if args else "")
        self.spans.append((a, b, new))
        self.block(node)


IMPORT = "from .i18n import gettext as _"


def convert(src: str) -> tuple[str, int]:
    tree = ast.parse(src)
    f = Finder(src)
    f.visit(tree)
    spans = sorted(f.spans, key=lambda s: s[0])
    # drop spans nested in an earlier one (a constant inside a converted f-string)
    kept, end = [], -1
    for s in spans:
        if s[0] >= end:
            kept.append(s)
            end = s[1]
    out = src
    for a, b, new in reversed(kept):
        out = out[:a] + new + out[b:]
    if kept and not re.search(r"^from \.+i18n import .*\bgettext as _\b|^from \.+i18n import .*\b_\b", out, re.M):
        tree = ast.parse(out)
        last = max(n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))
        lines = out.split("\n")
        lines.insert(last, IMPORT)
        out = "\n".join(lines)
    return out, len(kept)


def main(argv: list[str]) -> None:
    check = "--check" in argv
    for name in [a for a in argv if not a.startswith("--")]:
        path = Path(name)
        global IMPORT
        IMPORT = "from ..i18n import gettext as _" if path.parent.name == "app" else "from .i18n import gettext as _"
        src = path.read_text()
        out, n = convert(src)
        if check:
            print(f"{n:4} {path}")
        elif out != src:
            ast.parse(out)  # still valid Python
            path.write_text(out)
            print(f"wrapped {n:4}: {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
