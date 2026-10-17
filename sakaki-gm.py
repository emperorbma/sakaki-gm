#!/usr/bin/env python3
"""Backward-compatible launcher; prefer ``python -m sakaki_gm`` or ``sakaki-gm``."""
from sakaki_gm.__main__ import main

if __name__ == "__main__":
    main()
