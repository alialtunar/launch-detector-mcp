"""launch-detector MCP server.

Companies get a TLS certificate before a new subdomain goes live, and every certificate is
published in public certificate transparency logs. Tools read those logs (crt.sh) and surface
new product-like subdomains; the model judges what they mean. Transport: stdio. No API keys.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, ct, signals
from .http import SourceError

mcp = MCPServer(
    "launch_detector_mcp",
    version=__version__,
    instructions=(
        "Spot unannounced products from new subdomains in certificate transparency logs (crt.sh). "
        "Typical flow: launch_signals(domain) -> new_subdomains for raw evidence -> subdomain_timeline "
        "for activity over time; watch_domains for several competitors. These are hints, not proof: a "
        "hostname can be internal tooling, a test or a renamed service. Say so, cite the hostnames and "
        "first-seen dates, and never present a guess as fact. crt.sh is slow (20-60 s) and sometimes busy."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
Domain = Annotated[str, Field(min_length=4, max_length=253, description="Company domain, e.g. 'anthropic.com' or 'stripe.com'.")]
Days = Annotated[int, Field(ge=1, le=730, description="Look-back window in days.")]
Format = Annotated[Literal["markdown", "json"], Field(description="'markdown' (default) or 'json'.")]


def _error(e: Exception) -> str:
    if isinstance(e, SourceError):
        return f"Error: {e}"
    return f"Error: unexpected {type(e).__name__}: {e}"


def _since(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


# ---------- tools ----------

@mcp.tool(name="launch_signals", annotations=READ_ONLY)
async def launch_signals(
    domain: Domain,
    days: Days = 180,
    top: Annotated[int, Field(ge=1, le=50)] = 15,
    response_format: Format = "markdown",
) -> str:
    """Product-like names that first appeared under a domain in the window, ranked by how much they
    look like an upcoming launch (new, several hostnames, staging/beta/preview variants, product words)."""
    try:
        d = ct.normalize_domain(domain)
        names = await ct.names(d)
    except Exception as e:
        return _error(e)
    if not names:
        return f"No certificates found for {d}. Check the domain."
    sigs = signals.signals(names, d, days)[:top]
    if response_format == "json":
        return json.dumps({"domain": d, "window_days": days, "hostnames_total": len(names), "signals": [
            {"stem": s.stem, "score": s.score, "first_seen": s.first_seen, "envs": sorted(s.envs),
             "reasons": s.reasons, "hostnames": s.hostnames[:10]} for s in sigs]}, indent=2)
    lines = [f"## Launch signals · {d} · last {days} days",
             f"{len(names)} hostnames in certificate logs; {len(sigs)} new product-like names shown.", ""]
    if not sigs:
        lines.append("_No new product-like names in this window. Try a longer window or new_subdomains for raw names._")
        return "\n".join(lines)
    lines += ["| Name | Score | First seen | Why | Example hostnames |", "|---|---|---|---|---|"]
    for s in sigs:
        why = "; ".join(r for r in s.reasons if not r.startswith("first seen")) or "new"
        lines.append(f"| **{s.stem}** | {s.score} | {s.first_seen} | {why} | {', '.join(f'`{h}`' for h in s.hostnames[:3])} |")
    lines.append("\n_Hints, not proof: names can be internal tools or tests. Check hostnames before concluding._")
    return "\n".join(lines)


@mcp.tool(name="new_subdomains", annotations=READ_ONLY)
async def new_subdomains(
    domain: Domain,
    days: Days = 30,
    include_infrastructure: Annotated[bool, Field(description="Also list hostnames that look like plumbing (numbered nodes, regions, mail...).")] = False,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    response_format: Format = "markdown",
) -> str:
    """Raw evidence: every hostname whose first certificate appeared in the window, newest first,
    with its product-like name and environment words."""
    try:
        d = ct.normalize_domain(domain)
        names = await ct.names(d)
    except Exception as e:
        return _error(e)
    since = _since(days)
    rows = []
    for n in sorted(names.values(), key=lambda n: n.first_seen, reverse=True):
        if n.first_seen < since:
            continue
        stem, envs = signals.stem(n.name, d)
        if not stem and not include_infrastructure:
            continue
        rows.append({"hostname": n.name, "first_seen": n.first_seen, "stem": stem, "envs": sorted(envs),
                     "certs": n.certs, "issuers": sorted(n.issuers)})
    if response_format == "json":
        return json.dumps({"domain": d, "since": since, "count": len(rows), "hostnames": rows[:limit]}, indent=2)
    lines = [f"## New hostnames · {d} · since {since}", f"{len(rows)} found" +
             ("" if include_infrastructure else " (infrastructure-only names hidden)") + ".", ""]
    if rows:
        lines += ["| First seen | Hostname | Name | Env |", "|---|---|---|---|"]
        lines += [f"| {r['first_seen']} | `{r['hostname']}` | {r['stem'] or '—'} | {', '.join(r['envs']) or ''} |" for r in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"_…{len(rows) - limit} more._")
    return "\n".join(lines)


@mcp.tool(name="subdomain_timeline", annotations=READ_ONLY)
async def subdomain_timeline(
    domain: Domain,
    months: Annotated[int, Field(ge=2, le=36)] = 12,
    response_format: Format = "markdown",
) -> str:
    """New hostnames per month and the product-like names that first appeared each month.
    Bursts often line up with launches, rebrands or new regions."""
    try:
        d = ct.normalize_domain(domain)
        names = await ct.names(d)
    except Exception as e:
        return _error(e)
    counts = signals.monthly_counts(names, months)
    first_stem: dict[str, str] = {}
    for n in names.values():
        s, _ = signals.stem(n.name, d)
        if s and (s not in first_stem or n.first_seen < first_stem[s]):
            first_stem[s] = n.first_seen
    stems_by_month: dict[str, list[str]] = {}
    for s, f in first_stem.items():
        stems_by_month.setdefault(f[:7], []).append(s)
    rows = [{"month": m, "new_hostnames": c, "new_names": sorted(stems_by_month.get(m, []))} for m, c in counts]
    if response_format == "json":
        return json.dumps({"domain": d, "months": rows}, indent=2)
    peak = max((r["new_hostnames"] for r in rows), default=0) or 1
    lines = [f"## Certificate activity · {d} · last {months} months", "",
             "| Month | New hostnames | | New product-like names |", "|---|---|---|---|"]
    lines += [f"| {r['month']} | {r['new_hostnames']} | {'█' * round(8 * r['new_hostnames'] / peak)} | "
              f"{', '.join(r['new_names'][:8]) + (' …' if len(r['new_names']) > 8 else '') or '—'} |" for r in rows]
    return "\n".join(lines)


@mcp.tool(name="watch_domains", annotations=READ_ONLY)
async def watch_domains(
    domains: Annotated[list[str], Field(min_length=2, max_length=8, description="Competitor domains, e.g. ['openai.com', 'anthropic.com'].")],
    days: Days = 60,
    response_format: Format = "markdown",
) -> str:
    """A radar over 2-8 competitors: new hostnames in the window and each one's top launch signals."""

    async def one(raw: str) -> dict[str, Any]:
        try:
            d = ct.normalize_domain(raw)
            names = await ct.names(d)
        except Exception as e:
            return {"domain": raw, "error": _error(e)}
        since = _since(days)
        new = [n for n in names.values() if n.first_seen >= since]
        sigs = signals.signals(names, d, days)[:3]
        return {"domain": d, "new_hostnames": len(new), "total_hostnames": len(names),
                "top_signals": [{"stem": s.stem, "first_seen": s.first_seen, "score": s.score} for s in sigs]}

    results = await asyncio.gather(*(one(d) for d in domains))
    if response_format == "json":
        return json.dumps({"window_days": days, "domains": results}, indent=2)
    lines = [f"## Launch radar · last {days} days", "", "| Domain | New hostnames | Top signals |", "|---|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['domain']} | — | {r['error']} |")
            continue
        top = ", ".join(f"**{s['stem']}** ({s['first_seen']})" for s in r["top_signals"]) or "—"
        lines.append(f"| {r['domain']} | {r['new_hostnames']} | {top} |")
    lines.append("\n_Use launch_signals on one domain for the evidence._")
    return "\n".join(lines)


@mcp.tool(name="hostname_history", annotations=READ_ONLY)
async def hostname_history(
    hostname: Annotated[str, Field(min_length=4, max_length=253, description="A full hostname, e.g. 'console.anthropic.com'.")],
    response_format: Format = "markdown",
) -> str:
    """When one hostname first and last got a certificate, how many, and from which issuers."""
    host = hostname.strip().lower()
    parts = host.split(".")
    if len(parts) < 3:
        return "Error: pass a full hostname such as 'docs.example.com' (use launch_signals for a whole domain)."
    domain = ".".join(parts[-2:]) if len(parts[-1]) > 2 or len(parts) < 4 else ".".join(parts[-3:])
    try:
        names = await ct.names(domain)
    except Exception as e:
        return _error(e)
    n = names.get(host) or names.get("*." + host)
    if not n:
        near = sorted(k for k in names if k.endswith("." + host))[:10]
        return f"No certificate found for {host}." + (f" Names under it: {', '.join(near)}" if near else "")
    data = {"hostname": n.name, "first_seen": n.first_seen, "last_seen": n.last_seen, "certificates": n.certs,
            "issuers": sorted(n.issuers), "name": signals.stem(n.name, domain)[0]}
    if response_format == "json":
        return json.dumps(data, indent=2)
    return (f"## {n.name}\n- First certificate: {n.first_seen}\n- Latest certificate: {n.last_seen}\n"
            f"- Certificates: {n.certs}\n- Issuers: {', '.join(sorted(n.issuers))}")


# ---------- prompts ----------

@mcp.prompt(name="whats_coming", description="What might this company launch next? Evidence from certificate logs.")
def whats_coming(domain: str = "anthropic.com") -> str:
    return (
        f"1. Call launch_signals(domain='{domain}', days=180) and subdomain_timeline(domain='{domain}', months=12).\n"
        "2. For the top 5 names, call new_subdomains or hostname_history if you need more evidence.\n"
        "3. For each, say what it could be (product, internal tool, region, rebrand), how confident you are, and why. "
        "Cite hostnames and first-seen dates. Clearly label speculation; never state a guess as fact."
    )


@mcp.prompt(name="competitor_radar", description="Weekly radar across several competitors.")
def competitor_radar(domains: str = "openai.com, anthropic.com, mistral.ai") -> str:
    ds = [d.strip() for d in domains.split(",") if d.strip()]
    return (
        f"1. Call watch_domains(domains={ds}, days=30).\n"
        "2. For any domain with interesting signals, call launch_signals on it.\n"
        "3. Write a short radar: one line per company with the most interesting new names, then the 2-3 signals "
        "worth watching next week. Cite hostnames and dates; mark speculation as such."
    )


def main() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()
