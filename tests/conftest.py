"""Mock crt.sh JSON (dates relative to today so tests never go stale)."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from launch_detector import http


def ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat() + "T10:00:00"


def cert(i: int, names: list[str], days_ago: int, issuer: str = "C=US, O=Let's Encrypt, CN=R11") -> dict:
    return {"issuer_ca_id": 1, "issuer_name": issuer, "common_name": names[0], "name_value": "\n".join(names),
            "id": i, "not_before": ago(days_ago), "not_after": ago(days_ago - 90), "serial_number": f"{i:x}", "result_count": 2}


ROWS = [
    cert(1, ["acme.com", "www.acme.com"], 900),
    cert(2, ["docs.acme.com"], 800),
    cert(3, ["docs.acme.com"], 400),                                   # renewal: first seen stays 800 days ago
    cert(4, ["staging.atlas.acme.com"], 40),                           # new product with a staging env
    cert(5, ["atlas.acme.com", "api.atlas.acme.com"], 10),
    cert(6, ["node-12.us-east-1.edge.acme.com"], 5),                   # infrastructure noise
    cert(7, ["labs-preview.acme.com"], 20, issuer="C=US, O=Google Trust Services, CN=WR1"),
    cert(8, ["mail.acme.com", "user@acme.com"], 3),
    cert(9, ["a.acme.com"], 2),                                         # too short to be a product
    cert(10, ["evil.notacme.com"], 1),                                  # not under the domain
]


class CrtSh:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.busy_left = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if self.busy_left > 0:
            self.busy_left -= 1
            return httpx.Response(502, text="<title>502 Bad Gateway</title>")
        q = request.url.params.get("q", "")
        if q == "%.acme.com":
            return httpx.Response(200, json=ROWS)
        if q == "%.empty.com":
            return httpx.Response(200, text="[]")
        if q == "%.broken.com":
            return httpx.Response(200, text="<html>overloaded</html>")
        return httpx.Response(200, text="[]")


@pytest.fixture(autouse=True)
def crtsh(tmp_path):
    mock = CrtSh()
    http.set_transport(httpx.MockTransport(mock), retry_delays=(0.0, 0.0, 0.0))
    http.set_disk_cache(tmp_path / "cache")
    yield mock
    http.set_transport(None)
    http.set_disk_cache(None)
