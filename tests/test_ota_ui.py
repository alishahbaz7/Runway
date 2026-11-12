"""Device OTA page hygiene — the static checks a screenshot cannot keep true over time.

* Every icon the page names exists in the shared icon map (shell.js): a missing one renders as
  an empty button with no error anywhere.
* No bare <main>/<section>/<aside>: Runway's global rules for those (flex: 1, display: grid,
  overflow: auto) leaked into this page once and spread its panes apart.
* Sentence case: nothing on the page is forced to upper case.
* Every class the markup uses has a rule, in ota.css or the shared style.css.

The same checks for every page live in test_ui.py.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "runway" / "web"
HTML = (WEB / "ota.html").read_text(encoding="utf-8")
JS = (WEB / "ota.js").read_text(encoding="utf-8")
CSS = (WEB / "ota.css").read_text(encoding="utf-8")
SHARED_CSS = (WEB / "style.css").read_text(encoding="utf-8")
SHELL = (WEB / "shell.js").read_text(encoding="utf-8")


def _icon_map() -> set[str]:
    block = SHELL[SHELL.index("const ICONS = {"):SHELL.index("};", SHELL.index("const ICONS = {"))]
    return set(re.findall(r'^\s*"([a-z-]+)":\s*svg\(', block, re.M))


def test_every_icon_used_exists():
    icons = _icon_map()
    assert len(icons) > 10
    used = set(re.findall(r'data-icon="([a-z-]+)"', HTML))
    used |= set(re.findall(r'\bic\("([a-z-]+)"\)', JS))
    used |= set(re.findall(r"\bICONS\.([a-zA-Z]+)", JS))
    for expr in re.findall(r"\bICONS\[([^\]]+)\]", JS):
        used |= set(re.findall(r'"([a-z-]+)"', expr))
    missing = sorted(used - icons)
    assert not missing, f"icons used but not defined: {missing}"


def test_no_bare_structural_elements():
    for tag in ("main", "section", "aside", "nav"):
        assert f"<{tag}" not in HTML, f"<{tag}> picks up Runway's global {tag} rules — use a <div class>"


def test_sentence_case_nothing_forced_to_upper():
    # headings and labels are written in sentence case; no rule may force them to capitals
    assert "uppercase" not in CSS
    assert "uppercase" not in HTML


def test_every_class_in_the_markup_has_a_rule():
    classes = set()
    for attr in re.findall(r'class="([^"]+)"', HTML):
        classes.update(attr.split())
    rules = CSS + SHARED_CSS
    undefined = sorted(c for c in classes if not re.search(r"\." + re.escape(c) + r"(?![\w-])", rules))
    assert not undefined, f"classes with no CSS rule: {undefined}"


def test_stylesheets_balance():
    for name, text in (("ota.css", CSS), ("style.css", SHARED_CSS)):
        assert text.count("{") == text.count("}"), f"{name}: unbalanced braces"
