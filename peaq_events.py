#!/usr/bin/env python3
"""Record completed, paid RAAS rentals as peaqOS revenue events.

Runs on the operator's peaqOS laptop (Python 3.10+, stdlib only). Pulls the rentals RAAS has not yet
posted, builds one `peaqos qualify event` per rental and, with --post, runs it and marks the rental
posted so it is never submitted twice.

  RAAS_API_URL=https://raas-api.sixthsenselabs.xyz ADMIN_TOKEN=... python peaq_events.py   # dry run
  PEAQOS_OWS_WALLET=<wallet name> ... python peaq_events.py --post                          # submit
  python peaq_events.py --selftest

Env: RAAS_API_URL and ADMIN_TOKEN (required); PEAQ_MACHINE_ID (optional, overrides the machine id the
server reports); PEAQOS_OWS_WALLET (required for --post; read by the peaqos CLI itself). This script
never reads OWS_PASSPHRASE: the CLI prompts for it on the terminal. The rental's Solana pay_sig is
never printed.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# ponytail: the peaqOS docs only show `qualify event --type activity --value 0 --ts <iso>`; revenue
# events carry USD cents in --value. Confirm the exact flags (currency / metadata, if the CLI wants
# them) with `peaqos qualify event --help` on the laptop and edit ONLY this list. {placeholders} are
# filled per rental; everything else is passed through verbatim.
PEAQOS_EVENT = ["peaqos", "qualify", "event", "--machine-id", "{machine_id}", "--type", "revenue",
                "--value", "{usd_cents}", "--ts", "{ts_iso}"]

SAFE_ID = re.compile(r"[A-Za-z0-9:._-]+")  # server data goes onto a command line (a .cmd shim on Windows)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # a redirect would carry ADMIN_TOKEN elsewhere; fail instead
        return None


_open = urllib.request.build_opener(_NoRedirect).open


def check_base(base):
    u = urlsplit(base)
    if u.scheme != "https" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
        sys.exit("RAAS_API_URL must be https:// (ADMIN_TOKEN would travel in clear text)")


def api(base, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data, {"Authorization": "Bearer " + token,
                                                    "Content-Type": "application/json"}, method=method)
    with _open(req, timeout=30) as r:
        return json.load(r)


def build(row, machine_id=None):
    mid, cents, ts = str(machine_id or row["machine_id"]), row["usd_cents"], row["ts"]
    if not (SAFE_ID.fullmatch(mid) and type(cents) is int and cents > 0 and type(ts) is int and ts > 0):
        raise ValueError(f"[{row.get('id')}] unexpected machine_id/usd_cents/ts from RAAS; nothing run")
    fill = {"machine_id": mid, "usd_cents": cents,
            "ts_iso": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return [a.format(**fill) for a in PEAQOS_EVENT]


def run(cmd):
    """Run the CLI with the terminal attached (stdin and stderr inherited, so its passphrase prompt
    works). stdout is relayed byte for byte as it arrives and returned, so its last line can be kept
    as the ref. ponytail: if the CLI refuses a piped stdout, inherit it too and post an empty ref."""
    exe = shutil.which(cmd[0])  # finds peaqos.cmd on Windows, which Popen alone would not
    if not exe:
        sys.exit(f"{cmd[0]} not found on PATH")
    sys.stdout.flush()
    p = subprocess.Popen([exe, *cmd[1:]], stdout=subprocess.PIPE)
    out = bytearray()
    for chunk in iter(lambda: os.read(p.stdout.fileno(), 4096), b""):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        out += chunk
    return p.wait(), out.decode("utf-8", "replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the peaqos commands only (default)")
    mode.add_argument("--post", action="store_true", help="run peaqos for each pending rental and mark it posted")
    mode.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    base, token = os.environ.get("RAAS_API_URL", "").rstrip("/"), os.environ.get("ADMIN_TOKEN", "")
    if not base or not token:
        sys.exit("set RAAS_API_URL and ADMIN_TOKEN")
    check_base(base)
    if args.post and not os.environ.get("PEAQOS_OWS_WALLET"):
        sys.exit("set PEAQOS_OWS_WALLET to the OWS wallet name before --post")
    machine_id = os.environ.get("PEAQ_MACHINE_ID")

    pending = api(base, token, "GET", "/api/admin/peaq-pending")
    print(f"{len(pending)} pending rental(s)" + ("" if args.post else " (dry run; add --post to submit)"))
    for row in pending:
        try:
            cmd = build(row, machine_id)
        except ValueError as e:
            sys.exit(str(e))
        print(f"[{row['id']}] {shlex.join(cmd)}")
        if not args.post:
            continue
        rc, out = run(cmd)
        if rc != 0:
            sys.exit(f"[{row['id']}] peaqos exited {rc}; stopping, nothing marked")
        ref = (out.strip().splitlines() or [""])[-1].strip()[:200]
        try:
            api(base, token, "POST", f"/api/admin/bookings/{row['id']}/peaq-posted", {"ref": ref})
        except Exception as e:  # the event is submitted but RAAS does not know; a re-run would post it twice
            sys.exit(f"[{row['id']}] submitted (ref {ref!r}) but marking it failed: {e}\n"
                     f"mark it by hand before re-running: POST {base}/api/admin/bookings/{row['id']}/peaq-posted"
                     f' {{"ref": "{ref}"}}')
        print(f"[{row['id']}] marked posted, ref {ref!r}")


def selftest():
    import http.server
    import io
    import tempfile
    import threading

    row = {"id": "bk1", "usd_cents": 5000, "ts": 1767225600, "pay_sig": "PAYSIG_NEVER_PRINTED", "machine_id": "720700"}
    auths, posted = [], []

    class Fake(http.server.BaseHTTPRequestHandler):
        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            auths.append((self.path, self.headers["Authorization"]))
            self._send([] if posted else [row])

        def do_POST(self):
            auths.append((self.path, self.headers["Authorization"]))
            posted.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self._send({})

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tmp = Path(tempfile.mkdtemp(prefix="peaq-selftest-"))
    args_file, fake_py = tmp / "args.json", tmp / "fake_peaqos.py"
    fake_py.write_text("import json, sys\n"
                       f"json.dump(sys.argv[1:], open({str(args_file)!r}, 'w'))\n"
                       "print('Enter wallet passphrase:', file=sys.stderr)\n"
                       "print('event queued')\nprint('0xFAKE_TX_SIG')\n")
    if os.name == "nt":
        (tmp / "peaqos.cmd").write_text(f'@"{sys.executable}" "{fake_py}" %*\n')
    else:
        exe = tmp / "peaqos"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_py}" "$@"\n')
        exe.chmod(0o755)
    os.environ.update(RAAS_API_URL=f"http://127.0.0.1:{srv.server_port}", ADMIN_TOKEN="test-token",
                      PEAQOS_OWS_WALLET="fake-wallet", PATH=str(tmp) + os.pathsep + os.environ.get("PATH", ""))
    os.environ.pop("PEAQ_MACHINE_ID", None)
    real, cap = sys.stdout, io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True)
    try:
        cmd = build(row)
        assert cmd == ["peaqos", "qualify", "event", "--machine-id", "720700", "--type", "revenue",
                       "--value", "5000", "--ts", "2026-01-01T00:00:00Z"], cmd
        assert "--machine-id other" in " ".join(build(row, "other"))
        for bad in ({"machine_id": "1&calc"}, {"usd_cents": "5000"}, {"ts": None}):
            try:
                build({**row, **bad})
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass
        try:
            check_base("http://raas.example")
            raise AssertionError("plain-http RAAS_API_URL accepted")
        except SystemExit:
            pass

        sys.stdout = cap
        main(["--dry-run"])
        assert not args_file.exists() and posted == [], "dry run must not run peaqos or mark anything"
        main(["--post"])
        sys.stdout = real
        printed = cap.buffer.getvalue().decode()
        print(printed, end="")
        assert json.loads(args_file.read_text()) == cmd[1:], args_file.read_text()
        assert posted == [("/api/admin/bookings/bk1/peaq-posted", {"ref": "0xFAKE_TX_SIG"})], posted
        assert auths and all(a == "Bearer test-token" for _, a in auths), auths
        assert "0xFAKE_TX_SIG" in printed and row["pay_sig"] not in printed, "pay_sig must never be printed"
    finally:
        sys.stdout = real
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("PEAQ-EVENTS-OK")


if __name__ == "__main__":
    main()
