import json
from datetime import date, timedelta

import pytest
from mcp.client import Client

from launch_detector import __version__, ct, signals
from launch_detector.http import SourceError
from launch_detector.server import (
    hostname_history, launch_signals, mcp, new_subdomains, subdomain_timeline, watch_domains,
)


# ---- crt.sh parsing ----

async def test_names_track_first_and_last_seen():
    names = await ct.names("acme.com")
    docs = names["docs.acme.com"]
    assert docs.first_seen == (date.today() - timedelta(days=800)).isoformat()
    assert docs.last_seen == (date.today() - timedelta(days=400)).isoformat() and docs.certs == 2
    assert "evil.notacme.com" not in names and "user@acme.com" not in names
    assert names["labs-preview.acme.com"].issuers == {"Google Trust Services"}


@pytest.mark.parametrize("raw,expected", [
    ("https://www.Acme.com/pricing", "acme.com"), ("*.acme.com", "acme.com"), ("acme.co.uk", "acme.co.uk"),
])
def test_normalize_domain(raw, expected):
    assert ct.normalize_domain(raw) == expected


def test_normalize_domain_rejects_garbage():
    with pytest.raises(SourceError, match="not a domain"):
        ct.normalize_domain("acme")


async def test_retries_while_crtsh_is_busy(crtsh):
    crtsh.busy_left = 3
    assert "atlas.acme.com" in await ct.names("acme.com")


async def test_gives_up_with_clear_message(crtsh):
    crtsh.busy_left = 99
    with pytest.raises(SourceError, match="not answering"):
        await ct.names("acme.com")


async def test_overloaded_html_is_explained():
    with pytest.raises(SourceError, match="overloaded"):
        await ct.names("broken.com")


# ---- stems ----

@pytest.mark.parametrize("host,stem,envs", [
    ("stt-flux-a100.titanium.api.acme.com", "titanium", set()),
    ("staging.identity.acme.com", "identity", {"staging"}),
    ("atlantis-sandbox.c.acme.com", "atlantis", {"sandbox"}),
    ("economic-research.acme.com", "economic-research", set()),
    ("node-12.us-east-1.edge.acme.com", "", set()),
    ("spaincentral.privatelink.api.acme.com", "privatelink", set()),
    ("*.he.acme.com", "", set()),
    ("www.acme.com", "", set()),
    ("labs-preview.acme.com", "labs", {"preview"}),
    ("admin-api-us-east1.acme.com", "", set()),
    ("*.admin-api-europe-west1.acme.com", "", set()),
    ("static1.acme.com", "", set()),
    ("orbit.acme.com", "orbit", set()),
    ("checkout-eu-central-1.acme.com", "checkout", set()),
])
def test_stem(host, stem, envs):
    assert signals.stem(host, "acme.com") == (stem, envs)


async def test_signals_rank_new_products_with_staging_first():
    names = await ct.names("acme.com")
    sigs = signals.signals(names, "acme.com", days=90)
    assert [s.stem for s in sigs] == ["atlas", "labs"]
    atlas = sigs[0]
    assert len(atlas.hostnames) == 3 and "staging" in atlas.envs
    assert any("pre-launch" in r for r in atlas.reasons)
    assert "docs" not in [s.stem for s in signals.signals(names, "acme.com", days=900)]  # existed before the window


def test_monthly_counts_shape():
    rows = signals.monthly_counts({}, 3, today=date(2026, 3, 15))
    assert rows == [("2026-01", 0), ("2026-02", 0), ("2026-03", 0)]


# ---- tools ----

async def test_launch_signals_tool():
    data = json.loads(await launch_signals(domain="acme.com", days=90, response_format="json"))
    assert data["signals"][0]["stem"] == "atlas" and data["signals"][0]["envs"] == ["staging"]
    out = await launch_signals(domain="acme.com", days=90)
    assert "| **atlas** |" in out and "Hints, not proof" in out


async def test_launch_signals_empty_domain():
    assert (await launch_signals(domain="empty.com")).startswith("No certificates found")


async def test_new_subdomains_hides_infrastructure_by_default():
    out = json.loads(await new_subdomains(domain="acme.com", days=30, response_format="json"))
    hosts = [r["hostname"] for r in out["hostnames"]]
    assert "atlas.acme.com" in hosts and "node-12.us-east-1.edge.acme.com" not in hosts and "a.acme.com" not in hosts
    all_ = json.loads(await new_subdomains(domain="acme.com", days=30, include_infrastructure=True, response_format="json"))
    assert "node-12.us-east-1.edge.acme.com" in [r["hostname"] for r in all_["hostnames"]]


async def test_timeline_lists_new_names_per_month():
    data = json.loads(await subdomain_timeline(domain="acme.com", months=3, response_format="json"))
    assert len(data["months"]) == 3
    assert any("atlas" in m["new_names"] for m in data["months"])


async def test_watch_domains_reports_errors_per_row():
    out = await watch_domains(domains=["acme.com", "not a domain"], days=60)
    assert "**atlas**" in out and "not a domain" in out and "Error:" in out


async def test_hostname_history():
    out = await hostname_history(hostname="docs.acme.com")
    assert "Certificates: 2" in out
    assert (await hostname_history(hostname="nope.acme.com")).startswith("No certificate found")


# ---- MCP protocol ----

async def test_protocol_end_to_end():
    async with Client(mcp) as client:
        assert client.server_info.version == __version__
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"launch_signals", "new_subdomains", "subdomain_timeline", "watch_domains", "hostname_history"}
        assert all(t.annotations.read_only_hint for t in tools.values())
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert prompts == {"whats_coming", "competitor_radar"}
        res = await client.call_tool("launch_signals", {"domain": "acme.com", "days": 90})
        assert not res.is_error and "atlas" in res.content[0].text
        bad = await client.call_tool("watch_domains", {"domains": ["acme.com"]})
        assert bad.is_error  # needs at least 2
