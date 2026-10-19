"""Independent data-quality audit: checks written separately from the code that builds the tables.

Each check is a named scalar SQL query with a rule, so a failure can be re-run by hand in the SQL
editor. Nothing here is wired into a job; run it on demand (see `runners.py`).
"""
