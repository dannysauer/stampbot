# Roadmap

This roadmap records direction, not a delivery promise. Issues and pull
requests remain the place for concrete work.

## Current direction

- Keep label and ChatOps approval behavior predictable across GitHub changes.
- Keep the container and Helm deployment paths tested.
- Make policy failures visible without exposing credentials or private data.
- Keep dependencies, actions, and base images pinned and updated.
- Keep release verification honest about the artifacts each release publishes.

## Work worth doing next

- Publish and verify image identity and SLSA provenance end to end.
- Continue the OpenSSF Best Practices Silver work where its criteria fit a
  single-maintainer project.
- Tune mutation and fuzzing jobs so they find useful defects without hiding
  normal pull request feedback.
- Tighten repository-policy validation without silently breaking existing
  `stampbot.toml` files.

## Boundaries

Stampbot doesn't aim to:

- merge pull requests;
- grant repository access;
- bypass GitHub branch protection;
- act as a native `CODEOWNERS` identity; or
- replace human review where a project requires human judgment.

## Propose a change

Open a [GitHub issue](https://github.com/dannysauer/stampbot/issues). Describe
the problem, who has it, the behavior you expect, and any compatibility or
security cost.
