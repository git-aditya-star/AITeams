import sys

# The CLI runs from a uv tool venv without -B; keep the editable checkout free of .pyc files.
sys.dont_write_bytecode = True
