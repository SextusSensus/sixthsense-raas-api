#!/usr/bin/env python3
"""RAAS robot agent: mirrors renter input files onto the robot's RAM disk and ships results back.

Runs on the Booster K1 (Ubuntu 22.04, system Python 3.10). Stdlib only: the system python carries
ROS and numpy and must not be touched. Polls the RAAS server; for every ACTIVE booking it keeps
<RAAS_DIR>/<id>/in current and writes BRIEF.txt; for every booking the server wants purged it uploads
<RAAS_DIR>/<id>/out, deletes the whole tree, then confirms. It never executes anything a renter
uploaded: the operator reviews and runs renter files by hand.

Env: RAAS_API_URL, ROBOT_TOKEN, RAAS_DIR (default /mnt/raas), RAAS_POLL_S (default 5).
Flags: --once (one poll), --allow-disk (RAAS_DIR need not be a mount point; local tests only),
--selftest (whole flow against an in-process fake server; prints ROBOT-AGENT-OK).
"""
import argparse
import collections
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MIN_FREE = 256 * 2**20
CHUNK = 1 << 20
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")   # server ids are token_urlsafe; anything else is not ours


PERMANENT = {404, 409, 410, 413}  # server answers that retrying can never change


class Refused(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


def log(*parts):
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), *parts, file=sys.stderr, flush=True)


def err(e):
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = e.read(200).decode("utf-8", "replace")
        except Exception:
            detail = ""
        return f"HTTP {e.code} {detail}".strip()
    return f"{type(e).__name__}: {e}"


def free_bytes(path):
    return shutil.disk_usage(path).free


def safe_name(name, fallback):
    # The server already sanitizes, but renter data lands on our disk: do not trust it with paths.
    # Never starts with "." so it cannot collide with our hidden ".<name>.part" temp files.
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(str(name or ""))).strip(". ")
    return name[:120] or fallback


def _raise(e):
    raise e


def config(environ, allow_disk):
    """((api, token, root, poll), problems). Fail closed: any problem means the agent must not start."""
    api = environ.get("RAAS_API_URL", "").strip().rstrip("/")
    token = environ.get("ROBOT_TOKEN", "").strip()
    root = environ.get("RAAS_DIR", "/mnt/raas")
    problems = []
    u = urllib.parse.urlparse(api)
    if not u.hostname or not (u.scheme == "https" or
                              (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1"))):
        problems.append("RAAS_API_URL must be an https:// URL (plain http only to localhost)")
    if not token:
        problems.append("ROBOT_TOKEN is not set")
    try:
        poll = float(environ.get("RAAS_POLL_S", "5"))
    except ValueError:
        poll = 0.0
    if not 0 < poll < 3600:  # also rejects nan
        problems.append("RAAS_POLL_S must be a number of seconds in (0, 3600)")
    if allow_disk:
        os.makedirs(root, mode=0o700, exist_ok=True)
    elif not os.path.ismount(root):
        problems.append(f"RAAS_DIR={root} is not a mount point (mount the tmpfs; --allow-disk is for local tests only)")
    if os.path.isdir(root) and not os.access(root, os.W_OK):
        problems.append(f"RAAS_DIR={root} is not writable")
    return (api, token, root, poll), problems


class Agent:
    retry_sleep = 2.0

    def __init__(self, api, token, root):
        self.api, self.root = api.rstrip("/"), root
        self.headers = {"Authorization": f"Bearer {token}"}

    def request(self, method, path, data=None, extra=None, timeout=30):
        req = urllib.request.Request(self.api + path, data=data, method=method,
                                     headers={**self.headers, **(extra or {})})
        return urllib.request.urlopen(req, timeout=timeout)

    def tick(self):
        try:
            with self.request("GET", "/api/robot/jobs") as r:
                jobs = json.load(r)
            active, purge = list(jobs.get("active", [])), list(jobs.get("purge", []))
        except Exception as e:  # includes a reply that is not the expected shape
            log("jobs: fetch failed:", err(e))
            return
        for job in active:
            bid = str(job.get("id", "")) if isinstance(job, dict) else ""
            if not ID_RE.fullmatch(bid):
                log("active: skipping bad booking id")
                continue
            try:
                self.sync(bid, job)
            except Exception as e:
                log(f"sync {bid}: failed:", err(e))
        for bid in purge:
            bid = str(bid)
            if not ID_RE.fullmatch(bid):
                log("purge: skipping bad booking id")
                continue
            try:
                self.purge(bid)
            except Exception as e:
                log(f"purge {bid}: failed:", err(e))

    # --- active bookings ------------------------------------------------------------------------

    def sync(self, bid, job):
        d = os.path.join(self.root, bid)
        inn = os.path.join(d, "in")
        os.makedirs(inn, exist_ok=True)
        os.makedirs(os.path.join(d, "out"), exist_ok=True)
        brief = os.path.join(d, "BRIEF.txt")
        if not os.path.exists(brief):
            start = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(job.get("slot_start") or 0)))
            with open(brief, "w", encoding="utf-8") as f:
                f.write(f"booking: {bid}\nsession_type: {job.get('session_type', '')}\nslot_start: {start}\n"
                        f"hours: {job.get('hours', '')}\n\nRenter brief:\n{job.get('brief', '')}\n\n"
                        "Input files are in in/. Put results in out/; they are uploaded when the session is "
                        "purged.\nREVIEW BEFORE RUNNING ANYTHING. The agent never executes renter files.\n")
            log(f"sync {bid}: created {d}")
        files = [f for f in job.get("files", []) if isinstance(f, dict) and ID_RE.fullmatch(str(f.get("id", "")))]
        names = collections.Counter(safe_name(f.get("name"), f["id"]) for f in files)
        for f in files:
            name = safe_name(f.get("name"), f["id"])
            if names[name] > 1:  # two inputs with the same name: keep both, apart
                name = f"{f['id']}-{name}"
            try:  # per file: one bad file must not block the ones after it
                dst, size = os.path.join(inn, name), int(f.get("size") or 0)
                if os.path.isfile(dst) and os.path.getsize(dst) == size:
                    continue
                if free_bytes(self.root) - size < MIN_FREE:
                    log(f"sync {bid}: skipping {name} ({size} B): would leave < {MIN_FREE >> 20} MB free")
                    continue
                self.download(f["id"], dst, size)
                log(f"sync {bid}: downloaded {name} ({size} B)")
            except Exception as e:
                log(f"sync {bid}: download {name} failed:", err(e))

    def download(self, fid, dst, size):
        part = os.path.join(os.path.dirname(dst), f".{os.path.basename(dst)}.part")
        try:
            with self.request("GET", f"/api/robot/files/{fid}", timeout=60) as r, open(part, "wb") as out:
                shutil.copyfileobj(r, out, CHUNK)
            got = os.path.getsize(part)
            if got != size:
                raise ValueError(f"size mismatch for {fid}: got {got}, expected {size}")
            os.replace(part, dst)
        except BaseException:
            try:
                os.remove(part)
            except OSError:
                pass
            raise

    # --- finished bookings ----------------------------------------------------------------------

    def purge(self, bid):
        d = os.path.join(self.root, bid)
        if os.path.isdir(d):
            out, done_file = os.path.join(d, "out"), os.path.join(d, ".uploaded")
            done, failed = set(), 0
            if os.path.exists(done_file):
                with open(done_file, encoding="utf-8") as f:
                    done = set(f.read().splitlines())
            os.makedirs(out, exist_ok=True)
            # onerror raises: an unreadable subdir must stop the purge, not be skipped and then deleted.
            for dirpath, dirnames, fnames in os.walk(out, onerror=_raise):
                dirnames.sort()
                for fn in sorted(fnames):
                    p = os.path.join(dirpath, fn)
                    if os.path.islink(p) or not os.path.isfile(p):
                        continue
                    rel = os.path.relpath(p, out).replace(os.sep, "/")
                    if rel in done:
                        continue
                    try:
                        self.upload(bid, rel, p)
                    except Refused as e:
                        # The server will never take it (booking purged/refunded, quota). Keeping the file
                        # would leave renter data on the robot forever, so it is dropped with the tree.
                        log(f"purge {bid}: server refused {rel} for good (HTTP {e.code}), discarding it")
                    except Exception as e:  # keep going: upload what we can, retry the rest next tick
                        log(f"purge {bid}: giving up on {rel} this tick:", err(e))
                        failed += 1
                        continue
                    with open(done_file, "a", encoding="utf-8") as f:
                        f.write(rel + "\n")
            if failed:
                log(f"purge {bid}: {failed} upload(s) failed, keeping {d} for the next tick")
                return
            shutil.rmtree(d)
            log(f"purge {bid}: deleted {d}")
        with self.request("POST", f"/api/robot/bookings/{bid}/purged", data=b"") as r:
            r.read()
        log(f"purge {bid}: confirmed")

    def upload(self, bid, name, path):
        size, query = os.path.getsize(path), urllib.parse.urlencode({"name": name})
        hdr = {"Content-Length": str(size), "Content-Type": "application/octet-stream"}
        for attempt in range(1, 4):
            try:
                with open(path, "rb") as f, self.request(
                        "PUT", f"/api/robot/bookings/{bid}/results?{query}", data=f, extra=hdr, timeout=120) as r:
                    r.read()
                log(f"purge {bid}: uploaded {name} ({size} B)")
                return
            except Exception as e:
                log(f"purge {bid}: upload {name} attempt {attempt} failed:", err(e))
                if isinstance(e, urllib.error.HTTPError) and e.code in PERMANENT:
                    raise Refused(e.code) from e
                if isinstance(e, urllib.error.HTTPError) and e.code < 500:
                    break  # e.g. 401/403: a config problem; keep the files and retry next tick
                if attempt < 3:
                    time.sleep(self.retry_sleep)
        raise RuntimeError(f"upload of {name} failed")


# --- selftest ------------------------------------------------------------------------------------

def selftest():
    import subprocess
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"jobs": {"active": [], "purge": []}, "blobs": {}, "downloads": 0, "uploads": [],
             "purged": [], "fail_names": set(), "refuse_names": set()}

    class Fake(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body=b"", ctype="application/octet-stream"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def ok(self):
            if self.headers.get("Authorization") != "Bearer t0k":
                self.send(403, b'{"detail":"robot only"}', "application/json")
                return False
            return True

        def do_GET(self):
            if not self.ok():
                return
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/robot/jobs":
                return self.send(200, json.dumps(state["jobs"]).encode(), "application/json")
            fid = path.rsplit("/", 1)[1]
            if path.startswith("/api/robot/files/") and fid in state["blobs"]:
                state["downloads"] += 1
                return self.send(200, state["blobs"][fid])
            self.send(404)

        def do_PUT(self):
            if not self.ok():
                return
            url = urllib.parse.urlparse(self.path)
            m = re.fullmatch(r"/api/robot/bookings/([^/]+)/results", url.path)
            body = self.rfile.read(int(self.headers["Content-Length"]))
            name = urllib.parse.parse_qs(url.query)["name"][0]
            if not m:
                return self.send(404)
            if name in state["fail_names"]:
                return self.send(500)
            if name in state["refuse_names"]:
                return self.send(409, b'{"detail":"session not running"}', "application/json")
            state["uploads"].append((m.group(1), name, body))
            self.send(200, b"{}", "application/json")

        def do_POST(self):
            if not self.ok():
                return
            m = re.fullmatch(r"/api/robot/bookings/([^/]+)/purged", urllib.parse.urlparse(self.path).path)
            if not m:
                return self.send(404)
            state["purged"].append(m.group(1))
            self.send(200, b"{}", "application/json")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api, root = f"http://127.0.0.1:{srv.server_port}", tempfile.mkdtemp(prefix="raas-agent-")
    global free_bytes
    real_free = free_bytes
    try:
        blobs = {"f1": os.urandom(2 * CHUNK + 5), "f2": b"", "f3": b"dup"}
        state["blobs"] = dict(blobs)
        active = {"id": "bk1", "session_type": "operator-run", "slot_start": 1700000000, "hours": 2,
                  "brief": "Run the thing.", "files": [
                      {"id": "f1", "name": "../evil model.onnx", "size": len(blobs["f1"])},
                      {"id": "f2", "name": "same.bin", "size": 0},
                      {"id": "f3", "name": "same.bin", "size": 3}]}
        state["jobs"] = {"active": [active], "purge": []}

        Agent(api, "wrong", root).tick()                     # 403s are logged, never raised
        assert not os.listdir(root)

        agent = Agent(api, "t0k", root)
        agent.retry_sleep = 0
        agent.tick()
        d, inn = os.path.join(root, "bk1"), os.path.join(root, "bk1", "in")
        with open(os.path.join(inn, "evil model.onnx"), "rb") as f:
            assert f.read() == blobs["f1"]
        assert os.path.getsize(os.path.join(inn, "f2-same.bin")) == 0
        with open(os.path.join(inn, "f3-same.bin"), "rb") as f:
            assert f.read() == b"dup"
        with open(os.path.join(d, "BRIEF.txt"), encoding="utf-8") as f:
            assert "Run the thing." in f.read()
        assert os.path.isdir(os.path.join(d, "out")) and not any(n.endswith(".part") for n in os.listdir(inn))
        assert state["downloads"] == 3

        agent.tick()                                         # complete files are not fetched again
        assert state["downloads"] == 3

        state["blobs"]["f4"] = b"x"                          # free-space guard
        active["files"].append({"id": "f4", "name": "late.bin", "size": 1})
        free_bytes = lambda p: 0
        agent.tick()
        assert not os.path.exists(os.path.join(inn, "late.bin"))
        free_bytes = real_free
        agent.tick()
        assert os.path.getsize(os.path.join(inn, "late.bin")) == 1

        state["blobs"].update({"f5": b"short", "f6": b"ok"})  # size mismatch: nothing kept, later files still land
        active["files"] += [{"id": "f5", "name": "bad.bin", "size": 99}, {"id": "f6", "name": "after.bin", "size": 2}]
        agent.tick()
        assert not os.path.exists(os.path.join(inn, "bad.bin")) and not any(n.endswith(".part") for n in os.listdir(inn))
        assert os.path.getsize(os.path.join(inn, "after.bin")) == 2

        for junk in (None, [], {"active": [None, 7, {"id": "../x"}], "purge": []}):  # bad replies: logged, no crash
            state["jobs"] = junk
            agent.tick()
        assert sorted(os.listdir(root)) == ["bk1"]

        out = os.path.join(d, "out")
        os.makedirs(os.path.join(out, "logs"))
        os.makedirs(os.path.join(out, "empty-dir"))
        for rel, body in (("a-first.txt", b"first"), ("empty.txt", b""), ("results.txt", b"accuracy=0.93\n"),
                          ("logs/run.log", os.urandom(CHUNK + 1))):
            with open(os.path.join(out, rel), "wb") as f:
                f.write(body)
        state["jobs"] = {"active": [], "purge": ["bk1", "bk-nodir"]}
        state["fail_names"] = {"a-first.txt"}
        agent.tick()                                         # one upload keeps failing: the rest go, tree stays
        assert [u[1] for u in state["uploads"]] == ["empty.txt", "results.txt", "logs/run.log"]
        assert os.path.isdir(d) and state["purged"] == ["bk-nodir"]

        state["fail_names"] = set()
        agent.tick()                                         # now it goes through, nothing uploaded twice
        assert [u[1] for u in state["uploads"]] == ["empty.txt", "results.txt", "logs/run.log", "a-first.txt"]
        assert not os.path.exists(d)
        ups = {u[1]: u[2] for u in state["uploads"]}
        assert ups["empty.txt"] == b"" and ups["results.txt"] == b"accuracy=0.93\n" and len(ups["logs/run.log"]) == CHUNK + 1
        assert state["purged"] == ["bk-nodir", "bk1", "bk-nodir"]

        # a permanent refusal (booking already purged on the server) still wipes the renter's files
        d3 = os.path.join(root, "bk3", "out")
        os.makedirs(d3)
        with open(os.path.join(d3, "late.txt"), "wb") as f:
            f.write(b"too late")
        state["jobs"], state["refuse_names"] = {"active": [], "purge": ["bk3"]}, {"late.txt"}
        n_uploads = len(state["uploads"])
        agent.tick()
        assert not os.path.exists(os.path.join(root, "bk3")) and state["purged"][-1] == "bk3"
        assert len(state["uploads"]) == n_uploads
        state["refuse_names"] = set()

        # fail closed on config; the real CLI does one poll with --once
        _, p = config({"RAAS_API_URL": "http://example.com", "RAAS_DIR": root, "RAAS_POLL_S": "0"}, False)
        assert len(p) == 4, p
        assert config({"RAAS_API_URL": "https://", "ROBOT_TOKEN": "t", "RAAS_DIR": root}, True)[1]
        assert config({"RAAS_API_URL": api + "/", "ROBOT_TOKEN": "t0k", "RAAS_DIR": root}, True) == ((api, "t0k", root, 5.0), [])
        state["jobs"] = {"active": [{**active, "id": "bk2", "files": [active["files"][-1]]}], "purge": []}
        cli = [sys.executable, os.path.abspath(__file__), "--once"]
        env = {**os.environ, "RAAS_API_URL": api, "ROBOT_TOKEN": "t0k", "RAAS_DIR": root, "RAAS_POLL_S": "5"}
        assert subprocess.run(cli, env=env, stderr=subprocess.DEVNULL).returncode != 0   # not a mount point
        assert subprocess.run(cli + ["--allow-disk"], env={**env, "ROBOT_TOKEN": ""},
                              stderr=subprocess.DEVNULL).returncode != 0                  # no token
        assert not os.listdir(root)
        assert subprocess.run(cli + ["--allow-disk"], env=env, timeout=60).returncode == 0
        assert os.path.getsize(os.path.join(root, "bk2", "in", "after.bin")) == 2
        print("ROBOT-AGENT-OK")
    finally:
        free_bytes = real_free
        srv.shutdown()
        shutil.rmtree(root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true", help="one poll, then exit")
    ap.add_argument("--allow-disk", action="store_true", help="RAAS_DIR need not be a mount point (local tests)")
    ap.add_argument("--selftest", action="store_true", help="run against an in-process fake server")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    (api, token, root, poll), problems = config(os.environ, args.allow_disk)
    if problems:
        sys.exit("robot_agent refusing to start: " + "; ".join(problems))

    agent = Agent(api, token, root)
    log(f"start: api={api} dir={root} poll={poll}s")
    while True:
        agent.tick()
        if args.once:
            break
        time.sleep(poll)


if __name__ == "__main__":
    main()
