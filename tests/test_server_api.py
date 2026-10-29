"""HTTP contract tests for the stdlib phone API."""
import gzip
import http.client
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from server_helpers import expected_counts, load_source_index

from server.api import ApiHandler
from server.config import DATA_DIR
from server.devlog import dev_logging_enabled, is_local_host, normalize_host
from server.import_legacy import import_data


@pytest.fixture(scope="module")
def source_index():
    return load_source_index(DATA_DIR)


@pytest.fixture(scope="module")
def api_server(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("morphe-api-db") / "morphe.db"
    import_data(str(db_path), DATA_DIR, reset=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    server.daemon_threads = True
    server.db_path = str(db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[0], server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(host, port, path, headers=None, method="GET"):
    conn = http.client.HTTPConnection(host, port, timeout=20)
    conn.request(method, path, headers=headers or {})
    response = conn.getresponse()
    body = response.read()
    result = (response.status, dict(response.getheaders()), body)
    conn.close()
    return result


def test_root_landing(api_server):
    host, port = api_server
    status, headers, body = request(host, port, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    payload = json.loads(body)
    assert payload["ok"] is True
    assert "/healthz" in payload["endpoints"]["health"]
    assert headers["Access-Control-Allow-Headers"].find("ngrok-skip-browser-warning") != -1


def test_health_and_meta(api_server, source_index):
    host, port = api_server
    expected = expected_counts(DATA_DIR, source_index)
    status, _, body = request(host, port, "/healthz")
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["data_version"]

    status, _, body = request(host, port, "/v1/meta")
    assert status == 200
    meta = json.loads(body)
    assert meta["counts"]["bundles"] == expected["bundles"]
    assert meta["latest_count"] == expected["latest"]


def test_changes_and_changelog(api_server):
    host, port = api_server
    status, _, body = request(host, port, "/v1/changes")
    assert status == 200
    changes = json.loads(body)
    assert "affected_bundles" in changes

    status, _, body = request(host, port, "/v1/changelog?limit=50")
    assert status == 200
    changelog = json.loads(body)
    assert changelog
    if changes.get("affected_bundles"):
        with open(os.path.join(DATA_DIR, "core.json"), encoding="utf-8") as fh:
            core = json.load(fh)
        assert any(day.get("date") == core.get("date") for day in changelog)



def test_index_etag_and_gzip(api_server, source_index):
    host, port = api_server
    expected = expected_counts(DATA_DIR, source_index)
    status, headers, body = request(host, port, "/v1/index", {"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert headers["ETag"]
    index = json.loads(gzip.decompress(body))
    assert len(index) == expected["bundles"]

    status, headers, body = request(
        host, port, "/v1/index",
        {"Accept-Encoding": "gzip", "If-None-Match": headers["ETag"]},
    )
    assert status == 304
    assert body == b""


def test_bundle_detail_and_releases(api_server):
    host, port = api_server
    status, _, body = request(host, port, "/v1/bundles/santodan/dev")
    assert status == 200
    detail = json.loads(body)
    assert detail["bundle"] == "santodan"
    assert detail["channel"] == "dev"
    assert detail["apps"]
    assert detail["apps"][0]["patches"]

    status, _, body = request(host, port, "/v1/bundles/santodan/stable/releases?limit=5")
    assert status == 200
    releases = json.loads(body)["releases"]
    assert releases
    assert any(item["tag"] for item in releases)


def test_app_and_search(api_server):
    host, port = api_server
    status, _, body = request(host, port, "/v1/apps/com.nuvio.tv")
    assert status == 200
    app = json.loads(body)
    assert app["package"] == "com.nuvio.tv"
    assert app["bundles"]

    status, _, body = request(host, port, "/v1/search?q=nuvio")
    assert status == 200
    results = json.loads(body)
    assert results["apps"]


def test_404(api_server):
    host, port = api_server
    status, _, body = request(host, port, "/v1/bundles/does-not-exist/stable")
    assert status == 404
    assert json.loads(body)["error"] == "bundle not found"

def test_dev_logging_host_detection_and_overrides(monkeypatch):
    assert normalize_host("localhost:8090") == "localhost"
    assert normalize_host("[::1]:8090") == "::1"
    assert normalize_host("127.0.0.1:8090") == "127.0.0.1"
    assert normalize_host("certify-slashed-duo.ngrok-free.dev") == "certify-slashed-duo.ngrok-free.dev"
    assert is_local_host("127.0.0.1:1234") is True
    assert is_local_host("certify-slashed-duo.ngrok-free.dev") is False

    monkeypatch.delenv("MORPHE_DEBUG", raising=False)
    assert dev_logging_enabled("localhost:8090") is True
    assert dev_logging_enabled("example.test") is False
    monkeypatch.setenv("MORPHE_DEBUG", "1")
    assert dev_logging_enabled("example.test") is True
    monkeypatch.setenv("MORPHE_DEBUG", "0")
    assert dev_logging_enabled("localhost:8090") is False


def test_api_logs_local_requests_only(api_server, capsys):
    host, port = api_server
    status, _, _ = request(host, port, "/healthz", {"Host": "localhost:8090"})
    assert status == 200
    status, _, _ = request(host, port, "/healthz", {"Host": "example.test"})
    assert status == 200

    output = capsys.readouterr().out
    assert output.count("[api:dev]") == 1
    assert "GET /healthz 200" in output
    assert "host=localhost:8090" in output


def test_api_dev_logging_env_override(api_server, capsys, monkeypatch):
    host, port = api_server
    monkeypatch.setenv("MORPHE_DEBUG", "1")
    status, _, _ = request(host, port, "/healthz", {"Host": "example.test"})
    assert status == 200
    assert "[api:dev]" in capsys.readouterr().out

    monkeypatch.setenv("MORPHE_DEBUG", "0")
    status, _, _ = request(host, port, "/healthz", {"Host": "localhost:8090"})
    assert status == 200
    assert "[api:dev]" not in capsys.readouterr().out
