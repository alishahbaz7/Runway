"""The house look, held on every page — static checks a screenshot cannot keep true over time.

* One icon map (shell.js): every icon any page names exists in it — a missing one renders as an
  empty button with no error anywhere — and no page keeps a private copy.
* Sentence case: no stylesheet or page forces text to capitals, and the rail's labels read as
  sentences ("Packet parser", not "Packet Parser"; acronyms such as OTA keep their case).
* No emoji or text arrows standing in for icons on controls.
* No browser dialogs: confirm/prompt/alert are unstyled, show the host and can be suppressed —
  pages use the shell's own dialog (RunwayUI.confirm / RunwayUI.prompt).
* No style rule on a bare structural element (main, aside, section, header, nav): such rules
  leaked into newer pages more than once.
* Every page is framed by the shell, and every stylesheet's braces balance.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "runway" / "web"
SHELL = (WEB / "shell.js").read_text(encoding="utf-8")
PAGES = sorted(p for p in WEB.glob("*.html") if "location.replace" not in p.read_text(encoding="utf-8"))
SCRIPTS = sorted(WEB.glob("*.js"))
STYLES = sorted(WEB.glob("*.css"))

# Glyphs that once stood in for icons on buttons and labels.
GLYPHS = "▲▼▸▾⤓⤒✕●■▶↻⟳⇄⇪▤◉↗🔁ⓘ✈⟲✓✗⚠🗑⚙🔍✎"


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(js: str) -> str:
    """The script without its comments (a comment may name a glyph or a browser dialog)."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?<![:\"'\\])//[^\n]*", "", js)


def _icon_map() -> set[str]:
    block = SHELL[SHELL.index("const ICONS = {"):SHELL.index("};", SHELL.index("const ICONS = {"))]
    return set(re.findall(r'^\s*"([a-z-]+)":\s*svg\(', block, re.M))


def test_the_pages_are_found():
    names = {p.name for p in PAGES}
    assert {"index.html", "parser.html", "capture.html", "ota.html", "imu.html"} <= names


def test_every_icon_used_exists():
    icons = _icon_map()
    assert len(icons) > 40
    used: set[str] = set()
    for p in PAGES:
        used |= set(re.findall(r'data-icon="([a-z-]+)"', _text(p)))
    for p in SCRIPTS:
        js = _text(p)
        used |= set(re.findall(r'\b(?:setLabel|iconBtn|withIcon)\([^,()]*,\s*"([a-z-]+)"', js))
        used |= set(re.findall(r'\b(?:UI|RunwayUI)\.icon\("([a-z-]+)"\)', js))
        used |= set(re.findall(r'\bic\("([a-z-]+)"\)', js))
        used |= set(re.findall(r'\b(?:ICONS|ICON|icons)\.([a-z][a-zA-Z]*)\b', js))
        # a ternary choosing between two icons: iconBtn(…, open ? "chevron-down" : "chevron-right")
        for expr in re.findall(r'\b(?:setLabel|iconBtn)\([^,()]*,\s*([^,()]*\?[^,()]*:[^,()]*),', js):
            used |= set(re.findall(r'"([a-z-]+)"', expr))
    missing = sorted(used - icons)
    assert not missing, f"icons used but not in shell.js: {missing}"


def test_no_page_keeps_a_private_icon_map():
    for p in SCRIPTS:
        if p.name == "shell.js":
            continue
        assert "<svg" not in _code(_text(p)), f"{p.name}: draws its own SVG — use RunwayUI.icons"


def test_sentence_case_nothing_forced_to_capitals():
    for p in [*STYLES, *PAGES]:
        assert "uppercase" not in _text(p), f"{p.name}: text-transform: uppercase"


def test_rail_labels_read_as_sentences():
    labels = re.findall(r'label: "([^"]+)"', SHELL) + re.findall(r'title: "([^"]+)", items', SHELL)
    assert len(labels) > 10
    for label in labels:
        for word in label.split()[1:]:
            assert word.islower() or word.isupper(), f"rail label not in sentence case: {label!r}"


def test_no_glyphs_standing_in_for_icons():
    for p in PAGES:
        found = sorted({g for g in GLYPHS if g in _text(p)})
        assert not found, f"{p.name}: {found} — use an icon from shell.js"
    for p in SCRIPTS:
        found = sorted({g for g in GLYPHS if g in _code(_text(p))})
        assert not found, f"{p.name}: {found} — use an icon from shell.js"


def test_no_browser_dialogs():
    for p in SCRIPTS:
        hits = re.findall(r"(?<![\w.$])(?:window\.)?(?:confirm|prompt|alert)\(", _code(_text(p)))
        assert not hits, f"{p.name}: {hits} — use RunwayUI.confirm / RunwayUI.prompt"


def test_no_rule_on_a_bare_structural_element():
    for p in STYLES:
        css = re.sub(r"/\*.*?\*/", "", _text(p), flags=re.S)
        for selectors in re.findall(r"(?:^|})\s*([^{}@]+?)\s*{", css):
            for sel in selectors.split(","):
                sel = sel.strip()
                assert not re.match(r"(main|aside|section|header|nav)\b(?![-\w])", sel), \
                    f"{p.name}: a rule on bare {sel!r} — style the element by its class or id"


def test_every_page_is_framed_by_the_shell():
    for p in PAGES:
        html = _text(p)
        assert 'src="shell.js"' in html, f"{p.name}: no shell"
        assert re.search(r'<body data-page="[a-z]+"', html), f"{p.name}: no data-page"
        assert '<div id="content">' in html, f"{p.name}: no #content for the shell to frame"


def test_stylesheets_balance():
    for p in STYLES:
        css = _text(p)
        assert css.count("{") == css.count("}"), f"{p.name}: unbalanced braces"
