# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

The next release is planned as version 2.0.0 because configuration, installation, and module
import paths changed.

### Added

- Optional description formatting: with `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` set, new
  jobs get a paragraph- and bullet-structured copy of their description in the new
  `job_description_formatted` column, validated to contain the same words as the original.
  `--no-format` skips it for a run; existing databases are upgraded automatically (#28).
- Feed snapshots saved to `log/snapshots/` before parsing, and `--from-snapshot` to import one
  into the database without scraping.
- `--repair-from-snapshot` to fill empty `job_tags` and `job_proposals` of stored jobs from saved
  snapshots; it never overwrites a non-empty value and never adds jobs.
- Per-run counts of posts without skills or a proposal bucket, with a warning when most lack them.
- Real-time scrape progress and inserted/updated/failed/skipped job summaries.
- Scrape summaries now distinguish changed jobs from unchanged existing jobs.
- Manual login support for Google-, Apple-, and two-step verification flows when
  `UPWORK_PASSWORD` is not configured.
- Environment-based credential and runtime configuration.
- Persistent browser profile and optional proxy support.
- Explicit live login validation command.
- Makefile targets for installation, validation, testing, and maintenance.
- Automated tests, type checking, formatting, and pre-commit secret scanning.

### Changed

- Skills and proposal buckets are now parsed in both Best Matches tile layouts, and the bare
  `Proposals:` label is no longer stored as a value (#24).

- Authentication now reuses an already-authenticated persistent browser profile.
- Reorganized database and parsing modules under `upwork_scraper`.
- CI now uses the Makefile quality gate.
- SQLite is the only supported persistence format.
- `UPWORK_FIRST_NAME` is required for reliable job-results parsing.

### Fixed

- Login follows Upwork's redesigned two-step form and waits for the Cloudflare Turnstile check
  before submitting each step, instead of timing out while the old `#login_username` form was
  replaced.

### Removed

- Legacy `settings/config.py`.
- `requirements.txt`.
- Obsolete CSV output artifacts and undocumented CSV behavior.
