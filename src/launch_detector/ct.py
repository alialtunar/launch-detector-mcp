"""Certificate transparency via crt.sh (public, no key): every hostname a domain ever got a
TLS certificate for, with the date it first appeared."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .http import SourceError, fetch

CRT = "https://crt.sh/"
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@dataclass
class Name:
    name: str            # "staging.identity.example.com" (wildcards keep "*.")
    first_seen: str      # YYYY-MM-DD of the earliest certificate
    last_seen: str       # YYYY-MM-DD of the newest certificate
    certs: int = 0
    issuers: set[str] = field(default_factory=set)

    @property
    def wildcard(self) -> bool:
        return self.name.startswith("*.")


def normalize_domain(raw: str) -> str:
    d = re.sub(r"^[a-z]+://", "", raw.strip().lower())
    d = d.split("/", 1)[0].split(":", 1)[0].removeprefix("*.").removeprefix("www.")
    if not _DOMAIN.match(d):
        raise SourceError(f"'{raw}' is not a domain. Pass a company domain such as 'anthropic.com'.")
    return d


def _issuer(dn: str) -> str:
    m = re.search(r"O=([^,]+)", dn or "")
    return (m.group(1) if m else dn or "").strip('"')[:40]


async def names(domain: str) -> dict[str, Name]:
    """All hostnames under `domain` seen in CT logs (expired certificates included: history matters)."""
    d = normalize_domain(domain)
    _, text = await fetch(CRT, {"q": f"%.{d}", "output": "json"}, source="crt.sh", disk=True, disk_ttl=24 * 3600)
    if not text.strip():
        return {}
    try:
        rows = json.loads(text)
    except ValueError as e:
        raise SourceError("crt.sh returned something that is not JSON (it does this when overloaded). Try again in a minute.") from e
    out: dict[str, Name] = {}
    for r in rows:
        nb = (r.get("not_before") or "")[:10]
        if not nb:
            continue
        for n in (r.get("name_value") or "").lower().split("\n"):
            n = n.strip()
            if not n or not (n == d or n.endswith("." + d)) or "@" in n:
                continue
            cur = out.get(n)
            if cur is None:
                out[n] = cur = Name(n, nb, nb)
            cur.first_seen = min(cur.first_seen, nb)
            cur.last_seen = max(cur.last_seen, nb)
            cur.certs += 1
            cur.issuers.add(_issuer(r.get("issuer_name", "")))
    return out
