#!/usr/bin/env python3
"""Production query-understanding evaluation; HTML reports and top-three recall.

The old eleven-label scorer is retired. This entry and template_stage_run call
harness.query_understanding_async unchanged. See --help for report options.
"""
import asyncio
from template_stage_run import main

if __name__ == "__main__":
    asyncio.run(main())
