# 0006. Generate diagrams with Archify and commit them as SVG

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The architecture of this platform is the first thing a reader needs and the last
thing anyone remembers to update. ASCII diagrams in a README — which is what this
repository started with — are cheap to write, impossible to keep accurate, and
unreadable at the size a real system needs.

We wanted diagrams that are generated from a checked-in source of truth, that
render on GitHub without a plugin, and that look right in both light and dark
themes.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/platform-architecture-dark.svg">
  <img alt="Platform architecture: ingest, stream, analyse, serve and debug components" src="../diagrams/platform-architecture-light.svg">
</picture>

## Decision

Diagrams are authored as JSON specifications under `docs/diagrams/src/` and
rendered with [Archify](https://github.com/tt-a1i/archify), which validates
layout quality — crossings, corridors, label clearance — before it will render.
`make diagrams` regenerates everything.

Each diagram produces three committed artifacts:

- `docs/diagrams/<name>-dark.svg` and `-light.svg` for embedding in Markdown,
- `docs/diagrams/html/<name>.html`, the interactive version with pan, zoom,
  search, relationship tracing and guided views.

## Consequences

### What this makes easy

- A diagram is reviewed as a diff of its specification, so an architecture change
  and its picture land in the same commit.
- Layout quality is enforced by a validator rather than by eye: every committed
  diagram passes Archify's showcase profile with zero errors and zero warnings.
- Readers get correct colours in both GitHub themes via `<picture>`, and anyone
  who wants to explore opens the HTML.

### What this makes hard

- Regenerating diagrams needs Archify installed and `ARCHIFY_HOME` pointed at it.
  Contributors who only read the diagrams need nothing.
- Archify emits an interactive HTML page, not a standalone SVG, so
  `scripts/export_diagram_svg.py` lifts the inline SVG out: it inlines the
  stylesheet as CDATA (an SVG loaded via `<img>` is parsed as XML, and the CSS
  contains characters that would otherwise break the parse), gives valueless HTML
  attributes explicit values, pins the theme on the SVG root and injects an
  opaque background.
- The exported SVGs are around 180 KB each because the full stylesheet travels
  with them. That is the price of a self-contained file with no external CSS.

### What we now have to live with

Two committed artifacts per theme per diagram. They are generated files in
version control, which is a deliberate trade: it means the diagrams render for
everyone, including on GitHub, with no build step.

## Alternatives considered

### Mermaid in the Markdown

Tempting — GitHub renders it natively and there is nothing to install. Rejected
because layout is entirely the renderer's choice: no control over where a node
lands, no boundary regions, no security-group framing, and complex diagrams
degrade into unreadable spaghetti at exactly the size where a diagram matters.

### Hand-drawn diagrams (Excalidraw, draw.io, Figma)

Rejected. They look good and they rot. The source is a binary or a service, the
diff is meaningless in review, and updating one is a chore that gets skipped.

### Keep the ASCII block

Rejected. It cannot express boundaries, it cannot be read at a glance, and it was
already inaccurate.

## Revisit when

GitHub renders a declarative diagram format with real layout control, or Archify
stops being maintained. The specifications are plain JSON, so the topology could
be re-rendered by another tool without re-authoring the content.
