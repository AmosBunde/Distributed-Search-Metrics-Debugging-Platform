# 0005. One module set, three cloud environments

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The platform is documented as deployable to AWS, Azure and GCP. Each cloud names
the same capability differently — EKS/AKS/GKE, MSK/Event Hubs/Pub-Sub,
RDS/Flexible Server/Cloud SQL — but the application needs the same five things
everywhere: a Kubernetes cluster, a Kafka-compatible stream, PostgreSQL, Redis
and object storage.

Written naively, three cloud environments become three subtly different platforms
that drift apart until only one of them actually works.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/deployment-topology-dark.svg">
  <img alt="Deployment topology: cluster workloads inside a namespace, managed services around them" src="../diagrams/deployment-topology-light.svg">
</picture>

## Decision

`infrastructure/terraform/modules/` holds one module per capability, and
`environments/{aws,azure,gcp}/` compose them. Every module exposes the same
output names for the endpoints the application consumes, so the Helm values for a
cloud differ only in registry, storage class, ingress annotations and endpoints —
never in application configuration.

## Consequences

### What this makes easy

- Adopting one cloud means reading one environment directory, not the whole tree.
- The Helm chart stays cloud-agnostic; `values-aws.yaml` and `values-gcp.yaml`
  differ in a handful of lines.
- A capability added to one cloud has an obvious place in the other two, which
  makes drift visible in review.

### What this makes hard

- The lowest common denominator wins. Cloud-specific features that have no
  equivalent elsewhere either stay unused or become an explicit, documented
  exception.
- Three environments must be kept in step, and only one of them is likely to be
  exercised regularly by any given adopter.

### What we now have to live with

**None of this Terraform has ever been applied.** It is `fmt`-checked and
`validate`-checked in CI, which catches syntax and type errors and nothing else.
Applying it will cost money and will surface real-world issues — quota limits,
IAM propagation delays, provider version pins — that static validation cannot
find. Treat a first apply as a reviewed change, not a formality.

## Alternatives considered

### One cloud only, others documented as future work

Reasonable, and materially less work. Rejected because portability is the point
of the exercise: an adopter should not be blocked by the author's cloud choice.

### An abstraction layer over the providers (Crossplane, Pulumi with shared classes)

Rejected. It adds a dependency and an indirection layer that hides the very
differences an operator needs to see when something breaks in one cloud.

### Terraform workspaces rather than directories

Rejected. Workspaces are meant for identical infrastructure with different
variables. These are three genuinely different provider graphs; separate
directories keep the state boundaries and the provider blocks honest.

## Revisit when

Only one cloud is actually being used a year from now — at which point the other
two directories are unverified documentation and should be marked as such.
