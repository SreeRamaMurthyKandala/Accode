"""Generic coding tools — the baseline surface every coding agent needs."""
from accode.tools.generic.bash import TOOL as _bash
from accode.tools.generic.edit import TOOL as _edit
from accode.tools.generic.list_files import TOOL as _list_files
from accode.tools.generic.read import TOOL as _read
from accode.tools.generic.search import TOOL as _search
from accode.tools.generic.write import TOOL as _write

GENERIC_TOOLS = [_read, _write, _edit, _list_files, _search, _bash]
