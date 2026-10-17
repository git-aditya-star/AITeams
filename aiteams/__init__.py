import sys

# The tool shim runs without -B; keep the installed venv free of .pyc files.
sys.dont_write_bytecode = True
