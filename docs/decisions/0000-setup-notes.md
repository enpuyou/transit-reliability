# Setup Notes

Date: 2026-10-13

## Local checks

- Python 3.14.5, `uv` 0.11.30, Node 24.12.0, GitHub CLI 2.92.0, and Databricks CLI 1.20.0 are installed.
- GitHub CLI is authenticated as `enpuyou`.
- Databricks profile `enpu` is valid for the configured workspace. The `DEFAULT` profile is stale.
- KCM vehicle positions, trip updates, and alerts feeds are reachable and saved under `tests/fixtures/`.
- Feed sizes at setup time: vehicle positions 50,817 bytes; trip updates 1,142,067 bytes; alerts 45,614 bytes.

## Remaining manual checks

- Databricks schemas and volumes have not yet been created or verified.
- Databricks Files API upload has not yet been verified.
- Databricks outbound access has not yet been verified.
- KCM realtime terms and attribution requirements still need to be reviewed.

## Credential hygiene

The pre-existing `.env` contained live-looking credentials and must not be committed. Those credentials
were exposed during setup and should be rotated before development continues.
