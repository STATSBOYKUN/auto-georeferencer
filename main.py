#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wilkerstat CLI Entry Point.
"""

import sys
from pathlib import Path

# Pastikan root workspace terdaftar di sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.runner import main

if __name__ == "__main__":
    main()
