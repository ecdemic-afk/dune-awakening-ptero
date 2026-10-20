#!/usr/bin/env python3
"""Entry point: `python3 runtime/dunectl.py run` (the egg's startup command) or `doctor`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dunectl.supervisor import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv))
