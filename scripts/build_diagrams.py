#!/usr/bin/env python3
"""Rebuild every committed diagram from its specification.

For each specification in ``docs/diagrams/src`` this runs Archify's ``deliver``
command — which validates at showcase quality, renders, and only then commits
the HTML — and exports light and dark SVGs for embedding in Markdown.

Archify itself is a skill package that lives outside this repository. Point
``ARCHIFY_HOME`` at it, or keep it at the default location:

    ARCHIFY_HOME=~/.claude/skills/archify make diagrams

The generated artifacts are committed, so contributors who do not have Archify
installed can still read the diagrams; only regenerating them needs the tool.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_diagram_svg import THEMES, export

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "docs" / "diagrams" / "src"
OUTPUT_DIR = REPO_ROOT / "docs" / "diagrams"
DEFAULT_ARCHIFY_HOME = Path.home() / ".claude" / "skills" / "archify"


def archify_home() -> Path:
    home = Path(os.environ.get("ARCHIFY_HOME", DEFAULT_ARCHIFY_HOME)).expanduser()
    if not (home / "bin" / "archify.mjs").is_file():
        raise SystemExit(
            f"Archify not found at {home}.\n"
            "Install the Archify skill and set ARCHIFY_HOME to its directory, "
            "or read the committed diagrams in docs/diagrams/ instead."
        )
    return home


def deliver(home: Path, spec: Path, artifact: Path) -> None:
    """Validate, render and commit one diagram artifact."""
    artifact.parent.mkdir(parents=True, exist_ok=True)
    diagram_type = json.loads(spec.read_text(encoding="utf-8"))["diagram_type"]
    result = subprocess.run(
        [
            "node",
            str(home / "bin" / "archify.mjs"),
            "deliver",
            diagram_type,
            str(spec),
            str(artifact),
            "--quality",
            "showcase",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=home,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"archify deliver failed for {spec.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "specs",
        nargs="*",
        type=Path,
        help="specifications to rebuild (default: every spec in docs/diagrams/src)",
    )
    args = parser.parse_args(argv)

    specs = sorted(args.specs or SPEC_DIR.glob("*.json"))
    if not specs:
        raise SystemExit(f"no diagram specifications found in {SPEC_DIR}")

    home = archify_home()
    for spec in specs:
        name = spec.stem
        artifact = OUTPUT_DIR / "html" / f"{name}.html"
        deliver(home, spec, artifact)
        for theme in THEMES:
            export(artifact, OUTPUT_DIR / f"{name}-{theme}.svg", theme)
        print(f"✓ {name}")
    print(f"\n{len(specs)} diagram(s) rebuilt into {OUTPUT_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
