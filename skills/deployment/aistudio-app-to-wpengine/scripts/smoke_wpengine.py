#!/usr/bin/env python3
"""Keyless smoke test for an Express+Vite app behind WP Engine.

Probes a RUNNING server and reports on the three ways an AI Studio export fails
on a host that injects no secrets:

  1. the site loads at all
  2. Vite's host check is not blocking the platform's Host header
  3. every API route answers, instead of 503 / 500 / hanging

Start the app keyless first. An empty value that is already present in the
environment is never overridden by dotenv, so this reproduces a zero-secret host
without touching ``.env``:

    GEMINI_API_KEY='' AUTO_DEV_API_KEY='' npm run dev

Then probe it:

    python3 smoke_wpengine.py --host-to-spoof <slug>.js.wpenginepowered.com
    python3 smoke_wpengine.py --probes probes.json --port 3210

The script deliberately does not start the app itself. Keeping the harness to
plain HTTP requests means it needs no process control, and the launch stays a
command you can read before you run it.

Probes file (a skeleton is generated from server.ts when --probes is omitted):

    [{"method": "POST", "path": "/api/x", "json": {...}, "allowed": [503]}]

Verdicts: PASS on 2xx (or an allowed status), WARN on a 4xx such as 400 - the
route is reachable but needs a real payload - and FAIL on a 5xx or any request
that never answers. A hang is a failure, never slowness.

Exit codes: 0 everything passed, 1 at least one failure, 2 nothing was listening.
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import socket
import sys
import time
from pathlib import Path

FAIL_STATUSES = range(500, 600)
DEFAULT_PORT = 3000


def listening(host: str, port: int, timeout: float = 2.0) -> bool:
    """Is anything accepting connections there?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(host, port, method, path, payload=None, host_header=None, timeout=35.0):
    """One request. Returns (status|None, seconds, body, note)."""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    if host_header:
        headers["Host"] = host_header
    started = time.monotonic()
    try:
        conn.putrequest(method, path, skip_host=bool(host_header), skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders(body)
        response = conn.getresponse()
        text = response.read(300).decode("utf-8", "replace")
        return response.status, round(time.monotonic() - started, 2), text, ""
    except socket.timeout:
        return None, round(time.monotonic() - started, 2), "", "HANG (no answer inside the timeout)"
    except Exception as exc:  # refused, reset, ...
        return None, round(time.monotonic() - started, 2), "", f"ERROR {exc}"
    finally:
        conn.close()


def looks_like_html(body: str) -> bool:
    head = body.lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html")


def classify(status, allowed, body: str = "", expect_json: bool = False) -> str:
    """PASS / WARN / FAIL for one probe.

    Two false-pass guards, both learned the hard way. A dev server answers an
    unknown path with the SPA shell and a 200, which reads as success until you
    notice the body is HTML where JSON was expected. And a 404 means the route does
    not exist at all - a stale probe, or a different app answering on this port -
    which is a failure, not a warning.
    """
    if status is None:
        return "FAIL"
    if status in allowed:
        return "PASS"
    if 200 <= status < 300:
        if expect_json and looks_like_html(body):
            return "FAIL"
        return "PASS"
    if status in FAIL_STATUSES or status == 404:
        return "FAIL"
    return "WARN"


def starter_probes(repo: Path) -> list:
    """Derive a probe skeleton from the routes declared in server.ts."""
    source = repo / "server.ts"
    if not source.exists():
        return []
    text = source.read_text(encoding="utf-8", errors="replace")
    probes = []
    for match in re.finditer(r"app\.(get|post|put|delete)\(\s*'([^']+)'", text):
        method, path = match.group(1).upper(), match.group(2)
        if path == "*":
            continue
        item = {"method": method, "path": path}
        if method != "GET":
            item["json"] = {}
        if path.startswith("/api/vin-decode"):
            item["allowed"] = [503]      # needs a live vendor key by design
        probes.append(item)
    return probes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".", help="project root (holds server.ts)")
    parser.add_argument("--connect-host", default="localhost")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host-to-spoof", default="", help="the platform hostname Vite must allow")
    parser.add_argument("--probes", default="", help="JSON list of {method,path,json?,allowed?}")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()

    if not listening(args.connect_host, args.port):
        print(f"FAIL: nothing is listening on {args.connect_host}:{args.port}")
        print("Start the app keyless first - an empty value already in the environment is")
        print("never overridden by dotenv, so no secret from .env reaches it:")
        print("  GEMINI_API_KEY='' AUTO_DEV_API_KEY='' npm run dev")
        return 2

    probes_path = Path(args.probes) if args.probes else repo / "probes.json"
    if probes_path.exists():
        probes = json.loads(probes_path.read_text(encoding="utf-8"))
    else:
        probes = starter_probes(repo)
        if probes:
            probes_path.write_text(json.dumps(probes, indent=2), encoding="utf-8")
            print(f"# wrote a starter probe set to {probes_path} - give the routes real payloads")

    rows, failures = [], 0

    # These two must be a plain 2xx: a 403 here is the exact bug this script exists
    # for, so it is a FAIL, never a WARN.
    for label, host_header in (
        ("site loads", None),
        (f"host allowed ({args.host_to_spoof or 'none given'})", args.host_to_spoof or None),
    ):
        status, secs, text, note = probe(args.connect_host, args.port, "GET", "/", None, host_header)
        verdict = "PASS" if status is not None and 200 <= status < 300 else "FAIL"
        failures += verdict == "FAIL"
        rows.append((verdict, status, secs, label, text[:70].replace("\n", " "), note))

    for item in probes:
        method = item.get("method", "GET").upper()
        status, secs, text, note = probe(
            args.connect_host, args.port, method, item["path"],
            item.get("json"), None, item.get("timeout", 35.0),
        )
        verdict = classify(status, item.get("allowed", []), text,
                           expect_json=item["path"].startswith("/api/"))
        if verdict == "FAIL" and status is not None and 200 <= status < 300 and looks_like_html(text):
            note = "served the SPA shell, not JSON - this route does not exist here"
        elif verdict == "FAIL" and status == 404:
            note = "route not found - stale probe, or another app is answering on this port"
        elif verdict == "WARN" and status == 400 and not note:
            note = "route reachable - supply a payload in probes.json"
        failures += verdict == "FAIL"
        rows.append((verdict, status, secs, f"{method} {item['path']}", text[:70].replace("\n", " "), note))

    print(f"\n{'':4} {'code':>5} {'secs':>6}  {'what':38}  body / note")
    for verdict, status, secs, label, text, note in rows:
        print(f"{verdict:4} {str(status):>5} {secs:>6}  {label[:38]:38}  {note or text}")

    print(f"\n{len(rows) - failures}/{len(rows)} passed")
    if failures:
        print("FAILURES: a 403 on the host row means Vite's middleware host check is still"
              " blocking the platform hostname. A 5xx or a hang on a route means it still"
              " needs its offline fallback, or the upstream call is unbounded.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
