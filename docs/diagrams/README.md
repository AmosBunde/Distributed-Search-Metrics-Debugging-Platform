# Diagrams

Every diagram here is generated from a specification in [`src/`](src). Nothing is
hand-drawn, and nothing should be edited in place — change the specification and
regenerate.

| Diagram | Type | Shows |
|---|---|---|
| [platform-architecture](src/platform-architecture.json) | architecture | The whole system: ingest, stream, analysis, serving and debugging |
| [telemetry-ingest](src/telemetry-ingest.json) | dataflow | A search event's journey from source to queryable rollup |
| [debug-request](src/debug-request.json) | sequence | What happens when an operator investigates a slow query |
| [anomaly-detection](src/anomaly-detection.json) | workflow | How a closed window becomes an alert — or is suppressed |
| [event-lifecycle](src/event-lifecycle.json) | lifecycle | Every state one telemetry event can occupy |
| [deployment-topology](src/deployment-topology.json) | architecture | What runs in Kubernetes and what is a managed service |

## Regenerating

```bash
make diagrams                                   # all of them
ARCHIFY_HOME=/path/to/archify make diagrams     # if Archify lives elsewhere
.venv/bin/python scripts/build_diagrams.py docs/diagrams/src/event-lifecycle.json   # just one
```

This requires the [Archify](https://github.com/tt-a1i/archify) skill package;
`ARCHIFY_HOME` defaults to `~/.claude/skills/archify`. You only need it to
regenerate — the artifacts are committed, so reading them requires nothing.

Generation refuses to produce a diagram that does not pass Archify's `showcase`
quality profile: zero layout errors and zero warnings, including edge crossings,
ambiguous corridors and label clearance.

## Output files

For a spec named `foo.json`:

| File | Use |
|---|---|
| `foo-dark.svg` / `foo-light.svg` | Embedded in Markdown; GitHub picks by theme |
| `html/foo.html` | Interactive: pan, zoom, search, trace a relationship, guided views, export |

Embed both themes with `<picture>` so the diagram is legible either way:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/platform-architecture-dark.svg">
  <img alt="Describe what the diagram shows" src="docs/diagrams/platform-architecture-light.svg">
</picture>
```

Always write a real `alt` description — the SVG text is not read out by
screen readers when the file is embedded as an image.

## Adding a diagram

1. Pick the type that matches the question the diagram answers: `architecture`
   (what exists), `dataflow` (what moves), `sequence` (what calls what, in what
   order), `workflow` (what decisions get made), `lifecycle` (what states a thing
   passes through).
2. Write `src/<name>.json`. Keep it under about twelve primary nodes: one obvious
   main path with short side branches beats a complete but unreadable map.
3. Run `make diagrams` and fix whatever the validator reports.
4. Add a row to the table above, and reference the diagram from the README or the
   ADR that it explains.
