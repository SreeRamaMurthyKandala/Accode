"""The Hive -> GCP migration toolset — the orchestrator pipeline, decomposed.

Each tool wraps exactly one stage of the original `Orchestrator.run()`:

    Orchestrator stage              ->  Accode tool
    --------------------------------    -----------------------
    scan()                          ->  migration_discovery
    _process_file() loop            ->  migration_convert
    _run_syntax_checks()            ->  migration_syntax_check
    ensure_datasets()+execute_ddl() ->  migration_bq_setup
    validate_only()                 ->  migration_bq_validate
    _run_tests()                    ->  migration_run_tests
    FixAgent.fix_file()             ->  migration_fix
    Reporter + _patch_report()      ->  migration_report

Called in the canonical order they reproduce the full pipeline; called
individually they support stage-only workflows ("just discover", etc.).
"""
from accode.tools.migration.bq_setup import TOOL as _bq_setup
from accode.tools.migration.bq_validate import TOOL as _bq_validate
from accode.tools.migration.convert import TOOL as _convert
from accode.tools.migration.discovery import TOOL as _discovery
from accode.tools.migration.fix import TOOL as _fix
from accode.tools.migration.report import TOOL as _report
from accode.tools.migration.run_tests import TOOL as _run_tests
from accode.tools.migration.syntax_check import TOOL as _syntax_check

# Listed in canonical pipeline order.
MIGRATION_TOOLS = [
    _discovery,
    _convert,
    _syntax_check,
    _bq_setup,
    _bq_validate,
    _run_tests,
    _fix,
    _report,
]
