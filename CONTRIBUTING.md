# Contributing to Stampbot

Stampbot accepts contributions that solve a focused problem. That includes
code, tests, and documentation. Start with the user or operator who needs the
change.

By participating, you agree to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Before you start

Search the [open and closed issues](https://github.com/dannysauer/stampbot/issues)
before you open a new one. Add evidence to an existing issue when it covers the
same problem.

Use the bug report template for unexpected behavior. Include:

- The Stampbot application version, container digest, or chart version
- The deployment mode
- The webhook event and action
- Expected and actual behavior
- The relevant part of a sanitized `stampbot.toml`
- Sanitized logs or a summary of the GitHub webhook response

For a feature proposal, describe who has the problem and what outcome they
need. Discuss changes to GitHub App permissions, public configuration, or
deployment behavior in an issue before writing a large patch.

Do not report a suspected vulnerability in a public issue. Follow the private
process in the [security policy](SECURITY.md).

## Prepare a development checkout

You need Git and Make. Stampbot supports Python 3.11 through 3.14, and CI tests
each version.

The pinned contributor toolchain uses Python 3.14.6 and Poetry 2.4.1. Chart work
uses Helm 4.2.3. CI reads the same version files.

If you use [mise](https://mise.jdx.dev/), install the pinned tools from the
repository root:

```bash
mise install
```

If you don't use mise, install a supported Python version and Poetry yourself.
Install Helm, Docker, and kubeconform before you work on the chart.

Fork the repository and clone your fork. Then run these commands from the
repository root:

```bash
make install-dev
make test
make lint
```

`make install-dev` creates `.venv/` through Poetry. The test and lint targets use
that environment. A successful checkout finishes both commands without errors.

Keep project tools inside `.venv/`. Running pytest, Ruff, or MyPy from a global
environment can use different dependencies than CI.

## Make one coherent change

Keep implementation, tests, and public documentation together.

- Add a regression test for a bug when the behavior can be reproduced safely.
- Test new behavior at the narrowest level that proves it works.
- Update configuration, deployment, interface, and operations docs when their
  source changes.
- Preserve compatibility with existing `stampbot.toml` files unless the change
  is intentionally breaking and the migration is documented.
- Use synthetic webhook payloads and repository data in tests.
- Leave unrelated formatting and refactoring for another pull request.

Stampbot follows the
[Google Python Style Guide](docs/external/google-python-style-guide.md), with
Ruff as the formatter and linter. The active rules live in `pyproject.toml`.
Ruff enforces a 100-character line limit and Google-style docstrings. MyPy
checks types.

The copy of the style guide under `docs/external/` is synchronized from
upstream. Do not edit that generated file by hand.

### Change dependencies

Direct application and development dependencies use exact versions. Preserve
that policy when you add or update one.

Edit `pyproject.toml`, then regenerate the lock file with Poetry:

```bash
poetry lock
make install-dev
make pre-commit
```

The `poetry-export` hook generates `requirements.txt` from the Poetry sources.
Review and stage that generated change. Do not edit `requirements.txt` by hand.

`constraints.txt` pins the installer used by the container build. Change it
only when the build-tool constraint itself changes, and include the package
hash from the published distribution.

### Change GitHub Actions

Pin each third-party `uses:` entry to a full commit SHA. Keep the release
version in an end-of-line comment so reviewers can identify the pin.

Use read-only workflow permissions by default. Grant a job only the additional
permissions it needs, and do not expose repository secrets to untrusted pull
request code.

The SLSA reusable workflows have a documented semantic-tag exception for
provenance verifier compatibility. Check the
[Scorecard findings tracker](https://github.com/dannysauer/stampbot/issues/267)
before you change how those workflows are pinned.

## Check the result

Run every check that applies to the files you changed. This table gives the
normal local commands.

| Change | Commands |
| --- | --- |
| Any change | `make pre-commit` |
| Python behavior | `make lint` and `make test` |
| Helm chart or Kubernetes manifest | `make helm-test` |
| Dependency metadata | `poetry lock`; `make install-dev`; `make pre-commit` |

`make pre-commit` checks all tracked files. It needs the external tools used by
the matching hooks, including Helm and kubeconform for chart files. If you
cannot run a check, name it and explain why in the pull request.

The default test command excludes tests marked `live`. Live tests make network
requests. Run them only against an installation and account you are authorized
to use, and keep credentials and private response data out of test output.

## Name branches and commits

Use a short branch name with a conventional prefix such as `feat/`, `fix/`,
`docs/`, `test/`, `refactor/`, or `chore/`.

Every commit and the pull request title must follow Conventional Commits:

```text
<type>[optional scope][optional !]: <description>
```

The release workflow reads commit subjects on `main`:

- `fix` can produce a patch release
- `feat` can produce a minor release
- A breaking marker can produce a major release

Other commit types do not trigger an application release by themselves.

Every application release also produces a chart release. Without an
application release, any change under `charts/` since the latest reachable
chart tag produces a chart-only release. Only `release.yml` owns automatic
push-triggered planning; manual chart runs serialize with its chart
publication.

Keep the branch history small, and sign commits when you can. Before final
review, rebase onto the current `main`; do not merge `main` into the branch.

The maintainer generally fast-forwards a small branch with clean, signed
commits after its required checks pass. A larger branch may be squash-merged.
The repository does not accept merge commits.

## Open the pull request

Complete the pull request template. Explain the behavior change and why it
belongs in Stampbot. List the exact checks you ran and describe any security or
operator effect.

Link the issue that provides context. Use `Closes #123` only when merging the
pull request should close that issue.

Contributions are submitted under the project's
[Apache License 2.0](LICENSE). You must have the right to submit the work.
Stampbot does not require a separate Contributor License Agreement.
