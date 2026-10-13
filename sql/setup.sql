-- Phase 2 schemas. Keep this script idempotent for manual workspace setup.
CREATE SCHEMA IF NOT EXISTS workspace.${schema_prefix}_bronze;
CREATE SCHEMA IF NOT EXISTS workspace.${schema_prefix}_silver;
CREATE SCHEMA IF NOT EXISTS workspace.${schema_prefix}_gold;
CREATE SCHEMA IF NOT EXISTS workspace.${schema_prefix}_ml;
CREATE SCHEMA IF NOT EXISTS workspace.${schema_prefix}_ops;
