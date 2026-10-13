# Transit Reliability

Seattle transit reliability analytics for King County Metro service planners.

## Local setup

Requirements: Python 3.12+, `uv`, and the GitHub CLI.

```bash
uv venv --python 3.12
uv sync --all-groups
uv run pytest
uv run ruff check .
```

Copy `.env.example` to `.env` for local credentials. The `.env` file is ignored by Git.

## Repository plan

The architecture, constraints, manual setup checks, and delivery phases are documented in
`design_doc.md`. Setup observations are recorded in `docs/decisions/0000-setup-notes.md`.
