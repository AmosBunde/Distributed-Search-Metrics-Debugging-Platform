# Contributing

Thanks for helping improve the platform. This guide covers how the repository is
organised and what a change needs before it can merge.

## Getting set up

```bash
git clone https://github.com/AmosBunde/Distributed-Search-Metrics-Debugging-Platform.git
cd Distributed-Search-Metrics-Debugging-Platform
cp .env.example .env          # edit anything marked "changeme"
make install-dev              # Python tooling
make test-unit                # should pass immediately
```

`make help` lists every target. Targets that are not implemented yet fail with
the issue number that adds them, so you always know where a gap is tracked.

## Repository layout

| Path | Contents |
|---|---|
| `libs/common/` | Shared models, settings, logging and tracing used by every service |
| `services/` | The five backend services plus the dashboard |
| `infrastructure/terraform/` | Modules and per-cloud environments |
| `helm/` | Kubernetes chart |
| `tests/unit`, `tests/integration`, `tests/e2e` | Test suites by infrastructure requirement |
| `scripts/` | Database init SQL, topic creation, diagram export |
| `docs/adr/` | Architecture decision records |
| `docs/diagrams/` | Diagram specs and generated SVG/HTML |

## Working on a change

1. Branch from `main`: `git checkout -b feat/short-slug`.
2. Keep the change scoped to one concern.
3. Add tests. Unit tests must not need infrastructure; anything that does belongs
   in `tests/integration` or `tests/e2e` behind the matching pytest marker.
4. Run `make lint` and `make test-unit` before pushing.
5. Open a PR that references the issue it closes.

## Conventions

- **Python**: formatted and linted with `ruff` (`make format`). Line length 100.
  Type hints on public functions. Pydantic v2 models for anything crossing a
  service boundary.
- **Commits**: imperative subject line, one logical change per commit.
- **Configuration**: every setting is read from the environment via the shared
  settings object, and every new setting is added to `.env.example` with a safe
  default. Never commit a real credential.
- **SQL**: always parameterised. No string interpolation into queries.

## Architecture decisions

Anything that constrains future work — a datastore, a protocol, a deployment
model — gets an ADR in `docs/adr/`. Copy `docs/adr/template.md`, fill it in and
add it to the index. Diagrams are generated, never hand-drawn: edit the spec in
`docs/diagrams/src/` and run `make diagrams`.

## Definition of done

- Tests cover the new behaviour, including its failure modes.
- `make lint`, `make test-unit` and CI are green.
- Documentation reflects what the code actually does. If a change makes a README
  claim untrue, the same PR fixes the README.
