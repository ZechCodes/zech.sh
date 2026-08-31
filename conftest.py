"""Pytest bootstrap for the main app.

Puts the repository root on ``sys.path`` so tests can import the app's
top-level packages (``controllers``, ``models``, ``tests``) without each test
module doing its own ``sys.path`` surgery.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
