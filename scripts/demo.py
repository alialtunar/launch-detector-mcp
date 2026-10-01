"""Terminal demo: new product-like subdomains of a company.

Run:  uv run --with rich python scripts/demo.py linear.app   (rich is optional)
Calls the same tool function the MCP server exposes, so the output is what Claude sees.
"""

import asyncio
import logging
import sys

from launch_detector.server import launch_signals


async def main(domain: str) -> None:
    print(f"→ launch_signals(domain='{domain}', days=365)\n")
    out = await launch_signals(domain=domain, days=365, top=6)
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        Console(width=130).print(Markdown(out))
    except ImportError:
        print(out)


if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "linear.app"))
