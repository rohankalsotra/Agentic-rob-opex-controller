"""
conftest.py -- setup that pytest runs automatically before the tests.

Our code lives in the agents/ folder. This tells Python where to find it,
so a test can simply say:  import variance_engine
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
