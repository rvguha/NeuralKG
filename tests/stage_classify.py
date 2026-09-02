#!/usr/bin/env python3
"""Compatibility entry point; query understanding replaced the monolithic classifier."""
import asyncio
from stage_query_understanding import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
