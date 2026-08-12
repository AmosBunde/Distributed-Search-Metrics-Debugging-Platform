"""The SVG export turns an Archify artifact into a file GitHub can render.

Three things have to hold or the committed diagrams silently break:
the result must be well-formed XML, the stylesheet must survive intact, and the
theme must be pinned on the SVG root.
"""

import sys
from pathlib import Path

import defusedxml.minidom
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from export_diagram_svg import export, html_to_standalone_svg  # noqa: E402

# A miniature stand-in for an Archify artifact: a stylesheet whose comment
# contains "<html>" (which would end an XML parse if not wrapped in CDATA), and
# an SVG carrying a valueless attribute (invalid in XML) and a self-closing tag.
ARTIFACT = """<!doctype html>
<html><head><style>
  /* theme variables — toggle [data-theme] on <html> */
  :root, [data-theme="dark"] { --bg: #020617; --text: #ffffff; }
  :root[data-theme="light"] { --bg: #f8fafc; --text: #0f172a; }
  .node { fill: var(--bg); stroke: var(--text); }
</style></head>
<body>
<div class="toolbar">chrome that must not be exported</div>
<svg viewBox="0 0 100 50" role="img" data-detail-anchor>
  <rect class="node" x="1" y="2" width="10" height="5"/>
  <text x="4" y="9">Ingest &amp; store</text>
</svg>
<script>console.log("viewer runtime")</script>
</body></html>
"""


@pytest.fixture
def dark() -> str:
    return html_to_standalone_svg(ARTIFACT, "dark")


def test_output_is_well_formed_xml(dark: str, tmp_path: Path) -> None:
    """An SVG embedded via <img> is parsed as XML — a parse error renders nothing."""
    written = tmp_path / "diagram.svg"
    written.write_text(dark, encoding="utf-8")
    defusedxml.minidom.parse(str(written))


def test_stylesheet_is_wrapped_in_cdata(dark: str) -> None:
    assert "<style><![CDATA[" in dark
    assert "--bg: #020617" in dark, "theme variables must survive the extraction"


def test_valueless_attribute_gets_an_explicit_value(dark: str) -> None:
    assert 'data-detail-anchor=""' in dark


def _root_tag(svg: str) -> str:
    start = svg.find("<svg")
    return svg[start : svg.find(">", start)]


def test_dark_is_the_stylesheet_default(dark: str) -> None:
    """`:root` in an SVG document is the <svg> element, and dark is defined there."""
    assert "data-theme" not in _root_tag(dark)


def test_light_theme_is_pinned_on_the_root() -> None:
    light = html_to_standalone_svg(ARTIFACT, "light")
    assert 'data-theme="light"' in _root_tag(light)


def test_background_rectangle_is_injected(dark: str) -> None:
    """Without it the diagram sits on whatever colour the reader's page uses."""
    assert 'fill="var(--bg)"' in dark


def test_viewer_chrome_is_left_behind(dark: str) -> None:
    assert "toolbar" not in dark
    assert "viewer runtime" not in dark


def test_svg_namespace_is_declared(dark: str) -> None:
    assert 'xmlns="http://www.w3.org/2000/svg"' in dark


def test_diagram_content_is_preserved(dark: str) -> None:
    assert 'class="node"' in dark
    assert "Ingest &amp; store" in dark


def test_unknown_theme_is_rejected() -> None:
    with pytest.raises(ValueError, match="theme must be one of"):
        html_to_standalone_svg(ARTIFACT, "solarized")


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ("<html><head></head><body><svg></svg></body></html>", "no <style> block"),
        ("<html><head><style>a{}</style></head><body></body></html>", "no <svg> element"),
        ("<html><head><style>a{}</style></head><body><svg ></body></html>", "unterminated"),
    ],
)
def test_malformed_artifacts_fail_loudly(artifact: str, message: str) -> None:
    """A silent partial export would commit a broken diagram."""
    with pytest.raises(ValueError, match=message):
        html_to_standalone_svg(artifact)


def test_cdata_terminator_in_stylesheet_is_rejected() -> None:
    hostile = ARTIFACT.replace("--bg: #020617;", "--bg: #020617; /* ]]> */")
    with pytest.raises(ValueError, match="CDATA"):
        html_to_standalone_svg(hostile)


def test_export_writes_the_file_and_creates_parent_directories(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.html"
    artifact.write_text(ARTIFACT, encoding="utf-8")
    destination = tmp_path / "nested" / "out.svg"

    assert export(artifact, destination, "light") == destination
    assert 'data-theme="light"' in destination.read_text(encoding="utf-8")


COMMITTED = sorted((ROOT / "docs" / "diagrams").glob("*.svg"))


@pytest.mark.parametrize("svg", COMMITTED, ids=lambda p: p.name)
def test_committed_diagrams_are_well_formed(svg: Path) -> None:
    """Guards against a regenerated diagram landing broken."""
    defusedxml.minidom.parse(str(svg))


def test_every_spec_has_both_themes_committed() -> None:
    specs = {p.stem for p in (ROOT / "docs" / "diagrams" / "src").glob("*.json")}
    assert specs, "no diagram specifications found"
    for name in specs:
        for theme in ("dark", "light"):
            assert (
                ROOT / "docs" / "diagrams" / f"{name}-{theme}.svg"
            ).is_file(), f"{name}: {theme} SVG missing — run `make diagrams`"
        assert (ROOT / "docs" / "diagrams" / "html" / f"{name}.html").is_file()
