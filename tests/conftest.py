import sys
from pathlib import Path

# The data scripts import each other as plain modules (they're run as scripts),
# so make data/ importable from the tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data"))
