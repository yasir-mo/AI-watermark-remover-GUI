"""Load the built page in a real browser and fail on any console error.

A duplicate `const` in app.js once shipped to the live site. It is a static
SyntaxError, so the module never loaded and every control on the page was
dead, yet every other check passed: the Python tests do not touch the browser
build, the JS tests import modules individually rather than loading the page,
and the DOM id check only reads the files as text.

This runs the page the way a visitor does, so that class of fault cannot reach
the site again.

    python -m tools.check_page_boots            # serves web/ and checks it
    python -m tools.check_page_boots --url URL  # checks a deployed page
"""

from __future__ import annotations

import argparse
import http.server
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

BROWSERS = [
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "google-chrome",
    "chromium",
    "chromium-browser",
]

# Markers that prove app.js ran to completion and populated the page.
REQUIRED = {
    "example cards": 'class="example"',
    "provider list": '<option value="openrouter"',
    "demo cards": "Load into Strip",
}

ERROR_RE = re.compile(
    r"Uncaught|SyntaxError|ReferenceError|TypeError|is not defined|"
    r"Failed to (?:load|fetch)|Content Security Policy",
    re.I,
)

# Console noise that is not an application fault.
IGNORE_RE = re.compile(r"Password field is not contained in a form", re.I)


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if Path(candidate).exists():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_page_boots")
    ap.add_argument("--url", help="check a deployed URL instead of serving web/")
    ap.add_argument(
        "--require-browser",
        action="store_true",
        help="fail when no browser is available, instead of skipping (use in CI)",
    )
    args = ap.parse_args(argv)

    browser = find_browser()
    if browser is None:
        if args.require_browser:
            print(
                "no Chrome or Edge found, and --require-browser was given. "
                "This check is the only one that loads the page the way a "
                "visitor does, so a silent skip would defeat it.",
                file=sys.stderr,
            )
            return 1
        print("no Chrome or Edge found; skipping the browser boot check")
        return 0

    httpd = None
    if args.url:
        url = args.url
    else:
        httpd, port = serve(WEB)
        url = f"http://127.0.0.1:{port}/"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(  # noqa: S603
                [
                    browser,
                    "--headless",
                    "--disable-gpu",
                    "--no-sandbox",
                    f"--user-data-dir={tmp}",
                    "--enable-logging=stderr",
                    "--v=0",
                    "--virtual-time-budget=9000",
                    "--dump-dom",
                    url,
                ],
                capture_output=True,
                timeout=180,
            )
    finally:
        if httpd:
            httpd.shutdown()

    dom = proc.stdout.decode("utf-8", "replace")
    log = proc.stderr.decode("utf-8", "replace")

    failures: list[str] = []

    for line in log.splitlines():
        if IGNORE_RE.search(line):
            continue
        if ERROR_RE.search(line):
            failures.append(f"console: {line.strip()[:200]}")

    for label, marker in REQUIRED.items():
        if marker not in dom:
            failures.append(f"{label} missing from the rendered page ({marker!r})")

    if failures:
        print(f"page boot check FAILED for {url}", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    print(f"page boots cleanly: {url}")
    for label, marker in REQUIRED.items():
        print(f"  present: {label} ({dom.count(marker)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
