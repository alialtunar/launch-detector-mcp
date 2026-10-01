"""Live smoke test against the real crt.sh.

Run:  uv run python scripts/smoke_live.py
crt.sh is slow and often busy: expect a few minutes. Every check prints PASS/FAIL.
"""

import asyncio
import logging
import sys

from launch_detector.server import hostname_history, launch_signals, new_subdomains, subdomain_timeline, watch_domains

CHECKS = [
    ("Launch signals (anthropic.com, 180 days)", launch_signals, dict(domain="anthropic.com", days=180, top=10), "| Name |"),
    ("New subdomains (anthropic.com, 90 days)", new_subdomains, dict(domain="anthropic.com", days=90, limit=10), "| First seen |"),
    ("Timeline (anthropic.com, 12 months)", subdomain_timeline, dict(domain="anthropic.com", months=12), "| Month |"),
    ("Hostname history (console.anthropic.com)", hostname_history, dict(hostname="console.anthropic.com"), "First certificate"),
    ("Radar (linear.app, notion.so)", watch_domains, dict(domains=["linear.app", "notion.so"], days=90), "| Domain |"),
]


async def main() -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    failed = 0
    for name, fn, kwargs, must in CHECKS:
        out = await fn(**kwargs)
        ok = not out.startswith("Error") and must in out
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        print("      " + out.replace("\n", "\n      ")[:900] + ("\n      …" if len(out) > 900 else ""))
        print()
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
