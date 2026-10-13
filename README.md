# Transit Reliability

Seattle transit reliability analytics for King County Metro service planners.

## Local setup

Requirements: Python 3.12+, `uv`, and the GitHub CLI.

```bash
uv venv --python 3.12
uv sync --all-groups
uv run --group dev python -m pytest
uv run --group dev ruff check .
uv run --group dev python -m mypy src tests
```

Copy `.env.example` to `.env` for local credentials. The `.env` file is ignored by Git.

## Repository plan

The architecture, constraints, manual setup checks, and delivery phases are documented in
`design_doc.md`. Setup observations are recorded in `docs/decisions/0000-setup-notes.md`.
The available realtime fields and their analytical uses are summarized in
`docs/data_inventory.md`.
The source-by-source engineering and product reports, with example records, are
organized in `docs/data_reports/README.md`.
The current implementation and Databricks verification state are recorded in
`docs/status/2026-10-13-phase-2-progress.md`.
Manual setup, validation, Databricks, feed, Git, and troubleshooting commands are collected
in `docs/commands.md`.
The collection pipeline from King County Metro through Cloudflare to Databricks, including
what each pull contains, is explained in `docs/collection_pipeline.md`.
Free-tier limits and current usage are tracked in `docs/limits.md`.
