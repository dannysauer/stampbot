# Contributing to Stampbot

Stampbot accepts bug reports, enhancement requests, documentation improvements, tests, and
code changes through GitHub Issues and pull requests.

## Reporting Bugs and Requesting Features

Use GitHub Issues for public bug reports and enhancement requests:

https://github.com/dannysauer/stampbot/issues

For suspected security vulnerabilities, do not open a public issue. Follow the private
reporting process in [SECURITY.md](SECURITY.md).

## Acceptable Contributions

Contributions are expected to:

- be written and discussed in English
- keep behavior, tests, and documentation aligned
- use conventional commit messages and pull request titles
- follow the Python style and typing rules used by this repository
- pass the repository's lint, type-check, test, and pre-commit checks before merge
- avoid unrelated formatting or refactoring in focused fixes
- avoid committing generated files unless the repository documents them as generated outputs
- avoid committing secrets, credentials, tokens, or private keys

By contributing, you certify that you have the right to submit the contribution under this
project's Apache-2.0 license. This is the project's Developer Certificate of Origin style
contribution requirement; no separate CLA is required.

## Development Requirements

Use the repository virtual environment through Makefile targets. The Makefile creates and
uses `.venv/` automatically.

```bash
make install-dev
make test
make lint
make pre-commit
```

Do not rely on globally installed Python tooling for local development. If you need to run a
single tool directly, use the executable from `.venv/bin/` or activate the virtual
environment first.

## Coding Standard

Stampbot follows the [Google Python Style Guide](docs/external/google-python-style-guide.md)
with these repository-specific requirements:

- Python code is formatted with Ruff.
- Line length is 100 characters.
- Public APIs use type annotations.
- MyPy strict type checks must pass.
- Docstrings use Google style where public documentation is required.
- Imports are grouped as standard library, third-party, and local imports.

The active tool configuration is in [pyproject.toml](pyproject.toml).

## Commit and Branch Format

Commits and pull request titles must use conventional commit format:

```text
<type>[optional scope]: <description>
```

Common types are `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `style`,
`perf`, `build`, and `revert`.

Use conventional branch prefixes:

- `feat/*` for features
- `fix/*` for bug fixes
- `docs/*` for documentation
- `test/*` for tests
- `refactor/*` for refactoring
- `chore/*` for maintenance

## Pull Request Checklist

Before opening or updating a pull request:

1. Add or update tests when behavior changes.
2. Update README, installation, chart, or reference documentation when user-visible behavior
   changes.
3. Run `make pre-commit`.
4. Confirm the pull request title uses conventional commit format.

Major new functionality should include automated tests covering the new behavior. Bug fixes
should include a regression test when practical.

## Helm Chart Changes

When changing files under `charts/`, run:

```bash
make helm-test
```

At minimum, chart changes must pass Helm linting and template rendering.
