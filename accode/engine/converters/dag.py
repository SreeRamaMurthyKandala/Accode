"""Event engine XML scheduler definitions → Apache Airflow 2.x Python DAGs."""
import xml.etree.ElementTree as ET
from pathlib import Path
from .base import BaseConverter, ConversionResult

SYSTEM_PROMPT = """\
You are a data engineer migrating a custom event-engine XML scheduler to Apache Airflow 2.x.

Convert the XML to a valid, runnable Airflow 2.x Python DAG file.

## DAG metadata mapping
- `<EventId>` → dag_id (convert SCREAMING_SNAKE_CASE to snake_case lowercase)
- `<Description>` → doc_md string
- `<Schedule><Expression>` (cron) → schedule_interval string
- `<Schedule><TimeZone>` → pendulum timezone

## Task structure
Each `<Event>` produces one main task:
- Use BashOperator for shell commands
- `<Action><Command>` → bash_command
- `<Action><TimeoutMinutes>` → execution_timeout=timedelta(minutes=N)
- `<Action><RunAsUser>` → add as a comment (Composer manages service accounts)

## Dependencies
- `<Dependency>UPSTREAM_EVENT_ID</Dependency>` →
  ```python
  wait_for_upstream = ExternalTaskSensor(
      task_id="wait_for_upstream",
      external_dag_id="upstream_event_id_lowercase",
      poke_interval=60,
      timeout=3600,
      mode="reschedule",
  )
  ```
  Then set `wait_for_upstream >> main_task`

## On failure → default_args
- `<RetryCount>` → retries=N in default_args
- `<RetryDelayMinutes>` → retry_delay=timedelta(minutes=N) in default_args
- `<Email>` → email=[...] in default_args, email_on_failure=True

## On success / downstream trigger
- `<OnSuccess><PublishEvent>DOWNSTREAM_ID</PublishEvent>` →
  ```python
  # TODO: trigger downstream DAG 'downstream_id_lowercase'
  # Option A: TriggerDagRunOperator (if you want immediate trigger)
  # Option B: ExternalTaskSensor in the downstream DAG (recommended)
  ```

## Standard DAG template to use
```python
from datetime import datetime, timedelta
import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.sensors.external_task import ExternalTaskSensor

local_tz = pendulum.timezone("America/New_York")

default_args = {
    "owner": "data-engineering",
    "retries": 1,
    "retry_delay": timedelta(minutes=15),
    "email_on_failure": True,
    "email": ["data-alerts@company.com"],
    "email_on_retry": False,
}

with DAG(
    dag_id="dag_id_here",
    description="...",
    schedule_interval="cron here",
    start_date=datetime(2024, 1, 1, tzinfo=local_tz),
    catchup=False,
    default_args=default_args,
    tags=["migrated"],  # TODO: replace with meaningful tags derived from the EventId/project
) as dag:
    dag.doc_md = "..."

    main_task = BashOperator(
        task_id="run_job",
        bash_command=(
            "/path/to/script.sh "
            # TODO: update path to GCP-compatible script location
        ),
        execution_timeout=timedelta(minutes=90),
    )
```

## Important notes
- The original `<Command>` paths reference Hive scripts — add `# TODO: Update to GCS/GCP path`
- Always add `catchup=False`
- Add relevant tags from the EventId
- Use `mode="reschedule"` on ExternalTaskSensor (avoids worker slot hogging)

## Output format
- Return ONLY the Python DAG file — no markdown fences, no explanation
- Valid Python that passes `python -m py_compile`
- Add `# Converted from: <filename>` as the first comment line
- Add `# TODO:` for anything requiring manual update
"""


class DAGConverter(BaseConverter):
    def convert(self, source: Path) -> ConversionResult:
        content = source.read_text(errors="replace")
        notes: list[str] = []

        # Validate XML is parseable
        try:
            root = ET.fromstring(content)
            event_id = root.findtext(".//EventId") or source.stem
            notes.append(f"Event: {event_id}")
        except ET.ParseError as e:
            notes.append(f"XML parse warning: {e}")

        converted = self.llm.convert(SYSTEM_PROMPT, f"Filename: {source.name}\n\n{content}")

        needs_review = True  # DAGs always need review — command paths must be updated
        notes.append("Update bash_command paths to point to GCP-compatible scripts")
        if "ExternalTaskSensor" in converted:
            notes.append("Review ExternalTaskSensor external_dag_id values match actual DAG IDs")
        return ConversionResult(content=converted, notes=notes, needs_review=needs_review)
