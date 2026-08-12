"""The README is a promise to an adopter, and promises rot.

Every claim here is one a reader would act on: a command they will run, a URL
they will open, a file they will look for. A test is cheaper than someone
discovering the gap at `git clone`.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def make_targets() -> set[str]:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    return {
        target
        for line in makefile.splitlines()
        if line.startswith(".PHONY:")
        for target in line.replace(".PHONY:", "", 1).split()
    }


class TestCommands:
    def test_every_make_command_it_mentions_exists(self) -> None:
        """A README command that is not a target is a dead end at minute one."""
        # Targets contain digits (test-e2e), so the pattern has to as well.
        mentioned = set(re.findall(r"\bmake ([a-z][a-z0-9-]*)", README))
        missing = mentioned - make_targets()
        assert not missing, f"README references non-existent targets: {sorted(missing)}"

    def test_the_quickstart_is_in_the_right_order(self) -> None:
        """Copy the env file, start the stack, generate traffic, check it."""
        order = ["cp .env.example .env", "make dev", "make simulate", "make check-metrics"]
        positions = [README.index(step) for step in order]
        assert positions == sorted(positions), "the quickstart steps are out of order"

    def test_it_documents_the_port_conflict_it_will_hit(self) -> None:
        """The first failure a new adopter sees deserves a paragraph."""
        assert "port conflict" in README.lower()
        assert "CLICKHOUSE_PORT" in README


class TestDiagrams:
    def test_every_embedded_diagram_exists(self) -> None:
        for match in re.finditer(r'(?:srcset|src)="(docs/diagrams/[^"]+)"', README):
            assert (ROOT / match.group(1)).is_file(), f"missing diagram: {match.group(1)}"

    def test_diagrams_are_embedded_for_both_themes(self) -> None:
        """A dark-only diagram is unreadable for half of GitHub's readers."""
        pictures = README.count("<picture>")
        dark = README.count("-dark.svg")
        light = README.count("-light.svg")

        assert pictures >= 4, "the architecture should be shown, not only described"
        assert dark == light == pictures

    def test_every_diagram_has_a_real_alt_description(self) -> None:
        """Screen readers do not read the text inside an embedded SVG."""
        for alt in re.findall(r'<img alt="([^"]*)"', README):
            assert len(alt) > 40, f"alt text is not descriptive: {alt!r}"

    def test_the_ascii_diagram_is_gone(self) -> None:
        assert "└──" not in README, "the hand-drawn diagram is back"


class TestLinks:
    def test_every_relative_link_resolves(self) -> None:
        for target in re.findall(r"\]\((?!https?://)([^)#]+)", README):
            assert (ROOT / target).exists(), f"broken link: {target}"

    def test_every_adr_is_linked(self) -> None:
        for adr in sorted((ROOT / "docs" / "adr").glob("0*.md")):
            assert adr.name in README, f"{adr.name} is not linked from the README"


class TestClaims:
    def test_it_says_the_terraform_has_never_been_applied(self) -> None:
        """The most important sentence in the document."""
        assert re.search(r"(never|ever) been applied", README, re.IGNORECASE)

    def test_it_says_the_chart_has_never_been_installed(self) -> None:
        assert re.search(r"never (been )?installed", README, re.IGNORECASE)

    def test_it_names_the_stream_engine_that_is_actually_built(self) -> None:
        """The original README promised PyFlink; ADR-0003 changed that."""
        assert "PyFlink" not in README or "not PyFlink" in README or "why not Flink" in README

    def test_the_documented_api_matches_the_gateway(self) -> None:
        gateway = (ROOT / "services" / "api-gateway" / "gateway" / "main.py").read_text(
            encoding="utf-8"
        )
        for path in re.findall(r"`(/api/v1/[a-z/{}_]+)`", README):
            template = path.replace("{trace_id}", "{trace_id}").replace("{query_id}", "{query_id}")
            assert template in gateway, f"README documents {path}, which is not served"

    def test_the_coverage_gate_it_quotes_is_the_real_one(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        gate = re.search(r"COVERAGE_MIN \?= (\d+)", makefile).group(1)
        assert f"gated at {gate}%" in README

    def test_it_does_not_quote_a_test_count_that_will_rot(self) -> None:
        """An absolute count is wrong the next time anyone adds a test."""
        assert not re.search(r"\b\d{3,} (unit )?tests\b", README)

    @pytest.mark.parametrize(
        "port",
        ["3000", "8000", "3001", "16686", "8080", "9090", "9093", "8123"],
    )
    def test_documented_urls_match_the_default_ports(self, port: str) -> None:
        """Every URL in the table must be the port .env.example actually sets."""
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert f"localhost:{port}" in README
        assert f"={port}\n" in env_example, f"port {port} is documented but not a default"
