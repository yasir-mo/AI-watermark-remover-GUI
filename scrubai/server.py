"""Stdlib-only HTTP service.

No Flask, no FastAPI, no dependencies. Binds to 127.0.0.1 by default; binding
anywhere else requires an explicit flag and an API key.

Endpoints
    GET  /health          liveness
    GET  /capabilities    which optional tools and backends are present
    GET  /openapi.json    generated spec
    GET  /ui/             single-page frontend (served from ./ui)
    POST /inspect         scan a file or text, report findings
    POST /clean           strip and return the cleaned bytes
    POST /rewrite         Layer B paraphrase
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import logging
import mimetypes
import os
import posixpath
import socket
import sys
import time
import urllib.parse
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .capabilities import capabilities
from .layer_a import clean as layer_a_clean
from .layer_a import diff_ops
from .layer_a import scan as layer_a_scan
from .metadata import TEXT_FORMATS, MalformedFile, clean_file, detect_format, inspect_file
from .openapi import spec
from .rewrite import BackendConfig, RewriteError, rewrite
from .safety import MAX_INPUT_BYTES

LOG = logging.getLogger("scrubai.server")

API_KEY_ENV = "WATERMARKS_SERVER_API_KEY"
UI_DIR = Path(__file__).resolve().parent.parent / "ui"
START_TIME = time.monotonic()

# JSON envelope overhead: base64 inflates by 4/3, plus field names.
MAX_BODY_BYTES = int(MAX_INPUT_BYTES * 4 / 3) + 65536


class HttpError(Exception):
    def __init__(self, status: HTTPStatus, message: str, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


# --------------------------------------------------------------------------
# Request handling
# --------------------------------------------------------------------------


def _decode_input(payload: dict[str, Any]) -> tuple[bytes, str]:
    """Return ``(data, name)`` from either the ``file`` or ``text`` field."""
    name = payload.get("name") or "input.txt"
    if not isinstance(name, str) or len(name) > 512:
        raise HttpError(HTTPStatus.BAD_REQUEST, "invalid name")
    # Never let a client-supplied name escape into a path.
    name = Path(name).name or "input.txt"

    if "file" in payload and payload["file"] is not None:
        blob = payload["file"]
        if not isinstance(blob, str):
            raise HttpError(HTTPStatus.BAD_REQUEST, "file must be a base64 string")
        try:
            data = base64.b64decode(blob, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HttpError(
                HTTPStatus.BAD_REQUEST, "file is not valid base64", str(exc)
            ) from exc
    elif "text" in payload and payload["text"] is not None:
        if not isinstance(payload["text"], str):
            raise HttpError(HTTPStatus.BAD_REQUEST, "text must be a string")
        data = payload["text"].encode("utf-8")
    else:
        raise HttpError(HTTPStatus.BAD_REQUEST, "provide either 'file' or 'text'")

    if len(data) > MAX_INPUT_BYTES:
        raise HttpError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            f"input is {len(data)} bytes, limit is {MAX_INPUT_BYTES}",
        )
    return data, name


def _options(payload: dict[str, Any]) -> dict[str, Any]:
    opts = payload.get("options") or {}
    if not isinstance(opts, dict):
        raise HttpError(HTTPStatus.BAD_REQUEST, "options must be an object")
    return {
        "aggressive": bool(opts.get("aggressive", False)),
        "strip_emoji_glue": bool(opts.get("strip_emoji_glue", False)),
        "normalize": bool(opts.get("normalize", False)),
        "layer_a": bool(opts.get("layer_a", True)),
        "metadata": bool(opts.get("metadata", True)),
        "use_tools": bool(opts.get("use_tools", True)),
        "diff": bool(opts.get("diff", False)),
    }


def handle_inspect(payload: dict[str, Any]) -> dict[str, Any]:
    data, name = _decode_input(payload)
    opts = _options(payload)
    fmt = detect_format(name, data)

    report: dict[str, Any] = {
        "name": name,
        "format": fmt,
        "bytes": len(data),
        "is_text_format": fmt in TEXT_FORMATS,
    }

    if opts["metadata"]:
        report["metadata"] = inspect_file(data, name, use_tools=opts["use_tools"]).to_dict()

    if opts["layer_a"] and fmt in TEXT_FORMATS:
        text = data.decode("utf-8", "surrogateescape")
        report["layer_a"] = layer_a_scan(
            text,
            aggressive=opts["aggressive"],
            strip_emoji_glue=opts["strip_emoji_glue"],
        ).to_dict()

    report["summary"] = _summarize(report)
    return report


def handle_clean(payload: dict[str, Any]) -> dict[str, Any]:
    data, name = _decode_input(payload)
    opts = _options(payload)
    fmt = detect_format(name, data)
    original = data

    report: dict[str, Any] = {"name": name, "format": fmt, "input_bytes": len(data)}

    if opts["metadata"]:
        try:
            data, meta = clean_file(data, name)
        except MalformedFile as exc:
            raise HttpError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "file is malformed", str(exc)
            ) from exc
        report["metadata"] = meta.to_dict()

    if opts["layer_a"] and fmt in TEXT_FORMATS:
        text = data.decode("utf-8", "surrogateescape")
        cleaned = layer_a_clean(
            text,
            aggressive=opts["aggressive"],
            strip_emoji_glue=opts["strip_emoji_glue"],
            normalize=opts["normalize"],
        )
        data = cleaned.text.encode("utf-8", "surrogateescape")
        report["layer_a"] = cleaned.to_dict()
        if opts["diff"]:
            report["diff"] = diff_ops(original.decode("utf-8", "surrogateescape"), cleaned.text)

    report["output_bytes"] = len(data)
    report["bytes_removed"] = len(original) - len(data)
    report["summary"] = _summarize(report)
    return {
        "cleaned": base64.b64encode(data).decode("ascii"),
        "report": report,
    }


def handle_rewrite(payload: dict[str, Any]) -> dict[str, Any]:
    text = payload.get("text")
    if not isinstance(text, str):
        raise HttpError(HTTPStatus.BAD_REQUEST, "text is required and must be a string")
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "text too large")

    try:
        cfg = BackendConfig(
            backend=str(payload.get("backend", "print-prompt")),
            model=str(payload.get("model", "llama3.1")),
            base_url=str(payload.get("base_url", "http://127.0.0.1:11434")),
            temperature=float(payload.get("temperature", 0.9)),
            timeout=int(payload.get("timeout", 180)),
            allow_remote=bool(payload.get("allow_remote", False)),
            # Supplied by the UI's key field. Never logged; never echoed back.
            api_key_override=(payload.get("api_key") or None),
        )
        result = rewrite(
            text,
            strength=str(payload.get("strength", "paraphrase")),
            cfg=cfg,
            candidates=int(payload.get("candidates", 1)),
            layer_a_after=bool(payload.get("layer_a_after", True)),
            pivot=str(payload.get("pivot", "French")),
        )
    except RewriteError as exc:
        raise HttpError(HTTPStatus.BAD_GATEWAY, "rewrite failed", str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HttpError(HTTPStatus.BAD_REQUEST, "invalid rewrite options", str(exc)) from exc
    return result.to_dict()


def _summarize(report: dict[str, Any]) -> dict[str, Any]:
    meta = report.get("metadata") or {}
    la = report.get("layer_a") or {}
    metadata_findings = meta.get("count", 0)
    invisible = la.get("total_removable", la.get("total_removed", 0))
    return {
        "metadata_findings": metadata_findings,
        "c2pa_present": meta.get("c2pa_present", False),
        "invisible_chars": invisible,
        # Two distinct claims, kept separate on purpose.
        #
        # findings_present: there is something here to strip. Author names and
        # EXIF camera data count -- they are not watermarks, but they are
        # exactly what someone cleaning a file wants gone.
        #
        # watermark_suspected: the stronger claim, reserved for provenance
        # marks and deliberately hidden characters.
        "findings_present": bool(metadata_findings or invisible or la.get("total_removed", 0)),
        "watermark_suspected": bool(
            meta.get("c2pa_present")
            or la.get("watermark_suspected")
            or la.get("total_removed", 0) > 0
        ),
    }


ROUTES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "/inspect": handle_inspect,
    "/clean": handle_clean,
    "/rewrite": handle_rewrite,
}


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = f"scrubai/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    api_key: str | None = None
    ui_enabled: bool = True

    # ------------------------------------------------------------- helpers

    def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if status >= 400:
            # We may have rejected before draining the request body (an
            # oversized Content-Length, for one), so the stream is out of sync
            # and cannot be reused for a keep-alive request.
            self.close_connection = True
            self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str, detail: str = "") -> None:
        self._send_json(status, {"error": message, "detail": detail})

    def _authorized(self) -> bool:
        if not self.api_key:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:], self.api_key)

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from exc
        if length <= 0:
            raise HttpError(HTTPStatus.BAD_REQUEST, "empty body")
        if length > MAX_BODY_BYTES:
            raise HttpError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"body is {length} bytes, limit is {MAX_BODY_BYTES}",
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HttpError(HTTPStatus.BAD_REQUEST, "truncated body")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, "body is not valid JSON", str(exc)) from exc
        if not isinstance(payload, dict):
            raise HttpError(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
        return payload

    # ---------------------------------------------------------------- verbs

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path

        if path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "version": __version__,
                    "uptime_seconds": round(time.monotonic() - START_TIME, 1),
                },
            )
            return

        if not self._authorized():
            self._error(HTTPStatus.UNAUTHORIZED, "missing or invalid bearer token")
            return

        if path == "/capabilities":
            self._send_json(HTTPStatus.OK, capabilities())
            return

        if path == "/openapi.json":
            host = self.headers.get("Host", "127.0.0.1:8765")
            self._send_json(HTTPStatus.OK, spec(f"http://{host}"))
            return

        if path in ("/", "/ui", "/ui/"):
            self._serve_ui("index.html")
            return

        if path.startswith("/ui/"):
            self._serve_ui(path[len("/ui/") :])
            return

        self._error(HTTPStatus.NOT_FOUND, "no such endpoint")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if not self._authorized():
            self._error(HTTPStatus.UNAUTHORIZED, "missing or invalid bearer token")
            return

        handler = ROUTES.get(path)
        if handler is None:
            self._error(HTTPStatus.NOT_FOUND, "no such endpoint")
            return

        try:
            payload = self._read_body()
            self._send_json(HTTPStatus.OK, handler(payload))
        except HttpError as exc:
            self._error(exc.status, exc.message, exc.detail)
        except Exception as exc:
            LOG.exception("unhandled error on %s", path)
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal error",
                f"{type(exc).__name__}: {exc}"[:300],
            )

    # ------------------------------------------------------------------ ui

    def _serve_ui(self, rel: str) -> None:
        if not self.ui_enabled:
            self._error(HTTPStatus.NOT_FOUND, "UI is disabled")
            return

        # Normalize and confine to UI_DIR.
        rel = posixpath.normpath("/" + urllib.parse.unquote(rel)).lstrip("/")
        target = (UI_DIR / rel).resolve()
        try:
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            self._error(HTTPStatus.FORBIDDEN, "path traversal refused")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, f"no such file: {rel}")
            return

        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    # --------------------------------------------------------------- misc

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s %s", self.address_string(), fmt % args)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scrubai-server",
        description="Local watermark removal service (stdlib only).",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-ui", action="store_true", help="do not serve the web frontend")
    p.add_argument(
        "--allow-non-local",
        action="store_true",
        help=f"permit binding a non-loopback address (requires {API_KEY_ENV})",
    )
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    api_key = os.environ.get(API_KEY_ENV) or None
    is_local = args.host in ("127.0.0.1", "::1", "localhost")

    if not is_local and not args.allow_non_local:
        print(
            f"error: refusing to bind {args.host}. Pass --allow-non-local if you "
            f"really want the service reachable from the network.",
            file=sys.stderr,
        )
        return 2
    if not is_local and not api_key:
        # A container binds 0.0.0.0 by design and relies on the port mapping
        # (127.0.0.1:8765:8765) to stay private. That is a legitimate setup, but
        # it has to be stated explicitly, because the same bind on a host
        # exposes the service to the whole network.
        if os.environ.get("WATERMARKS_ALLOW_INSECURE_BIND") == "1":
            LOG.warning(
                "binding %s with no API key because WATERMARKS_ALLOW_INSECURE_BIND=1. "
                "Anything that can reach this port can use the service.",
                args.host,
            )
        else:
            print(
                f"error: binding {args.host} with no API key would expose this "
                f"service to the network. Either set {API_KEY_ENV}, or set "
                f"WATERMARKS_ALLOW_INSECURE_BIND=1 if the port is already "
                f"confined (a container published to 127.0.0.1, for example).",
                file=sys.stderr,
            )
            return 2

    Handler.api_key = api_key
    Handler.ui_enabled = not args.no_ui

    httpd = Server((args.host, args.port), Handler)
    caps = capabilities()
    installed = [n for n, t in caps["tools"].items() if t["installed"]]
    missing = [n for n, t in caps["tools"].items() if not t["installed"]]

    LOG.info("scrubai %s listening on http://%s:%d", __version__, args.host, args.port)
    LOG.info("auth: %s", "bearer token required" if api_key else "none (loopback only)")
    LOG.info("tools installed: %s", ", ".join(installed) or "none")
    if missing:
        LOG.info("tools missing: %s (see GET /capabilities)", ", ".join(missing))
    LOG.info("pdf tier: %s", caps["pdf_tier"])
    if Handler.ui_enabled:
        LOG.info("UI at http://%s:%d/ui/", args.host, args.port)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOG.info("shutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
