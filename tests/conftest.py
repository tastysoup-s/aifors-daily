from pathlib import Path
import os

import pytest

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def rss_feed_xml(fixtures_dir: Path) -> str:
    return (fixtures_dir / "rss_feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def arxiv_response_xml(fixtures_dir: Path) -> str:
    return (fixtures_dir / "arxiv_response.xml").read_text(encoding="utf-8")


@pytest.fixture
def github_search_json(fixtures_dir: Path) -> str:
    return (fixtures_dir / "github_search.json").read_text(encoding="utf-8")


@pytest.fixture
def hn_search_json(fixtures_dir: Path) -> str:
    return (fixtures_dir / "hn_search.json").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch):
    """Tests must mock transports/providers, never use live APIs."""
    import socket

    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def guard(host):
        # Windows asyncio creates its internal socketpair on loopback.
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("External network is forbidden in unit tests")

    def local_connect(sock, address):
        guard(address[0])
        return connect(sock, address)

    def local_connect_ex(sock, address):
        guard(address[0])
        return connect_ex(sock, address)

    def local_lookup(host, *args, **kwargs):
        guard(host)
        return getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", local_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", local_lookup)
