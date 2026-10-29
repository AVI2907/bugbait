"""Make BugBait's own modules importable no matter how pytest is invoked."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
