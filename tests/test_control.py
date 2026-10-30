"""Exercise the client against a real local HTTP service, without external access."""

from __future__ import annotations

import json
import threading
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from neobot_cli.control import ControlClient
from neobot_cli.errors import CliError
from neobot_cli.project import Workspace


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "my-neobot"
    data = root / "data"
    plugins = data / "plugins"
    plugins.mkdir(parents=True)
    return Workspace(root.resolve(), data.resolve(), plugins.resolve(), root / ".env")


@pytest.fixture
def service(workspace):
    stack = ExitStack()

    @contextmanager
    def serve(respond=None):
        state = SimpleNamespace(requests=[], respond=respond)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *arguments):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                state.requests.append(
                    {"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body}
                )
                if state.respond is None:
                    response = {
                        "schema_version": 1,
                        "ok": True,
                        "data": {
                            "instance_id": "local-test-instance",
                            "data_dir": str(workspace.data_dir),
                            "plugin_dir": str(workspace.plugin_dir),
                            "pid": 123,
                            "capabilities": ["plugin.dev"],
                            "plugins": [],
                        },
                    }
                    status, headers, payload = 200, {}, json.dumps(response).encode()
                else:
                    status, headers, payload = state.respond(self)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        state.url = f"http://127.0.0.1:{server.server_port}"
        try:
            yield state
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def create(respond=None):
        return stack.enter_context(serve(respond))

    yield create
    stack.close()


def discovery(workspace, url, **overrides):
    path = workspace.data_dir / ".neobot/control.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": 1,
        "url": url,
        "token": "local-test-credential",
        "pid": 123,
        "instance_id": "local-test-instance",
        "data_dir": str(workspace.data_dir),
    }
    document.update(overrides)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def json_response(document, status=200):
    return lambda request: (status, {"Content-Type": "application/json"}, json.dumps(document).encode())


def test_status_and_development_request_use_current_instance_and_bearer(workspace, service):
    host = service()
    discovery(workspace, host.url)
    client = ControlClient(workspace)
    status = client.status()
    assert status["running"] is True
    assert status["data_dir"] == str(workspace.data_dir)
    client.request("POST", "plugins/dev", {"name": "hello", "enabled": True})
    assert [(call["method"], call["path"]) for call in host.requests] == [
        ("GET", "/v1/status"),
        ("POST", "/v1/plugins/dev"),
    ]
    assert all(call["headers"]["Authorization"] == "Bearer local-test-credential" for call in host.requests)
    assert json.loads(host.requests[1]["body"]) == {"name": "hello", "enabled": True}
    assert "local-test-credential" not in json.dumps(status)


@pytest.mark.parametrize("field", ["instance_id", "data_dir", "plugin_dir"])
def test_status_rejects_another_instance(workspace, service, field):
    data = {
        "instance_id": "local-test-instance",
        "data_dir": str(workspace.data_dir),
        "plugin_dir": str(workspace.plugin_dir),
    }
    data[field] = "other-instance" if field == "instance_id" else str(workspace.root.parent / "other")
    host = service(json_response({"schema_version": 1, "ok": True, "data": data}))
    discovery(workspace, host.url)
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).status()
    assert failure.value.code == "INSTANCE_MISMATCH"


def test_invalid_discovery_data_directory_is_rejected_before_connecting(workspace, service):
    host = service()
    discovery(workspace, host.url, data_dir=str(workspace.root.parent / "other/data"))
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).status()
    assert failure.value.code == "CONTROL_INVALID"
    assert host.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:1234",
        "http://localhost:1234",
        "http://192.0.2.1:1234",
        "http://[::1]:1234",
        "http://user@127.0.0.1:1234",
        "http://127.0.0.1:1234/other",
        "http://127.0.0.1:1234?other=1",
        "http://127.0.0.1:1234#fragment",
        "http://127.0.0.1:not-a-port",
    ],
)
def test_only_explicit_loopback_discovery_url_is_accepted(workspace, url):
    discovery(workspace, url)
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).status()
    assert failure.value.code == "CONTROL_INVALID"


def test_ambient_proxies_never_receive_control_credentials(workspace, service, monkeypatch):
    proxy = service(json_response({"schema_version": 1, "ok": False, "error": {"code": "PROXY_USED"}}))
    host = service()
    discovery(workspace, host.url)
    for variable in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(variable, proxy.url)
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    assert ControlClient(workspace).status()["running"] is True
    assert len(host.requests) == 1
    assert proxy.requests == []


@pytest.mark.parametrize("code", [301, 302, 307, 308])
def test_redirect_is_not_followed_or_sent_credentials(workspace, service, code):
    foreign = service()
    host = service(lambda request: (code, {"Location": foreign.url + "/v1/status"}, b"redirect refused"))
    discovery(workspace, host.url)
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).status()
    assert failure.value.code == "PROTOCOL_INCOMPATIBLE"
    assert len(host.requests) == 1
    assert foreign.requests == []


def test_redirect_with_valid_success_body_is_still_rejected(workspace, service):
    foreign = service()
    data = {
        "instance_id": "local-test-instance",
        "data_dir": str(workspace.data_dir),
        "plugin_dir": str(workspace.plugin_dir),
    }
    body = json.dumps({"schema_version": 1, "ok": True, "data": data}).encode()
    host = service(lambda request: (302, {"Location": foreign.url + "/v1/status"}, body))
    discovery(workspace, host.url)
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).status()
    assert failure.value.code == "PROTOCOL_INCOMPATIBLE"
    assert foreign.requests == []


@pytest.mark.parametrize("status,code", [(401, "AUTH_REQUIRED"), (409, "PLUGIN_BUSY"), (500, "HOST_FAILURE")])
def test_host_error_envelope_becomes_cli_error(workspace, service, status, code):
    host = service(
        json_response({"schema_version": 1, "ok": False, "error": {"code": code, "message": "操作未完成"}}, status)
    )
    discovery(workspace, host.url)
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).request("POST", "plugins/dev", {"name": "hello", "enabled": True})
    assert failure.value.code == code
    assert str(failure.value) == "操作未完成"


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        {"schema_version": 2, "ok": True, "data": {}},
        {"schema_version": 1, "ok": "yes", "data": {}},
        {"schema_version": 1, "ok": True, "data": []},
        {"schema_version": 1, "ok": False, "error": None},
        {"schema_version": 1, "ok": False, "error": []},
    ],
)
def test_malformed_envelope_is_reported_without_raw_exception(workspace, service, document):
    host = service(json_response(document))
    discovery(workspace, host.url)
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).status()
    assert failure.value.code == "PROTOCOL_INCOMPATIBLE"


@pytest.mark.parametrize("field", ["data_dir", "plugin_dir"])
def test_status_path_types_are_validated(workspace, service, field):
    data = {
        "instance_id": "local-test-instance",
        "data_dir": str(workspace.data_dir),
        "plugin_dir": str(workspace.plugin_dir),
    }
    data[field] = None
    host = service(json_response({"schema_version": 1, "ok": True, "data": data}))
    discovery(workspace, host.url)
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).status()
    assert failure.value.code == "PROTOCOL_INCOMPATIBLE"


@pytest.mark.parametrize("document", [None, [], "invalid"])
def test_malformed_discovery_is_reported_without_raw_exception(workspace, document):
    path = discovery(workspace, "http://127.0.0.1:1234")
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).status()
    assert failure.value.code == "CONTROL_INVALID"


def test_missing_discovery_reports_host_offline(workspace):
    with pytest.raises(CliError) as failure:
        ControlClient(workspace).status()
    assert failure.value.code == "HOST_OFFLINE"


def test_non_json_and_oversized_responses_are_protocol_errors(workspace, service):
    for payload in (b"not json", b" " * (1024 * 1024 + 1)):
        host = service(lambda request, payload=payload: (200, {}, payload))
        discovery(workspace, host.url)
        with pytest.raises(CliError) as failure:
            ControlClient(workspace).status()
        assert failure.value.code == "PROTOCOL_INCOMPATIBLE"
