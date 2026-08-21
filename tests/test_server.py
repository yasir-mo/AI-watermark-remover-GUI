"""HTTP service: routing, auth, guards, and payload handling."""

from __future__ import annotations

import base64
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from scrubai import __version__
from scrubai.server import Handler, Server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, port: int, token: str | None = None):
        self.base = f"http://127.0.0.1:{port}"
        self.token = token

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get(self, path: str):
        req = urllib.request.Request(self.base + path, headers=self._headers())
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())

    def post(self, path: str, payload: dict):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())

    def raw(self, path: str, body: bytes, ctype: str = "application/json"):
        req = urllib.request.Request(
            self.base + path,
            data=body,
            headers={**self._headers(), "Content-Type": ctype},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    Handler.api_key = None
    Handler.ui_enabled = True
    httpd = Server(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield Client(port)
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def authed_server():
    port = _free_port()
    Handler.api_key = "correct-horse-battery-staple"
    httpd = Server(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port
    httpd.shutdown()
    httpd.server_close()
    Handler.api_key = None


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class TestHealthAndDiscovery:
    def test_health(self, server):
        status, body = server.get("/health")
        assert status == 200
        assert body["ok"] is True
        assert body["version"] == __version__
        assert body["uptime_seconds"] >= 0

    def test_capabilities_shape(self, server):
        _, caps = server.get("/capabilities")
        for tool in ("exiftool", "c2patool", "qpdf"):
            assert tool in caps["tools"]
            assert isinstance(caps["tools"][tool]["installed"], bool)
            assert caps["tools"][tool]["install"], "missing install instructions"
        assert caps["pdf_tier"] in ("stdlib", "exiftool", "exiftool+qpdf")
        assert caps["limits"]["max_input_bytes"] > 0

    def test_openapi_is_valid_shape(self, server):
        _, spec = server.get("/openapi.json")
        assert spec["openapi"] == "3.0.3"
        for path in ("/health", "/inspect", "/clean", "/rewrite", "/capabilities"):
            assert path in spec["paths"]
        assert "bearerAuth" in spec["components"]["securitySchemes"]

    def test_unknown_route_404s(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.get("/nope")
        assert exc.value.code == 404


class TestSecurityHeaders:
    def test_present_on_every_response(self, server):
        req = urllib.request.Request(server.base + "/health")
        with urllib.request.urlopen(req) as r:
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            assert "default-src 'self'" in r.headers["Content-Security-Policy"]
            assert r.headers["Referrer-Policy"] == "no-referrer"


class TestAuth:
    def test_health_is_public(self, authed_server):
        status, body = Client(authed_server).get("/health")
        assert status == 200 and body["ok"]

    def test_protected_without_token_401s(self, authed_server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            Client(authed_server).get("/capabilities")
        assert exc.value.code == 401

    def test_wrong_token_401s(self, authed_server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            Client(authed_server, "wrong").get("/capabilities")
        assert exc.value.code == 401

    def test_correct_token_passes(self, authed_server):
        c = Client(authed_server, "correct-horse-battery-staple")
        assert c.get("/capabilities")[0] == 200

    def test_post_also_protected(self, authed_server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            Client(authed_server).post("/inspect", {"text": "hi"})
        assert exc.value.code == 401


class TestInspect:
    def test_text_payload(self, server):
        _, body = server.post("/inspect", {"text": "a​b", "name": "x.txt"})
        assert body["format"] == "text"
        assert body["layer_a"]["total_removable"] == 1
        assert body["summary"]["watermark_suspected"] is True

    def test_clean_text_is_not_suspected(self, server):
        _, body = server.post("/inspect", {"text": "ordinary text", "name": "x.txt"})
        assert body["summary"]["watermark_suspected"] is False

    def test_file_payload(self, server, png_with_metadata):
        _, body = server.post(
            "/inspect",
            {
                "file": b64(png_with_metadata),
                "name": "art.png",
            },
        )
        assert body["format"] == "png"
        assert body["summary"]["c2pa_present"] is True
        assert body["metadata"]["count"] > 0

    def test_does_not_mutate(self, server, png_with_metadata):
        """Inspect must be read-only: cleaning after it yields the same result."""
        a = server.post("/clean", {"file": b64(png_with_metadata), "name": "a.png"})[1]
        server.post("/inspect", {"file": b64(png_with_metadata), "name": "a.png"})
        b = server.post("/clean", {"file": b64(png_with_metadata), "name": "a.png"})[1]
        assert a["cleaned"] == b["cleaned"]

    def test_binary_format_has_no_layer_a(self, server, png_with_metadata):
        _, body = server.post(
            "/inspect",
            {
                "file": b64(png_with_metadata),
                "name": "art.png",
            },
        )
        assert body["is_text_format"] is False
        assert "layer_a" not in body


class TestClean:
    def test_returns_cleaned_bytes(self, server):
        _, body = server.post("/clean", {"text": "a​b", "name": "x.txt"})
        assert base64.b64decode(body["cleaned"]).decode() == "ab"
        assert body["report"]["layer_a"]["total_removed"] == 1

    def test_diff_is_opt_in(self, server):
        _, off = server.post("/clean", {"text": "a​b", "name": "x.txt"})
        assert "diff" not in off["report"]
        _, on = server.post(
            "/clean",
            {
                "text": "a​b",
                "name": "x.txt",
                "options": {"diff": True},
            },
        )
        assert any(o["op"] == "delete" for o in on["report"]["diff"])

    def test_png_round_trip(self, server, png_with_metadata):
        _, body = server.post(
            "/clean",
            {
                "file": b64(png_with_metadata),
                "name": "art.png",
            },
        )
        out = base64.b64decode(body["cleaned"])
        assert out.startswith(b"\x89PNG\r\n\x1a\n")
        assert b"Midjourney" not in out
        assert body["report"]["bytes_removed"] > 0

    def test_output_is_actually_clean(self, server, png_with_metadata):
        _, cleaned = server.post(
            "/clean",
            {
                "file": b64(png_with_metadata),
                "name": "a.png",
            },
        )
        _, recheck = server.post(
            "/inspect",
            {
                "file": cleaned["cleaned"],
                "name": "a.png",
            },
        )
        assert recheck["summary"]["c2pa_present"] is False
        assert recheck["metadata"]["count"] == 0

    def test_options_are_honored(self, server):
        payload = {"text": "Amerіca", "name": "x.txt", "options": {"aggressive": True}}
        _, body = server.post("/clean", payload)
        assert base64.b64decode(body["cleaned"]).decode() == "America"


class TestRewriteEndpoint:
    def test_print_prompt_default(self, server):
        _, body = server.post("/rewrite", {"text": "The cat sat."})
        assert body["backend"] == "print-prompt"
        assert "The cat sat." in body["prompt"]

    def test_unreachable_backend_502s(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.post(
                "/rewrite",
                {
                    "text": "hi",
                    "backend": "ollama",
                    "base_url": "http://127.0.0.1:1",
                    "timeout": 2,
                },
            )
        assert exc.value.code == 502

    def test_remote_url_refused_without_opt_in(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.post(
                "/rewrite",
                {
                    "text": "hi",
                    "backend": "ollama",
                    "base_url": "http://example.com",
                },
            )
        assert exc.value.code == 502
        assert "loopback" in exc.value.read().decode()

    def test_missing_text_400s(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.post("/rewrite", {"backend": "print-prompt"})
        assert exc.value.code == 400

    def test_api_key_is_never_echoed(self, server):
        _, body = server.post(
            "/rewrite",
            {
                "text": "hi",
                "backend": "print-prompt",
                "api_key": "sk-secret-value",
            },
        )
        assert "sk-secret-value" not in json.dumps(body)


class TestInputGuards:
    def test_invalid_base64_400s(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.post("/inspect", {"file": "not!valid!base64", "name": "x.png"})
        assert exc.value.code == 400

    def test_malformed_json_400s(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.raw("/inspect", b"{not json")
        assert exc.value.code == 400

    def test_non_object_body_400s(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.raw("/inspect", b'["a list"]')
        assert exc.value.code == 400

    def test_empty_body_400s(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.raw("/inspect", b"")
        assert exc.value.code == 400

    def test_neither_file_nor_text_400s(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.post("/inspect", {"name": "x.txt"})
        assert exc.value.code == 400

    def test_oversized_declared_length_413s(self, server):
        """Reject on Content-Length before reading the body."""
        from scrubai.server import MAX_BODY_BYTES

        conn = socket.create_connection(("127.0.0.1", int(server.base.rsplit(":", 1)[1])))
        conn.sendall(
            b"POST /inspect HTTP/1.1\r\nHost: x\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {MAX_BODY_BYTES + 1}\r\n\r\n".encode()
        )
        status = conn.recv(64).split(b" ")[1]
        conn.close()
        assert status == b"413"

    def test_traversal_in_name_is_neutralized(self, server):
        _, body = server.post(
            "/inspect",
            {
                "text": "hi",
                "name": "../../etc/passwd",
            },
        )
        assert "/" not in body["name"] and "\\" not in body["name"]
        assert body["name"] == "passwd"

    def test_absurd_name_400s(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            server.post("/inspect", {"text": "hi", "name": "x" * 600})
        assert exc.value.code == 400


class TestStaticUi:
    def test_index_served(self, server):
        with urllib.request.urlopen(server.base + "/ui/") as r:
            body = r.read()
        assert r.status == 200
        assert b"<title>ScrubAI" in body

    def test_assets_served_with_types(self, server):
        for path, ctype in (("/ui/style.css", "text/css"), ("/ui/app.js", "javascript")):
            with urllib.request.urlopen(server.base + path) as r:
                assert ctype in r.headers["Content-Type"]

    @pytest.mark.parametrize(
        "path",
        [
            "/ui/../scrubai/server.py",
            "/ui/%2e%2e/scrubai/server.py",
            "/ui/..%2fscrubai%2fserver.py",
            "/ui/....//scrubai/server.py",
        ],
    )
    def test_traversal_refused(self, server, path):
        """Send the raw path so the client cannot normalize it for us."""
        port = int(server.base.rsplit(":", 1)[1])
        conn = socket.create_connection(("127.0.0.1", port))
        conn.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while chunk := conn.recv(4096):
            data += chunk
        conn.close()
        assert b" 200 " not in data.split(b"\r\n")[0]
        assert b"BaseHTTPRequestHandler" not in data


class TestSummarySemantics:
    """findings_present and watermark_suspected are deliberately different."""

    def test_metadata_only_is_findings_but_not_watermark(self, server, docx_with_properties):
        """An author name is worth stripping without being a watermark."""
        _, body = server.post("/inspect", {"file": b64(docx_with_properties), "name": "r.docx"})
        s = body["summary"]
        assert s["metadata_findings"] > 0
        assert s["findings_present"] is True
        assert s["watermark_suspected"] is False

    def test_c2pa_is_both(self, server, png_with_metadata):
        _, body = server.post("/inspect", {"file": b64(png_with_metadata), "name": "a.png"})
        s = body["summary"]
        assert s["findings_present"] is True
        assert s["watermark_suspected"] is True

    def test_hidden_characters_are_both(self, server):
        _, body = server.post("/inspect", {"text": "a\u200bb", "name": "x.txt"})
        s = body["summary"]
        assert s["findings_present"] is True
        assert s["watermark_suspected"] is True

    def test_clean_file_is_neither(self, server):
        _, body = server.post("/inspect", {"text": "plain text", "name": "x.txt"})
        s = body["summary"]
        assert s["findings_present"] is False
        assert s["watermark_suspected"] is False
