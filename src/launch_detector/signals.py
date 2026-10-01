"""From hostnames to product signals.

'stt-flux-a100.titanium.api.example.com' -> stem 'titanium' (labels that only describe
infrastructure are skipped), 'staging.identity.example.com' -> stem 'identity' with env 'staging'.
A stem that first appears recently, gets staging/beta variants and several hostnames is the
kind of thing that precedes a launch. Deterministic heuristics; the model judges the story.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .ct import Name

ENV_WORDS = {"staging", "stage", "stg", "dev", "development", "test", "testing", "qa", "uat", "sandbox",
             "preview", "beta", "alpha", "canary", "preprod", "pre-prod", "demo", "internal", "int", "nonprod", "early"}
PRELAUNCH_ENVS = {"staging", "stage", "stg", "preview", "beta", "alpha", "canary", "preprod", "pre-prod", "early", "sandbox"}
# Labels that describe plumbing, not products: skipped when picking the stem.
GENERIC = {"www", "api", "apis", "app", "apps", "web", "m", "c", "s", "edge", "cdn", "static", "assets", "img", "images",
           "media", "files", "mail", "email", "smtp", "imap", "pop", "mx", "autodiscover", "autoconfig", "ns", "ns1", "ns2",
           "dns", "vpn", "remote", "gateway", "gw", "lb", "proxy", "origin", "cache", "secure", "ssl", "login", "auth", "sso",
           "admin", "status", "metrics", "monitoring", "grafana", "logs", "internal", "corp", "intranet", "go", "link", "links",
           "click", "track", "tracking", "em", "e", "info", "support", "help", "docs", "blog", "careers", "jobs", "shop",
           "store", "pages", "public", "private", "svc", "service", "services", "prod", "production", "live", "cloud"}
_REGION = re.compile(r"^(?:us|eu|ap|sa|ca|me|af|uk|de|fr|jp|au|in|br|cn|kr|sg)(?:-?(?:east|west|north|south|central|ne|se|nw|sw))?-?\d*[a-z]?$"
                     r"|^(?:eastus|westus|westeurope|northeurope|spaincentral|uksouth|centralus)\d*$")
_NOISE = re.compile(r"^(?:[a-z]{0,3}\d+[a-z\d]*|[0-9a-f]{8,}|.*-\d+)$")  # s1, a100, chs2, hashes, unified-23
KEYWORDS = {"ai", "agent", "agents", "labs", "lab", "beta", "preview", "new", "pay", "payments", "wallet", "store",
            "marketplace", "studio", "voice", "video", "chat", "assistant", "copilot", "cloud", "platform", "devices",
            "hardware", "enterprise", "partners", "partner", "academy", "certification", "research", "health", "edu"}


@dataclass
class Signal:
    stem: str
    first_seen: str
    hostnames: list[str] = field(default_factory=list)
    envs: set[str] = field(default_factory=set)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


# Cloud regions inside a label: "admin-api-us-east1", "api-europe-west1", "eu-central-1"
_REGION_IN = re.compile(r"(?:^|-)(?:us|eu|europe|asia|ap|sa|ca|me|af|uk|australia|northamerica|southamerica)"
                        r"-(?:east|west|north|south|central|northeast|northwest|southeast|southwest)-?\d*(?=-|$)")


def _clean(label: str) -> tuple[str, set[str]]:
    """'atlantis-sandbox' -> ('atlantis', {'sandbox'}); 'staging' -> ('', {'staging'});
    'admin-api-us-east1' -> ('', set()): every part is plumbing."""
    without_region = _REGION_IN.sub("", label)
    if _REGION.match(label) or not without_region or _NOISE.match(without_region):  # "us-east-1", "unified-23"
        return "", set()
    parts = [p for p in without_region.split("-") if p]
    envs = {p for p in parts if p in ENV_WORDS}
    core = [p for p in parts if p not in ENV_WORDS and not _NOISE.match(p) and not _REGION.match(p)
            and re.sub(r"\d+$", "", p) not in GENERIC | {"admin"}]  # "static1" is "static"
    return "-".join(core), envs


def stem(hostname: str, domain: str) -> tuple[str, set[str]]:
    """The product-ish label of a hostname and any environment words found in it."""
    rest = hostname.removesuffix(domain).rstrip(".").removeprefix("*.").lstrip("*.")
    if not rest:
        return "", set()
    envs: set[str] = set()
    candidates: list[str] = []
    for label in reversed(rest.split(".")):  # closest to the domain first
        core, e = _clean(label)
        envs |= e
        # 1-2 letter labels ("a", "he", "li") are locales or short links, not products
        if len(core) > 2 and core not in GENERIC and not _REGION.match(core) and not _NOISE.match(core):
            candidates.append(core)
    return (candidates[0] if candidates else ""), envs


def signals(names: dict[str, Name], domain: str, days: int, today: date | None = None) -> list[Signal]:
    """Stems whose first hostname appeared within `days`, ranked by launch-likeness."""
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    by_stem: dict[str, Signal] = {}
    for n in names.values():
        s, envs = stem(n.name, domain)
        if not s:
            continue
        sig = by_stem.setdefault(s, Signal(stem=s, first_seen=n.first_seen))
        sig.first_seen = min(sig.first_seen, n.first_seen)
        sig.hostnames.append(n.name)
        sig.envs |= envs
    out = []
    for sig in by_stem.values():
        if sig.first_seen < since:
            continue  # the stem existed before the window: not new
        age = (today - date.fromisoformat(sig.first_seen)).days
        sig.score = 1.0 + max(0.0, 1 - age / max(days, 1))  # newer = higher
        sig.reasons.append(f"first seen {sig.first_seen}")
        if len(sig.hostnames) > 1:
            sig.score += min(2.0, 0.4 * (len(sig.hostnames) - 1))
            sig.reasons.append(f"{len(sig.hostnames)} hostnames")
        pre = sig.envs & PRELAUNCH_ENVS
        if pre:
            sig.score += 1.0
            sig.reasons.append("pre-launch environment: " + ", ".join(sorted(pre)))
        kw = {w for w in re.split(r"[-.]", sig.stem) if w in KEYWORDS}
        if kw:
            sig.score += 0.5
            sig.reasons.append("product word: " + ", ".join(sorted(kw)))
        sig.hostnames.sort()
        sig.score = round(sig.score, 2)
        out.append(sig)
    return sorted(out, key=lambda s: (-s.score, s.first_seen))


def monthly_counts(names: dict[str, Name], months: int, today: date | None = None) -> list[tuple[str, int]]:
    """How many hostnames were first seen in each of the last `months` months (oldest first)."""
    today = today or date.today()
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    counts = {k: 0 for k in keys}
    for n in names.values():
        k = n.first_seen[:7]
        if k in counts:
            counts[k] += 1
    return [(k, counts[k]) for k in reversed(keys)]
