"""Build the single-file release asset: dist/agentperf.pyz (stdlib only).

Usage: python3 scripts/build_pyz.py [dest]
The result runs with any Python 3.8+:  python3 agentperf.pyz run --quick
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentperf.remote import build_pyz  # noqa: E402

if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join("dist", "agentperf.pyz")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    print(build_pyz(dest))
