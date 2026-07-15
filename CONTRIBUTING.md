# Contributing to Stampbot

Stampbot welcomes bug reports, documentation fixes, tests, and focused code
changes. Start with the problem you want to solve.

## Before you open an issue

Search [open and closed issues](https://github.com/dannysauer/stampbot/issues)
for the same behavior. If you find one, add new evidence there instead of
splitting the discussion.

For a bug, include:

- the Stampbot version and deployment type;
- the event and action that failed;
- expected and actual behavior;
- a minimal `stampbot.toml` when policy matters; and
- sanitized logs or webhook response details.

For an enhancement, explain the user problem before proposing an interface.

Don't report a suspected vulnerability in a public issue. Follow
[SECURITY.md](SECURITY.md).

## Set up a development checkout

You need Git, Make, Poetry, and Python 3.11 or newer. Clone your fork, then run:

```bash
make install-dev
make test
make lint
```

The Makefile creates `.venv/` through Poetry. Use its executables for direct
commands:

```bash
.venv/bin/pytest tests/test_webhook_handler.py -v
.venv/bin/ruff check stampbot tests
```

Don't install project tools globally or run a different environment by
accident. The repository virtual environment is the one CI behavior is built
around.

## Make the change

Keep code, tests, and docs together.

- Add a regression test for a bug when practical.
- Cover new behavior at the nearest useful level.
- Update configuration, interface, deployment, or operations docs when their
  source changes.
- Keep credentials and real delivery payloads out of fixtures.
- Avoid unrelated formatting and refactoring in a focused change.

Stampbot follows the
[Google Python Style Guide](docs/external/google-python-style-guide.md) with
Ruff formatting, a 100-character line limit, Google-style docstrings, and MyPy
type checks. `pyproject.toml` is the active tool configuration.

When dependencies change, edit `pyproject.toml` and regenerate `poetry.lock`
with Poetry. Don't edit `requirements.txt` by hand; automation exports it from
the Poetry sources.

## Check the work

Run the repository checks before you push:

```bash
make pre-commit
```

For a smaller local loop:

```bash
make lint
make test
```

If you changed anything under `charts/`, also run:

```bash
make helm-test
```

At minimum, confirm that the chart lints and renders with the documented
credential contract. A chart change should add or update a Helm unit or
integration case when behavior changes.

## Name the branch and commits

Use a short branch prefix:

- `feat/` for a feature;
- `fix/` for a bug;
- `docs/` for documentation;
- `test/` for tests;
- `refactor/` for restructuring; or
- `chore/` for maintenance.

Every commit and the pull request title must use Conventional Commits:

```text
<type>[optional scope]: <description>
```

Common types are `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`,
`style`, `perf`, `build`, and `revert`. The pull request title matters because
squash merge turns it into the commit on `main`, where release automation reads
it.

## Open the pull request

Describe the behavior change and the reason for it. Include the commands you
ran and any check you couldn't run.

Before requesting review:

1. Rebase or merge the current `main` according to your normal fork workflow.
2. Run `make pre-commit`.
3. Confirm tests cover changed behavior.
4. Confirm public docs match the implementation.
5. Use a Conventional Commit pull request title.

By contributing, you certify that you have the right to submit the work under
the project's Apache-2.0 license. Stampbot doesn't require a separate
Contributor License Agreement.
