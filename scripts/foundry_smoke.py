"""Make one Microsoft Foundry model call through Agent Framework and print the result.

Usage: az login, fill FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL in .env, then
    python scripts/foundry_smoke.py
"""

from __future__ import annotations

import asyncio
import sys

from second_act.orchestration.foundry import FoundryNotConfigured, smoke_call


def main() -> int:
    try:
        result = asyncio.run(smoke_call())
    except FoundryNotConfigured as exc:
        print(f"not configured: {exc}", file=sys.stderr)
        return 2
    print(f"response: {result.text!r}")
    print(
        f"latency: {result.latency_s}s  tokens in/out: {result.input_tokens}/{result.output_tokens}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
