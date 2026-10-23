"""Collect every translatable text of the runner into dwyt_runner/locale/messages.pot and bring the language files
up to date (round 9, decision 19.24). Needs Babel (in the test extras).

    python tools/i18n_extract.py            # write messages.pot, merge it into every locale/<code>.po
    python tools/i18n_extract.py --check    # exit 1 when messages.pot is out of date (a test runs this)

Sources: _() / N_() in the Python modules (not the LLM prompt modules: those stay Hungarian / English by design) and
in the window's templates. The msgid is the Hungarian text.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_file
from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "dwyt_runner"
LOCALE = PKG / "locale"
POT = LOCALE / "messages.pot"
SKIP_PY = {"i18n.py", "summarize.py", "translate.py", "ask.py", "chapters.py", "categorize.py", "langs.py",
           "formatting.py"}
KEYWORDS = {"_": None, "gettext": None, "N_": None}
JINJA_OPTIONS = {"extensions": "jinja2.ext.i18n", "newstyle_gettext": "true"}


def build() -> Catalog:
    cat = Catalog(project="dwyt-runner", charset="utf-8", fuzzy=False)
    for path in sorted((PKG / "app" / "templates").glob("*.html")):
        for lineno, msg, comments, _ctx in extract_from_file("jinja2.ext:babel_extract", str(path), KEYWORDS,
                                                             options=JINJA_OPTIONS):
            cat.add(msg, locations=[(f"app/templates/{path.name}", lineno)], auto_comments=comments)
    for path in sorted(PKG.rglob("*.py")):
        if path.name in SKIP_PY:
            continue
        rel = path.relative_to(PKG).as_posix()
        for lineno, msg, comments, _ctx in extract_from_file("python", str(path), KEYWORDS):
            if isinstance(msg, str) and msg.strip():
                cat.add(msg, locations=[(rel, lineno)], auto_comments=comments)
    return cat


def render(cat: Catalog) -> bytes:
    buf = io.BytesIO()
    write_po(buf, cat, omit_header=True, sort_output=True, width=0)
    return buf.getvalue()


def main(argv: list[str]) -> int:
    data = render(build())
    if "--check" in argv:
        # the texts only: line numbers differ between Python versions and Windows checks files out with CRLF
        have = set()
        if POT.is_file():
            with POT.open("rb") as f:
                have = {m.id for m in read_po(f) if m.id}
        if have != {m.id for m in build() if m.id}:
            print("dwyt_runner/locale/messages.pot is out of date: python tools/i18n_extract.py")
            return 1
        return 0
    LOCALE.mkdir(exist_ok=True)
    POT.write_bytes(data)
    cat = build()
    for po in sorted(LOCALE.glob("*.po")):
        with po.open("rb") as f:
            old = read_po(f, locale=po.stem)
        old.update(cat, no_fuzzy_matching=True)
        buf = io.BytesIO()
        write_po(buf, old, omit_header=False, sort_output=True, width=0, ignore_obsolete=True)
        po.write_bytes(buf.getvalue())
    print(f"{len(cat)} texts -> {POT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
