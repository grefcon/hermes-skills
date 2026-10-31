#!/usr/bin/env python3
"""Keyless + host-header smoke test for an Express+Vite app on WP Engine.

Reproduces the three ways an AI Studio export fails on a secret-less host, using
only the stdlib:

  1. the site loads at all (GET /)
  2. Vite's host check is not blocking (GET / with the platform's Host header)
  3. every API route answers instead of 503/500/hanging

It starts the app itself with the API keys forced to EMPTY STRINGS. dotenv does
not override a variable that already exists, so this reproduces WP Engine's
zero-secret environment without touching .env. If --probes is omitted, a starter
file is generated from server.ts for you to fill in.

Usage
-----
  python3 smoke_wpengine.py --host-to-spoof <slug>.js.wpenginepowered.com
  python3 smoke_wpengine.py --probes probes.json --port 3210
  python3 smoke_wpengine.py --no-launch --port 3000   # test a server already running

Exit codes: 0 all checks passed, 1 at least one failure, 2 the app never came up.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KEYLESS_ENV = ("GEMINI_API_KEY", "AUTO_DEV_API_KEY")
FAIL_STATUSES = range(500, 600)


def wait_for_port(host: str, port: int, proc, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def probe(host: str, port: int, method: str, path: str, payload=None, host_header=None, timeout=35.0):
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
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders(body)
        resp = conn.getresponse()
        text = resp.read(300).decode("utf-8", "replace")
        return resp.status, round(time.monotonic() - started, 2), text, ""
    except socket.timeout:
        return None, round(time.monotonic() - started, 2), "", "HANG (no response inside the timeout)"
    except Exception as exc:  # connection refused, reset, ...
        return None, round(time.monotonic() - started, 2), "", f"ERROR {exc}"
    finally:
        conn.close()


def classify(status, allowed):
    if status is None:
        return "FAIL"
    if status in allowed:
        return "PASS"
    if 200 <= status < 300:
        return "PASS"
    if status in FAIL_STATUSES:
        return "FAIL"
    return "WARN"


def starter_probes(repo: Path) -> list[dict]:
    """Derive a probes skeleton from the routes declared in server.ts."""
    src = repo / "server.ts"
    if not src.exists():
        return []
    text = src.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r"app\.(get|post|put|delete)\(\s*'([^']+)'", text):
        method, path = m.group(1).upper(), m.group(2)
        if path == "*" or path.startswith("/api/vin-decode"):
            continue
        item = {"method": method, "path": path}
        if method != "GET":
            item["json"] = {}
        if path.startswith("/api/vin-decode"):
            item["allowed"] = [503]
        out.append(item)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".", help="project root (holds server.ts)")
    ap.add_argument("--port", type=int, default=3000)
    ap.add_argument("--connect-host", default="127.0.0.1")
    ap.add_argument("--host-to-spoof", default="", help="the platform hostname Vite must allow")
    ap.add_argument("--probes", default="", help="JSON list of {method,path,json?,allowed?}")
    ap.add_argument("--no-launch", action="store_true", help="test a server that is already running")
    ap.add_argument("--cmd", default="npm run dev", help="how to start the app")
    ap.add_argument("--boot-timeout", type=float, default=60.0)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    proc = log = None
    log_path = Path(tempfile.gettempdir()) / "smoke-wpengine.log"

    if not args.no_launch:
        env = dict(os.environ)
        env.pop("NODE_ENV", None)  # the platform does not set it
        env["PORT"] = str(args.port)
        for key in KEYLESS_ENV:
            env[key] = ""  # empty survives dotenv and means "no secret"
        log = open(log_path, "w", encoding="utf-8")
        print(f"# launching (keyless): {args.cmd}  port={args.port}")
        proc = subprocess.Popen(args.cmd, shell=True, cwd=repo, env=env,
                                stdout=log, stderr=subprocess.STDOUT)

    if not wait_for_port(args.connect_host, args.port, proc, args.boot_timeout):
        print("FAIL: the app never accepted a connection")
        if log:
            log.flush()
            print("---- app log tail ----")
            print("".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines(True)[-25:]))
        if proc:
            proc.terminate()
        return 2

    probes_path = Path(args.probes) if args.probes else repo / "probes.json"
    probes = []
    if probes_path.exists():
        probes = json.loads(probes_path.read_text(encoding="utf-8"))
    else:
        probes = starter_probes(repo)
        if probes:
            probes_path.write_text(json.dumps(probes, indent=2), encoding="utf-8")
            print(f"# wrote a starter probe set to {probes_path} - fill in realistic payloads")

    rows, failures = [], 0
    # These two must be a plain 2xx: a 403 here is the exact bug this script exists for,
    # so it is a FAIL, never a WARN.
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
        path = item["path"]
        allowed = item.get("allowed", [])
        status, secs, text, note = probe(args.connect_host, args.port, method, path,
                                         item.get("json"), None, item.get("timeout", 35.0))
        verdict = classify(status, allowed)
        if verdict == "WARN" and status == 400:
            note = note or "route reachable - supply a payload in probes.json"
        failures += verdict == "FAIL"
        rows.append((verdict, status, secs, f"{method} {path}", text[:70].replace("\n", " "), note))

    print(f"\n{'':4} {'code':>5} {'secs':>6}  {'what':38}  body / note")
    for verdict, status, secs, label, text, note in rows:
        print(f"{verdict:4} {str(status):>5} {secs:>6}  {label[:38]:38}  {note or text}")

    if proc:
        proc.terminate()
    print(f"\n{len(rows) - failures}/{len(rows)} passed")
    if failures:
        print("FAILURES: a 403 on the host row means Vite's middleware host check is still"
              " blocking the platform hostname (pass allowedHosts inline to createViteServer)."
              " A 5xx or a hang on an endpoint means it still needs its offline fallback, or the"
              " upstream call is unbounded.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
