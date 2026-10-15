"""RAAS: hourly Booster K1 rentals for Sixth Sense Labs.

Phantom sign-in + USDC-on-Solana checkout, per-booking encrypted file vault, admin console API,
and the robot job API used by robot_agent.py. One process, SQLite + local disk.
ponytail: single instance only (SQLite + local vault); move to Postgres + object storage if it ever
needs more than one server.
"""
import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import base58
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from pydantic import BaseModel

import solana_pay
import vault

env = os.environ.get
DATA_DIR = Path(env("DATA_DIR", "./data"))
TREASURY = env("TREASURY_ADDRESS", "")
USDC_MINT = env("USDC_MINT", solana_pay.USDC_MAINNET)
PRICE_UNITS = int(Decimal(env("PRICE_USDC_PER_HOUR", "25")) * 10**6)
RPC_URL = env("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
ADMIN_WALLETS = {w.strip() for w in env("ADMIN_WALLETS", "").split(",") if w.strip()}
ADMIN_TOKEN = env("ADMIN_TOKEN", "")
ROBOT_TOKEN = env("ROBOT_TOKEN", "")
SESSION_SECRET = env("SESSION_SECRET", "").encode()
VAULT_MASTER = bytes.fromhex(env("VAULT_MASTER_KEY", ""))
MAX_UPLOAD = int(env("MAX_UPLOAD_MB", "2048")) * 2**20
RESULTS_TTL = int(env("RESULTS_TTL_HOURS", "72")) * 3600
OPERATOR_TZ = ZoneInfo(env("OPERATOR_TZ", "America/New_York"))
SESSION_TYPES = [t for t in env("SESSION_TYPES", "operator-run,remote-view").split(",") if t]
SITE_DOMAIN = env("SITE_DOMAIN", "localhost")
SITE_ORIGIN = env("SITE_ORIGIN", "")          # the public booking site; proxies /api/* to this server
PUBLIC_API_URL = env("PUBLIC_API_URL", "").rstrip("/")  # this server's own public URL, for direct transfers
SECURE_COOKIE = env("SECURE_COOKIE", "1") == "1"
LINK_TTL = 600      # signed direct-transfer links live 10 minutes
MACHINE_ID = "72070072199752035588865179268289351973289970295321663729669764070389137395940"

HOLD_S = 600        # unpaid slot hold
GRACE_S = 900       # extra time for a finalized payment to be reported before the hold is released
BOOK_DAYS = 14
MAX_HOURS = 8
LEAD_S = 3600       # no booking a slot that starts within the hour
ALL_TYPES = {"operator-run", "remote-view", "teleop"}


def _b58_32(s):
    try:
        return len(base58.b58decode(s)) == 32
    except ValueError:
        return False


def check_config():
    # Fail closed: refuse to boot without the values that keep money and data safe.
    problems = []
    if not _b58_32(TREASURY):
        problems.append("TREASURY_ADDRESS (Solana public key)")
    if len(SESSION_SECRET) < 32:
        problems.append("SESSION_SECRET (>=32 chars)")
    if len(VAULT_MASTER) != 32:
        problems.append("VAULT_MASTER_KEY (64 hex chars)")
    if not set(SESSION_TYPES) <= ALL_TYPES:
        problems.append(f"SESSION_TYPES must be a subset of {sorted(ALL_TYPES)}")
    if problems:
        raise SystemExit("RAAS refusing to start, missing/invalid: " + ", ".join(problems))


SCHEMA = """
CREATE TABLE IF NOT EXISTS nonces(nonce TEXT PRIMARY KEY, wallet TEXT NOT NULL, message TEXT NOT NULL,
  expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS availability(weekday INTEGER NOT NULL, hour INTEGER NOT NULL,
  PRIMARY KEY(weekday, hour));
CREATE TABLE IF NOT EXISTS bookings(
  id TEXT PRIMARY KEY, wallet TEXT NOT NULL, slot_start INTEGER NOT NULL, hours INTEGER NOT NULL,
  session_type TEXT NOT NULL, price_units INTEGER NOT NULL, status TEXT NOT NULL,
  brief TEXT NOT NULL DEFAULT '', contact TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, hold_expires REAL NOT NULL, waiver_at REAL NOT NULL,
  pay_sig TEXT UNIQUE, paid_at REAL, started_at REAL, completed_at REAL,
  robot_purged_at REAL, purged_at REAL, vault_key BLOB, peaq_posted_at REAL, peaq_ref TEXT);
CREATE TABLE IF NOT EXISTS slot_claims(slot_start INTEGER PRIMARY KEY, booking_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, booking_id TEXT NOT NULL, kind TEXT NOT NULL,
  name TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL);
"""


@contextmanager
def db():
    con = sqlite3.connect(DATA_DIR / "raas.db", isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA secure_delete=ON")  # deleted keys are overwritten, not left in free pages
    try:
        yield con
    finally:
        con.close()


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(SCHEMA)


# --- sessions ---------------------------------------------------------------------------------

def _mac(body):
    return hmac.new(SESSION_SECRET, body.encode(), hashlib.sha256).hexdigest()


def make_session(wallet):
    body = f"{wallet}.{int(time.time()) + 7 * 86400}"
    return f"{body}.{_mac(body)}"


def read_session(token):
    try:
        wallet, exp, mac = token.rsplit(".", 2)
    except (AttributeError, ValueError):
        return None
    if not hmac.compare_digest(mac, _mac(f"{wallet}.{exp}")) or int(exp) < time.time():
        return None
    return wallet


def _bearer(request):
    h = request.headers.get("authorization", "")
    return h[7:] if h.lower().startswith("bearer ") else ""


def renter(request: Request):
    wallet = read_session(request.cookies.get("raas_session"))
    if not wallet:
        raise HTTPException(401, "sign in with Phantom first")
    return wallet


def admin(request: Request):
    tok = _bearer(request)
    if ADMIN_TOKEN and tok and hmac.compare_digest(tok, ADMIN_TOKEN):
        return "token"
    wallet = read_session(request.cookies.get("raas_session"))
    if wallet and wallet in ADMIN_WALLETS:
        return wallet
    raise HTTPException(403, "admin only")


def robot(request: Request):
    tok = _bearer(request)
    if not (ROBOT_TOKEN and tok and hmac.compare_digest(tok, ROBOT_TOKEN)):
        raise HTTPException(403, "robot only")


# --- booking helpers --------------------------------------------------------------------------

def booking_row(con, bid):
    b = con.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    if not b:
        raise HTTPException(404, "no such booking")
    return b


def owned(con, bid, wallet):
    b = booking_row(con, bid)
    if b["wallet"] != wallet:
        raise HTTPException(404, "no such booking")
    return b


def booking_out(con, b):
    d = {k: b[k] for k in b.keys() if k != "vault_key"}
    d["memo"] = f"raas:{b['id']}"
    d["amount_usdc"] = str(Decimal(b["price_units"]) / 10**6)
    d["results_expire"] = b["completed_at"] + RESULTS_TTL if b["completed_at"] else None
    d["files"] = [dict(f) for f in con.execute(
        "SELECT id, kind, name, size, created FROM files WHERE booking_id=? ORDER BY created", (b["id"],))]
    return d


def release_slots(con, bid):
    con.execute("DELETE FROM slot_claims WHERE booking_id=?", (bid,))


def purge(con, bid, status="purged"):
    vault.delete_booking(DATA_DIR, bid)
    con.execute("DELETE FROM files WHERE booking_id=?", (bid,))
    con.execute("UPDATE bookings SET status=?, vault_key=NULL, purged_at=? WHERE id=?",
                (status, time.time(), bid))


def sweep(con, now=None):
    now = now or time.time()
    for (bid,) in con.execute("SELECT id FROM bookings WHERE status='held' AND hold_expires+?<?",
                              (GRACE_S, now)).fetchall():
        release_slots(con, bid)
        purge(con, bid, "expired")
    for (bid,) in con.execute("SELECT id FROM bookings WHERE status='complete' AND completed_at+?<?",
                              (RESULTS_TTL, now)).fetchall():
        purge(con, bid)
    con.execute("DELETE FROM nonces WHERE expires<?", (now,))


def open_slots(con, now=None):
    """Concrete hourly slot starts (unix, UTC) for the next BOOK_DAYS from the operator's weekly grid."""
    now = now or time.time()
    avail = {(r[0], r[1]) for r in con.execute("SELECT weekday, hour FROM availability")}
    claimed = {r[0] for r in con.execute("SELECT slot_start FROM slot_claims")}
    today = datetime.now(OPERATOR_TZ).date()
    out, seen = [], set()
    for d in range(BOOK_DAYS):
        day = today + timedelta(days=d)
        for h in range(24):
            if (day.weekday(), h) in avail:
                ts = int(datetime(day.year, day.month, day.day, h, tzinfo=OPERATOR_TZ).timestamp())
                if ts > now + LEAD_S and ts not in seen:  # seen: a DST-gap hour maps onto the next hour
                    seen.add(ts)
                    out.append({"start": ts, "free": ts not in claimed})
    return out


def create_booking(con, wallet, slot_start, hours, session_type, brief, contact, now=None):
    now = now or time.time()
    if session_type not in SESSION_TYPES:
        raise HTTPException(400, "session type not offered")
    if not 1 <= hours <= MAX_HOURS:
        raise HTTPException(400, f"book 1-{MAX_HOURS} hours")
    offered = {s["start"] for s in open_slots(con, now)}
    wanted = [slot_start + 3600 * i for i in range(hours)]
    if not all(t in offered for t in wanted):
        raise HTTPException(400, "slot not available")
    bid = secrets.token_urlsafe(9)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT INTO bookings(id, wallet, slot_start, hours, session_type, price_units, status, brief,"
            " contact, created, hold_expires, waiver_at, vault_key) VALUES(?,?,?,?,?,?,'held',?,?,?,?,?,?)",
            (bid, wallet, slot_start, hours, session_type, PRICE_UNITS * hours, brief[:2000], contact[:200],
             now, now + HOLD_S, now, vault.wrap(VAULT_MASTER, vault.new_key())))
        con.executemany("INSERT INTO slot_claims(slot_start, booking_id) VALUES(?,?)", [(t, bid) for t in wanted])
        con.execute("COMMIT")
    except sqlite3.IntegrityError:
        con.execute("ROLLBACK")
        raise HTTPException(409, "slot just got taken, pick another")
    return bid


def apply_payment(con, b, sig, tx):
    """Verify tx for booking b and move it to paid (or needs_refund). Returns new status or raises."""
    ok, reason = solana_pay.verify_usdc_payment(
        tx, booking_id=b["id"], treasury=TREASURY, mint=USDC_MINT,
        min_units=b["price_units"], deadline=b["hold_expires"])
    if reason == "not_found":
        return "pending"
    if not ok and reason != "late":
        raise HTTPException(400, f"payment rejected: {reason}")
    now = time.time()
    con.execute("BEGIN IMMEDIATE")
    try:
        status = "paid"
        if reason == "late":
            status = "needs_refund"
        elif b["status"] == "expired":  # paid in time but reported after the hold was released
            try:
                con.executemany("INSERT INTO slot_claims(slot_start, booking_id) VALUES(?,?)",
                                [(b["slot_start"] + 3600 * i, b["id"]) for i in range(b["hours"])])
            except sqlite3.IntegrityError:
                status = "needs_refund"
        con.execute("UPDATE bookings SET status=?, pay_sig=?, paid_at=? WHERE id=?", (status, sig, now, b["id"]))
        if status == "needs_refund":
            release_slots(con, b["id"])
        if status == "paid" and b["vault_key"] is None:  # expired bookings had their key dropped
            con.execute("UPDATE bookings SET vault_key=? WHERE id=?",
                        (vault.wrap(VAULT_MASTER, vault.new_key()), b["id"]))
        con.execute("COMMIT")
    except sqlite3.IntegrityError:
        con.execute("ROLLBACK")
        raise HTTPException(409, "that transaction already paid for another booking")
    return status


def _safe_name(name):
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", (name or "file").strip())[:120].strip(". ")
    return name or "file"


async def store_file(con, b, kind, name, request):
    # ponytail: encrypts on the event-loop thread; fine at one robot's traffic, offload if uploads pile up.
    if b["vault_key"] is None:
        raise HTTPException(409, "booking data already purged")
    used = con.execute("SELECT COALESCE(SUM(size),0) FROM files WHERE booking_id=?", (b["id"],)).fetchone()[0]
    fid = secrets.token_urlsafe(9)
    try:
        size = await vault.write_stream(vault.blob_path(DATA_DIR, b["id"], fid),
                                        vault.unwrap(VAULT_MASTER, b["vault_key"]), fid,
                                        request.stream(), MAX_UPLOAD - used)
    except vault.TooLarge:
        raise HTTPException(413, f"booking upload limit is {MAX_UPLOAD // 2**20} MB")
    con.execute("INSERT INTO files(id, booking_id, kind, name, size, created) VALUES(?,?,?,?,?,?)",
                (fid, b["id"], kind, _safe_name(name), size, time.time()))
    return {"id": fid, "name": _safe_name(name), "size": size, "kind": kind}


def send_file(con, f):
    b = booking_row(con, f["booking_id"])
    if b["vault_key"] is None:
        raise HTTPException(410, "purged")
    key = vault.unwrap(VAULT_MASTER, b["vault_key"])
    return StreamingResponse(
        vault.read_stream(vault.blob_path(DATA_DIR, b["id"], f["id"]), key, f["id"]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{f["name"]}"', "Content-Length": str(f["size"])})


def file_row(con, fid):
    f = con.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    if not f:
        raise HTTPException(404, "no such file")
    return f


# --- app ---------------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    check_config()
    init_db()

    def sweep_once():
        with db() as con:
            sweep(con)

    async def janitor():
        while True:
            try:
                await asyncio.to_thread(sweep_once)
            except Exception as e:  # keep sweeping; a bad tick must not stop purges
                print("sweep error:", e)
            await asyncio.sleep(60)

    task = asyncio.create_task(janitor())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def headers(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/direct/") and SITE_ORIGIN:
        # Signed-link transfers are the only cross-origin calls; errors need CORS too or the
        # browser hides why an upload failed.
        resp.headers["Access-Control-Allow-Origin"] = SITE_ORIGIN
        resp.headers["Access-Control-Allow-Methods"] = "PUT, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Max-Age"] = "600"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://esm.sh; connect-src 'self' https://esm.sh; "
        "img-src 'self' data:; media-src 'self'; style-src 'self'; font-src 'self'; frame-ancestors 'none'")
    return resp


class Wallet(BaseModel):
    wallet: str


class Verify(BaseModel):
    wallet: str
    nonce: str
    signature: str  # base64


class NewBooking(BaseModel):
    slot_start: int
    hours: int = 1
    session_type: str
    brief: str = ""
    contact: str = ""
    waiver: bool


class Pay(BaseModel):
    signature: str  # base58 tx signature


class Availability(BaseModel):
    slots: list[tuple[int, int]]  # (weekday 0=Mon, hour 0-23) in OPERATOR_TZ


class PeaqPosted(BaseModel):
    ref: str = ""


@app.get("/api/config")
def config():
    return {"price_usdc": str(Decimal(PRICE_UNITS) / 10**6), "treasury": TREASURY, "mint": USDC_MINT,
            "cluster": "devnet" if USDC_MINT == solana_pay.USDC_DEVNET else "mainnet-beta",
            "session_types": SESSION_TYPES, "operator_tz": str(OPERATOR_TZ), "max_hours": MAX_HOURS,
            "max_upload_mb": MAX_UPLOAD // 2**20, "results_ttl_hours": RESULTS_TTL // 3600,
            "hold_minutes": HOLD_S // 60, "machine_id": MACHINE_ID}


@app.get("/api/slots")
def slots():
    with db() as con:
        sweep(con)
        return open_slots(con)


@app.post("/api/auth/challenge")
def challenge(body: Wallet):
    if not _b58_32(body.wallet):
        raise HTTPException(400, "bad wallet address")
    nonce, now = secrets.token_hex(16), datetime.now(timezone.utc)
    message = (f"{SITE_DOMAIN} wants you to sign in with your Solana account:\n{body.wallet}\n\n"
               f"Sign in to Sixth Sense Labs RAAS. This does not move funds.\n\n"
               f"Nonce: {nonce}\nIssued At: {now.isoformat(timespec='seconds')}\n"
               f"Expiration Time: {(now + timedelta(minutes=10)).isoformat(timespec='seconds')}")
    with db() as con:
        con.execute("INSERT INTO nonces VALUES(?,?,?,?)", (nonce, body.wallet, message, time.time() + 600))
    return {"nonce": nonce, "message": message}


@app.post("/api/auth/verify")
def verify(body: Verify, response: Response):
    with db() as con:
        row = con.execute("SELECT * FROM nonces WHERE nonce=? AND wallet=? AND expires>?",
                          (body.nonce, body.wallet, time.time())).fetchone()
        if not row or con.execute("DELETE FROM nonces WHERE nonce=?", (body.nonce,)).rowcount != 1:
            raise HTTPException(401, "challenge expired, try again")
    try:
        VerifyKey(base58.b58decode(body.wallet)).verify(row["message"].encode(), base64.b64decode(body.signature))
    except (BadSignatureError, ValueError):
        raise HTTPException(401, "signature does not match wallet")
    response.set_cookie("raas_session", make_session(body.wallet), max_age=7 * 86400, httponly=True,
                        secure=SECURE_COOKIE, samesite="lax")
    return {"wallet": body.wallet, "admin": body.wallet in ADMIN_WALLETS}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie("raas_session")
    return {}


@app.get("/api/me")
def me(request: Request):
    wallet = read_session(request.cookies.get("raas_session"))
    return {"wallet": wallet, "admin": bool(wallet and wallet in ADMIN_WALLETS)}


@app.get("/api/blockhash")
def blockhash(wallet=Depends(renter)):
    return solana_pay.rpc(RPC_URL, "getLatestBlockhash", [{"commitment": "finalized"}])["value"]


@app.post("/api/bookings")
def new_booking(body: NewBooking, wallet=Depends(renter)):
    if not body.waiver:
        raise HTTPException(400, "accept the rental terms first")
    with db() as con:
        sweep(con)
        bid = create_booking(con, wallet, body.slot_start, body.hours, body.session_type, body.brief, body.contact)
        return booking_out(con, booking_row(con, bid))


@app.get("/api/bookings")
def my_bookings(wallet=Depends(renter)):
    with db() as con:
        sweep(con)
        rows = con.execute("SELECT * FROM bookings WHERE wallet=? AND status!='expired' ORDER BY slot_start DESC",
                           (wallet,)).fetchall()
        return [booking_out(con, b) for b in rows]


@app.get("/api/bookings/{bid}")
def my_booking(bid: str, wallet=Depends(renter)):
    with db() as con:
        return booking_out(con, owned(con, bid, wallet))


@app.post("/api/bookings/{bid}/cancel")
def cancel(bid: str, wallet=Depends(renter)):
    with db() as con:
        b = owned(con, bid, wallet)
        if b["status"] != "held":
            raise HTTPException(409, "only unpaid holds can be cancelled here; contact us for refunds")
        release_slots(con, bid)
        purge(con, bid, "expired")
        return {}


@app.post("/api/bookings/{bid}/pay")
def pay(bid: str, body: Pay, wallet=Depends(renter)):
    try:
        if len(base58.b58decode(body.signature)) != 64:
            raise ValueError
    except ValueError:
        raise HTTPException(400, "bad transaction signature")
    with db() as con:
        b = owned(con, bid, wallet)
        if b["status"] not in ("held", "expired"):
            return booking_out(con, b)
        try:
            tx = solana_pay.fetch_tx(RPC_URL, body.signature)
        except Exception:
            raise HTTPException(502, "Solana RPC unavailable, retrying")
        status = apply_payment(con, b, body.signature, tx)
        if status == "pending":
            return JSONResponse({"status": "pending"}, status_code=202)
        return booking_out(con, booking_row(con, bid))


@app.put("/api/bookings/{bid}/files")
async def upload(bid: str, name: str, request: Request, wallet=Depends(renter)):
    with db() as con:
        b = owned(con, bid, wallet)
        if b["status"] not in ("paid", "active"):
            raise HTTPException(409, "upload opens after payment and closes when the session ends")
        return await store_file(con, b, "input", name, request)


@app.get("/api/bookings/{bid}/files/{fid}")
def download(bid: str, fid: str, wallet=Depends(renter)):
    with db() as con:
        owned(con, bid, wallet)
        f = file_row(con, fid)
        if f["booking_id"] != bid:
            raise HTTPException(404, "no such file")
        return send_file(con, f)


# --- signed direct transfers ---------------------------------------------------------------------
# The booking site proxies /api/* through a Cloudflare Worker, which caps request bodies at 100 MB.
# Big uploads and downloads go straight to this server instead, on short-lived HMAC-signed links
# issued to the signed-in renter. The link itself is the authorization, so no cookie crosses origins.

def _sign(*parts):
    return _mac(":".join(map(str, parts)))


def _check_link(sig, *parts):
    exp = int(parts[-1])
    if exp < time.time() or not hmac.compare_digest(sig, _sign(*parts)):
        raise HTTPException(403, "link expired, request a new one")


@app.post("/api/bookings/{bid}/upload-link")
def upload_link(bid: str, name: str, wallet=Depends(renter)):
    with db() as con:
        b = owned(con, bid, wallet)
        if b["status"] not in ("paid", "active"):
            raise HTTPException(409, "upload opens after payment and closes when the session ends")
    exp = int(time.time()) + LINK_TTL
    query = urlencode({"name": name, "exp": exp, "sig": _sign("up", bid, name, exp)})
    return {"url": f"{PUBLIC_API_URL}/direct/upload/{bid}?{query}"}


@app.get("/api/bookings/{bid}/files/{fid}/link")
def download_link(bid: str, fid: str, wallet=Depends(renter)):
    with db() as con:
        owned(con, bid, wallet)
        if file_row(con, fid)["booking_id"] != bid:
            raise HTTPException(404, "no such file")
    exp = int(time.time()) + LINK_TTL
    return {"url": f"{PUBLIC_API_URL}/direct/download/{fid}?exp={exp}&sig={_sign('down', fid, exp)}"}


@app.options("/direct/upload/{bid}")
def direct_upload_preflight(bid: str):
    return Response(status_code=204)  # CORS headers come from the middleware


@app.put("/direct/upload/{bid}")
async def direct_upload(bid: str, name: str, exp: int, sig: str, request: Request):
    _check_link(sig, "up", bid, name, exp)
    with db() as con:
        b = booking_row(con, bid)
        if b["status"] not in ("paid", "active"):
            raise HTTPException(409, "upload opens after payment and closes when the session ends")
        return await store_file(con, b, "input", name, request)


@app.get("/direct/download/{fid}")
def direct_download(fid: str, exp: int, sig: str):
    _check_link(sig, "down", fid, exp)
    with db() as con:
        return send_file(con, file_row(con, fid))


# --- admin -------------------------------------------------------------------------------------

@app.get("/api/admin/bookings")
def admin_bookings(who=Depends(admin)):
    with db() as con:
        sweep(con)
        return [booking_out(con, b) for b in con.execute("SELECT * FROM bookings ORDER BY slot_start DESC LIMIT 500")]


@app.get("/api/admin/availability")
def get_availability(who=Depends(admin)):
    with db() as con:
        return [list(r) for r in con.execute("SELECT weekday, hour FROM availability")]


@app.put("/api/admin/availability")
def put_availability(body: Availability, who=Depends(admin)):
    slots = {(d, h) for d, h in body.slots if 0 <= d <= 6 and 0 <= h <= 23}
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM availability")
        con.executemany("INSERT INTO availability VALUES(?,?)", sorted(slots))
        con.execute("COMMIT")
    return {"count": len(slots)}


TRANSITIONS = {  # action: (allowed from, to, timestamp column)
    "start": (("paid",), "active", "started_at"),
    "complete": (("active",), "complete", "completed_at"),
}


@app.post("/api/admin/bookings/{bid}/{action}")
def admin_action(bid: str, action: str, who=Depends(admin)):
    with db() as con:
        b = booking_row(con, bid)
        if action in TRANSITIONS:
            allowed, to, col = TRANSITIONS[action]
            if b["status"] not in allowed:
                raise HTTPException(409, f"cannot {action} a {b['status']} booking")
            con.execute(f"UPDATE bookings SET status=?, {col}=? WHERE id=?", (to, time.time(), bid))
        elif action == "purge":
            if b["status"] == "held":
                raise HTTPException(409, "unpaid hold; let it expire")
            purge(con, bid, b["status"] if b["status"] in ("needs_refund", "refunded") else "purged")
        elif action == "refunded":
            if b["status"] not in ("paid", "needs_refund", "expired"):
                raise HTTPException(409, f"cannot refund a {b['status']} booking")
            release_slots(con, bid)
            purge(con, bid, "refunded")
        else:
            raise HTTPException(404, "unknown action")
        return booking_out(con, booking_row(con, bid))


@app.put("/api/admin/bookings/{bid}/results")
async def admin_results(bid: str, name: str, request: Request, who=Depends(admin)):
    with db() as con:
        b = booking_row(con, bid)
        if b["status"] not in ("active", "complete"):
            raise HTTPException(409, "results attach to active or complete sessions")
        return await store_file(con, b, "result", name, request)


@app.get("/api/admin/files/{fid}")
def admin_download(fid: str, who=Depends(admin)):
    with db() as con:
        return send_file(con, file_row(con, fid))


@app.get("/api/admin/peaq-pending")
def peaq_pending(who=Depends(admin)):
    with db() as con:
        rows = con.execute("SELECT * FROM bookings WHERE pay_sig IS NOT NULL AND status IN ('complete','purged')"
                           " AND peaq_posted_at IS NULL ORDER BY completed_at").fetchall()
        # USDC has 6 decimals; peaqOS revenue is USD minor units (cents).
        return [{"id": b["id"], "usd_cents": b["price_units"] // 10**4, "ts": int(b["completed_at"]),
                 "pay_sig": b["pay_sig"], "machine_id": MACHINE_ID} for b in rows]


@app.post("/api/admin/bookings/{bid}/peaq-posted")
def peaq_posted(bid: str, body: PeaqPosted, who=Depends(admin)):
    with db() as con:
        booking_row(con, bid)
        con.execute("UPDATE bookings SET peaq_posted_at=?, peaq_ref=? WHERE id=?", (time.time(), body.ref[:200], bid))
        return {}


# --- robot -------------------------------------------------------------------------------------

@app.get("/api/robot/jobs")
def robot_jobs(_=Depends(robot)):
    with db() as con:
        active = con.execute("SELECT * FROM bookings WHERE status='active'").fetchall()
        done = con.execute("SELECT id FROM bookings WHERE started_at IS NOT NULL AND robot_purged_at IS NULL"
                           " AND status!='active'").fetchall()
        return {"active": [{"id": b["id"], "session_type": b["session_type"], "slot_start": b["slot_start"],
                            "hours": b["hours"], "brief": b["brief"],
                            "files": [dict(f) for f in con.execute(
                                "SELECT id, name, size FROM files WHERE booking_id=? AND kind='input'", (b["id"],))]}
                           for b in active],
                "purge": [r["id"] for r in done]}


@app.get("/api/robot/files/{fid}")
def robot_file(fid: str, _=Depends(robot)):
    with db() as con:
        f = file_row(con, fid)
        if f["kind"] != "input" or booking_row(con, f["booking_id"])["status"] != "active":
            raise HTTPException(403, "only input files of an active session")
        return send_file(con, f)


@app.put("/api/robot/bookings/{bid}/results")
async def robot_results(bid: str, name: str, request: Request, _=Depends(robot)):
    with db() as con:
        b = booking_row(con, bid)
        if b["status"] not in ("active", "complete"):
            raise HTTPException(409, "session not running")
        return await store_file(con, b, "result", name, request)


@app.post("/api/robot/bookings/{bid}/purged")
def robot_purged(bid: str, _=Depends(robot)):
    with db() as con:
        booking_row(con, bid)
        con.execute("UPDATE bookings SET robot_purged_at=? WHERE id=?", (time.time(), bid))
        return {}


@app.get("/")
def home():
    # Renters use the booking site; this server only hosts the API and the operator console.
    return RedirectResponse(SITE_ORIGIN or "/admin.html")


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
