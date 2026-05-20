"""BigQuery SQL dry-run validator.

Uses BigQuery's dry_run=True mode which validates SQL syntax and schema
without executing the query — no data is scanned and no cost is incurred.
Requires BigQuery read permissions on the target project.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# Match BigQuery parameter references like @trans_dt, @cs_db
# Excludes @@... (system variables) and quoted identifiers.
_PARAM_RE = re.compile(r"(?<!@)@(\w+)")

# BQ surfaces parameter-type mismatches with messages like:
#   Query parameter '@trans_dt' has type DATE which cannot be coerced to expected type STRING at [10:15]
#   Query parameter 'trans_dt' has type DATE which cannot be coerced to expected type STRING
# We parse out the param name + expected type so we can retry dry-run with the
# correct type — letting the same query work whether the underlying column is
# STRING or DATE without forcing users to maintain per-query type configs.
_PARAM_TYPE_ERR_RE = re.compile(
    r"[Qq]uery parameter ['\"]?@?(\w+)['\"]? "
    r"has type \w+ which cannot be coerced to expected type (\w+)"
)

# Types BQ accepts in ScalarQueryParameter. Anything outside this set we leave
# as STRING (the safe default) rather than passing through and erroring.
_VALID_BQ_PARAM_TYPES = {
    "STRING", "BYTES", "INT64", "INTEGER", "FLOAT64", "FLOAT", "NUMERIC",
    "BIGNUMERIC", "BOOL", "BOOLEAN", "TIMESTAMP", "DATE", "TIME", "DATETIME",
    "GEOGRAPHY", "JSON",
}


@dataclass
class ValidationResult:
    success: bool
    error: str | None
    bytes_processed: int


class BQValidator:
    def __init__(self, cfg: dict[str, Any]):
        self.project = cfg.get("gcp", {}).get("project")
        credentials_file = cfg.get("gcp", {}).get("credentials_file")
        # Optional config: declare types for specific @params used in queries.
        # Example in config.yaml:
        #   query_parameter_types:
        #     min_declared_value: NUMERIC
        #     trans_dt: DATE
        # Params not listed default to STRING NULL for dry-run.
        self.param_types: dict[str, str] = cfg.get("query_parameter_types", {})
        self.available = False
        self._unavailable_reason = ""

        try:
            from google.cloud import bigquery
            if credentials_file:
                from google.oauth2 import service_account
                credentials = service_account.Credentials.from_service_account_file(
                    credentials_file,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                self.client = bigquery.Client(project=self.project, credentials=credentials)
            else:
                self.client = bigquery.Client(project=self.project)
            self.bigquery = bigquery
            self.available = True
        except ImportError:
            self._unavailable_reason = "google-cloud-bigquery not installed"
        except Exception as e:
            self._unavailable_reason = str(e)

    def ensure_datasets(self, dataset_map: dict[str, str], region: str = "US") -> None:
        """Create BQ datasets from dataset_map values if they don't already exist."""
        if not self.available:
            return
        seen: set[str] = set()
        for dataset_id in dataset_map.values():
            if dataset_id in seen:
                continue
            seen.add(dataset_id)
            ref = f"{self.project}.{dataset_id}"
            dataset = self.bigquery.Dataset(ref)
            dataset.location = region
            self.client.create_dataset(dataset, exists_ok=True)

    def execute_ddl(self, sql: str) -> ValidationResult:
        """Execute a DDL statement (CREATE/DROP TABLE) for real — creates the table."""
        if not self.available:
            return ValidationResult(
                success=False,
                error=f"BQ client unavailable: {self._unavailable_reason}",
                bytes_processed=0,
            )
        try:
            self.client.query(sql).result()
            return ValidationResult(success=True, error=None, bytes_processed=0)
        except Exception as e:
            msg = str(e) or repr(e) or e.__class__.__name__
            return ValidationResult(success=False, error=msg, bytes_processed=0)

    def validate(self, sql: str) -> ValidationResult:
        if not self.available:
            return ValidationResult(
                success=False,
                error=f"BQ client unavailable: {self._unavailable_reason}",
                bytes_processed=0,
            )

        # BigQuery dry-run requires every @param referenced in the SQL to
        # have a matching ScalarQueryParameter supplied. Auto-supply NULL
        # params so dry-run can proceed; production callers pass the real
        # values at execution time.
        #
        # Start with the user-configured types (or STRING by default), then
        # retry up to 3 times — each round we parse BQ's "expected type" hint
        # from the error and adjust the offending param. This lets the same
        # config work across HQLs that use @trans_dt as STRING in one query
        # and DATE in another.
        param_names = sorted(set(_PARAM_RE.findall(sql)))
        per_call_types: dict[str, str] = {
            name: self.param_types.get(name, "STRING") for name in param_names
        }

        last_error = ""
        for attempt in range(4):  # initial + 3 retries
            try:
                query_parameters = [
                    self.bigquery.ScalarQueryParameter(name, per_call_types[name], None)
                    for name in param_names
                ]
                job_config = self.bigquery.QueryJobConfig(
                    dry_run=True,
                    use_query_cache=False,
                    query_parameters=query_parameters,
                )
                job = self.client.query(sql, job_config=job_config)
                return ValidationResult(
                    success=True,
                    error=None,
                    bytes_processed=job.total_bytes_processed or 0,
                )
            except Exception as e:
                msg = str(e) or repr(e) or e.__class__.__name__
                last_error = msg
                # If BQ told us the expected type for a specific param, retry
                # with it. Otherwise give up — the error is something else.
                m = _PARAM_TYPE_ERR_RE.search(msg)
                if not m:
                    break
                bad_name, expected_type = m.group(1), m.group(2).upper()
                if bad_name not in per_call_types:
                    break
                if expected_type not in _VALID_BQ_PARAM_TYPES:
                    break
                if per_call_types[bad_name] == expected_type:
                    # Already tried this type — avoid infinite loop
                    break
                per_call_types[bad_name] = expected_type

        return ValidationResult(success=False, error=last_error, bytes_processed=0)
