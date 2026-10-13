# Setup Notes

Date: 2026-10-13

## Local checks

- Python 3.14.5, `uv` 0.11.30, Node 24.12.0, GitHub CLI 2.92.0, and Databricks CLI 1.20.0 are installed.
- GitHub CLI is authenticated as `enpuyou`.
- Databricks profile `enpu` is valid for the configured workspace. The `DEFAULT` profile is stale.
- KCM vehicle positions, trip updates, and alerts feeds are reachable and saved under `tests/fixtures/`.
- Feed sizes at setup time: vehicle positions 50,817 bytes; trip updates 1,142,067 bytes; alerts 45,614 bytes.

## Remaining manual checks

- Databricks schemas and volumes verified on 2026-10-13 with the `enpu` CLI profile:
	`workspace.transit_landing.raw` and `workspace.transit_ops.state` both exist as managed volumes.
- Databricks Files API upload verified on 2026-10-13: a marker file uploaded to
	`/Volumes/workspace/transit_landing/raw/_smoke/hello.txt` returned HTTP 204 and was readable
	through the Databricks CLI.
- Databricks outbound access has not yet been verified.
- KCM realtime terms and attribution requirements still need to be reviewed.

The setup gate for external collection is therefore clear: the inbound Files API path works.
Use the external collector architecture and continue to treat Databricks outbound access as
optional rather than a prerequisite.

## Credential hygiene

The pre-existing `.env` contained live-looking credentials and must not be committed. Those credentials
were exposed during setup. Decision recorded on 2026-10-13: keep them in local use for now because
the user considers the risk low. Continue to avoid committing `.env`, printing token values, or
putting credentials in documentation; revisit this decision if the repository becomes public or the
credentials gain broader permissions.
