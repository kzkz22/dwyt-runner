"""Wrap the visible texts of Jinja templates for translation (round 9, decision 19.24), keeping the Hungarian output
byte-identical.

    python tools/i18n_wrap.py central/templates/*.html            # rewrite in place
    python tools/i18n_wrap.py --check central/templates/item.html  # only list what would be wrapped

- a text between tags (with its {{ expressions }}) becomes {{ _("text %(name)s", name=expr) }};
- the translatable attributes (title, placeholder, aria-label, alt, data-confirm, label, meta description) likewise;
- string literals inside {{ }} / {% %} that look like Hungarian prose become _('...').
Leading and trailing whitespace stays outside the call, so the output does not change. The new-style gettext of
jinja2.ext.i18n treats the message as markup and always %-formats it, so a literal % becomes %%.
Run it once per new template; texts already inside _( ... ) are left alone.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

LETTERS = "A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű"
WORD = re.compile(rf"[{LETTERS}]{{2,}}")
ACCENT = re.compile("[ÁÉÍÓÖŐÚÜŰáéíóöőúüű„”–…]")
ATTRS = ("title", "placeholder", "aria-label", "alt", "data-confirm", "label", "data-label")
SKIP_BLOCK = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<pre\b.*?</pre>|<code\b.*?</code>|"
                        r"<kbd\b.*?</kbd>|\{#.*?#\}|\{%-?\s*raw\s*-?%\}.*?"
                        r"\{%-?\s*endraw\s*-?%\}", re.S)
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
TAG = re.compile(r"<(?!!--)[A-Za-z/!][^<>]*?(?:\{\{.*?\}\}[^<>]*?|\{%.*?%\}[^<>]*?)*>", re.S)
STRING = re.compile(r"""(?<![\w.])('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")""")
CALLS_NOT_TEXT = re.compile(r"(icon|asset|local|logo|urlencode|replace|format|startswith|endswith|split|join|get|"
                            r"strftime|attr|selectattr|rejectattr|map|groupby|trans_attr)\s*\($")


def is_prose(text: str) -> bool:
    return bool(WORD.search(text))


def literal_is_prose(body: str) -> bool:
    """A string literal in Jinja code that a user reads: an accent, or a capitalised phrase with a space."""
    if "%" in body or "/" in body and " " not in body:
        return False
    return bool(ACCENT.search(body)) or bool(re.match(rf"[A-ZÁÉÍÓÖŐÚÜŰ][{LETTERS}]*\s+\S", body))


def wrap_literals(code: str) -> str:
    """Hungarian prose literals inside {{ }} / {% %} -> _('...')."""
    out, last = [], 0
    for m in STRING.finditer(code):
        lit, start = m.group(1), m.start(1)
        body = lit[1:-1]
        before = code[:start].rstrip()
        if not literal_is_prose(body) or before.endswith("_(") or CALLS_NOT_TEXT.search(before) \
                or re.search(r"(==|!=|\bin)\s*\(?\s*$", before) or before.endswith("["):
            continue
        out += [code[last:start], f"_({lit})"]
        last = m.end(1)
    return "".join(out) + code[last:]


def var_name(expr: str, used: set[str]) -> str:
    base = re.sub(r"\W", "", expr.split("|")[0].split("(")[0].strip().split(".")[-1].split("[")[0]) or "v"
    if not re.match(r"[A-Za-z_]", base):
        base = "v"
    name, n = base, 2
    while name in used:
        name, n = f"{base}{n}", n + 1
    used.add(name)
    return name


def gettext_call(segment: str) -> str | None:
    """'Text {{ a }} more' -> '{{ _("Text %(a)s more", a=a) }}'; None when there is nothing to translate."""
    plain = JINJA.sub("", segment)
    if not is_prose(plain) or "{%" in segment or "{{-" in segment or "-}}" in segment:
        return None
    parts, args, used = [], [], set()
    pos = 0
    for m in re.finditer(r"\{\{-?(.*?)-?\}\}", segment, re.S):
        parts.append(segment[pos:m.start()].replace("%", "%%"))
        expr = m.group(1).strip()
        name = var_name(expr, used)
        parts.append(f"%({name})s")
        args.append(f"{name}={expr}")
        pos = m.end()
    parts.append(segment[pos:].replace("%", "%%"))
    msg = "".join(parts).replace("\\", "\\\\").replace('"', '\\"')
    return "{{ _(\"" + msg + "\"" + "".join(", " + a for a in args) + ") }}"


def wrap_text(text: str) -> str:
    """A run of text between tags: split at {% statements %}, wrap each prose segment, keep the whitespace out."""
    out, pos = [], 0
    pieces = []
    for m in re.finditer(r"\{%.*?%\}", text, re.S):
        pieces += [("text", text[pos:m.start()]), ("stmt", m.group(0))]
        pos = m.end()
    pieces.append(("text", text[pos:]))
    for kind, piece in pieces:
        if kind == "stmt":
            out.append("{%" + wrap_literals(piece[2:-2]) + "%}")
            continue
        if piece.strip().startswith("{{ _(") and piece.strip().endswith(") }}") and piece.count("{{") == 1:
            out.append(piece)
            continue
        lead = piece[: len(piece) - len(piece.lstrip())]
        trail = piece[len(piece.rstrip()):]
        core = piece.strip()
        call = gettext_call(core) if core else None
        if call is None:
            out.append(re.sub(r"\{\{.*?\}\}", lambda x: "{{" + wrap_literals(x.group(0)[2:-2]) + "}}", piece,
                              flags=re.S))
        else:
            out.append(lead + call + trail)
    return "".join(out)


def wrap_tag(tag: str) -> str:
    def attr(m: re.Match) -> str:
        name, q, value = m.group(1), m.group(2), m.group(3)
        if name not in ATTRS and not (name == "content" and 'name="description"' in tag):
            return re.sub(r"\{\{.*?\}\}", lambda x: "{{" + wrap_literals(x.group(0)[2:-2]) + "}}", m.group(0),
                          flags=re.S)
        return f"{name}={q}{wrap_text(value)}{q}"
    inner = re.sub(r"([\w:-]+)=([\"'])(.*?)\2", attr, tag, flags=re.S)
    return re.sub(r"\{%.*?%\}", lambda x: "{%" + wrap_literals(x.group(0)[2:-2]) + "%}", inner, flags=re.S)


def convert(src: str) -> str:
    out, pos = [], 0
    for m in SKIP_BLOCK.finditer(src):
        out.append(convert_html(src[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(convert_html(src[pos:]))
    return "".join(out)


def convert_html(src: str) -> str:
    out, pos = [], 0
    for m in TAG.finditer(src):
        out.append(wrap_text(src[pos:m.start()]))
        out.append(wrap_tag(m.group(0)))
        pos = m.end()
    out.append(wrap_text(src[pos:]))
    return "".join(out)


def main(argv: list[str]) -> None:
    check = "--check" in argv
    for name in [a for a in argv if not a.startswith("--")]:
        path = Path(name)
        if path.name == "_icons.html":
            continue
        before = path.read_text()
        after = convert(before)
        if check:
            for line in sorted(set(re.findall(r"_\((?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')", after))):
                print(path.name, line)
        elif after != before:
            path.write_text(after)
            print("wrapped:", path)


if __name__ == "__main__":
    main(sys.argv[1:])
