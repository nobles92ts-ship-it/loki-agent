#!/usr/bin/env python3
"""Loki diagnostics — thin shim kept for the setup wizards.

    venv\\Scripts\\python.exe tools\\diag.py

The checks themselves live in ``loki.core.diagnostics`` so that
``python -m loki doctor`` and this entry point can never drift apart. Prefer
the command; this stays because setup.ps1 / setup.sh call it.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    from loki.core import diagnostics
    sys.exit(diagnostics.doctor())
