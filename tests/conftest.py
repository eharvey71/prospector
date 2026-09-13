"""Path setup: tests import shared/ modules and worker adapter helpers
directly, WITHOUT going through worker/adapters/__init__.py (which pulls in
Playwright). Keeps the suite runnable with just pytest + pydantic."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for rel in ("shared", "worker/adapters"):
    p = str(ROOT / rel)
    if p not in sys.path:
        sys.path.insert(0, p)
