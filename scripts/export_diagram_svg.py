#!/usr/bin/env python3
"""Extract a standalone, GitHub-renderable SVG from an Archify HTML artifact.

Archify renders an interactive HTML page that carries the diagram as a single
inline ``<svg>`` styled by a ``<style>`` block in the document head. GitHub will
not render that HTML in a README, so this module lifts the SVG out into a file
that stands on its own:

* the stylesheet is inlined into the SVG and wrapped in CDATA, because an SVG
  referenced by ``<img>`` is parsed as XML and the CSS contains characters (``<``
  in a comment, ``&`` in selectors) that would otherwise end the parse;
* HTML-style valueless attributes are given explicit values, which XML requires;
* the theme is pinned by setting ``data-theme`` on the SVG root, so one artifact
  can be exported once for light and once for dark;
* an opaque background rectangle is injected, so the diagram does not sit on
  whatever colour the reader's page happens to be.

Usage:
    python scripts/export_diagram_svg.py artifact.html out-dark.svg --theme dark
"""

from __future__ import annotations

import argparse
import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path

THEMES = ("dark", "light")


class _XmlRewriter(HTMLParser):
    """Re-serialise an HTML-parsed SVG fragment as well-formed XML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    @staticmethod
    def _attributes(attrs: list[tuple[str, str | None]]) -> str:
        return "".join(f' {name}="{escape(value or "", quote=True)}"' for name, value in attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(f"<{tag}{self._attributes(attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(f"<{tag}{self._attributes(attrs)}/>")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data, quote=False))


def _extract_stylesheet(html: str) -> str:
    match = re.search(r"<style[^>]*>(.*?)</style>", html, re.S)
    if match is None:
        raise ValueError("no <style> block found in the Archify artifact")
    return match.group(1)


def _extract_svg_fragment(html: str) -> str:
    start = html.find("<svg")
    if start == -1:
        raise ValueError("no <svg> element found in the Archify artifact")
    end = html.find("</svg>", start)
    if end == -1:
        raise ValueError("unterminated <svg> element in the Archify artifact")
    return html[start : end + len("</svg>")]


def html_to_standalone_svg(html: str, theme: str = "dark") -> str:
    """Return a self-contained SVG document for ``theme``."""
    if theme not in THEMES:
        raise ValueError(f"theme must be one of {THEMES}, got {theme!r}")

    stylesheet = _extract_stylesheet(html)
    if "]]>" in stylesheet:
        raise ValueError("stylesheet contains ']]>' and cannot be wrapped in CDATA")

    rewriter = _XmlRewriter()
    rewriter.feed(_extract_svg_fragment(html))
    svg = "".join(rewriter.parts)

    # `:root` inside an SVG document is the <svg> element, so the theme blocks
    # apply once the attribute lives here. Dark is the stylesheet default.
    theme_attribute = ' data-theme="light"' if theme == "light" else ""
    svg = svg.replace("<svg", f'<svg xmlns="http://www.w3.org/2000/svg"{theme_attribute}', 1)

    head = svg.find(">") + 1
    prelude = (
        f"<style><![CDATA[\n{stylesheet}\n]]></style>"
        '<rect x="0" y="0" width="100%" height="100%" fill="var(--bg)"/>'
    )
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + svg[:head] + prelude + svg[head:]


def export(artifact: Path, destination: Path, theme: str = "dark") -> Path:
    """Write the standalone SVG for ``artifact`` to ``destination``."""
    svg = html_to_standalone_svg(artifact.read_text(encoding="utf-8"), theme)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(svg, encoding="utf-8")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artifact", type=Path, help="Archify-rendered HTML file")
    parser.add_argument("destination", type=Path, help="path of the SVG to write")
    parser.add_argument("--theme", choices=THEMES, default="dark")
    args = parser.parse_args(argv)

    written = export(args.artifact, args.destination, args.theme)
    print(f"{written} ({written.stat().st_size:,} bytes, {args.theme})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
