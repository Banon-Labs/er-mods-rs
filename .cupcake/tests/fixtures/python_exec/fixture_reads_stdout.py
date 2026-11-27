"""Fixture for bash_no_python_file_write: a `.py` script outside `scripts/` that only reads, which
the guard admits once the `python_script_writes` signal has read it. Run in
`scripts/test-cupcake-policies.py` beside a `sed -n` and a `$(...)`, the two shapes refused on
2026-11-19/20 although the script they ran wrote nothing."""
import sys

print(len(sys.argv))
