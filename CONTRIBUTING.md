# Contributing to Upwork Scraper

Thanks for your interest in contributing. Issues, documentation improvements, bug fixes, and
feature proposals are welcome.

## Before you start

- Search existing issues before opening a new one.
- Use the issue templates for bugs and feature requests.
- Do not include Upwork credentials, cookies, database files, browser profiles, or other private
  data in issues, pull requests, logs, or test fixtures.
- Do not report security vulnerabilities through a public issue. See [SECURITY.md](SECURITY.md).

## Development setup

The project requires Python 3.11 or newer and uses `uv` for environment and dependency
management:

```bash
uv sync --locked
```

Install the optional pre-commit hooks if you will contribute regularly:

```bash
uv run pre-commit install
```

## Local checks

Before opening a pull request, run the complete quality gate:

```bash
make check
```

This runs the tests, Ruff lint and formatting checks, and mypy. You can also run the hooks over
the whole repository with:

```bash
uv run pre-commit run --all-files
```

The live login validation is manual and is not required for ordinary documentation or code
changes:

```bash
make validate-login
```

## Branches and pull requests

Create a focused branch from `main`. Use a descriptive prefix such as `fix/`, `feature/`,
`docs/`, or `chore/`.

Keep each pull request focused and explain:

- what changed and why;
- how the change was tested;
- any documentation, configuration, or migration impact; and
- any follow-up work that remains.

Link the relevant issue when one exists. Add or update tests for behavioral changes and update
the README or changelog when user-visible behavior changes. Documentation-only maintenance does
not need a changelog entry unless it changes user-facing project guidance.

## Commit and review expectations

Keep commits clear and narrowly scoped. Pull requests should pass CI and address review feedback
before merging. Maintainers may request changes, split a pull request, or decline a proposal when
it does not fit the project’s scope or quality standards.

By contributing, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
