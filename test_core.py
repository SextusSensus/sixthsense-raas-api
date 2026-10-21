"""Smallest checks that fail if the money, booking or purge logic breaks. Run: python test_core.py"""
import base64
import os
import secrets
import shutil
import tempfile
from pathlib import Path

import base58
from nacl.signing import SigningKey

TMP = Path(tempfile.mkdtemp(prefix="raas-test-"))
ADMIN, RENTER, OTHER, TREASURY = (SigningKey.generate() for _ in range(4))
pub = lambda k: base58.b58encode(bytes(k.verify_key)).decode()
os.environ.update(DATA_DIR=str(TMP), TREASURY_ADDRESS=pub(TREASURY), SESSION_SECRET=secrets.token_hex(32),
                  VAULT_MASTER_KEY=secrets.token_hex(32), ADMIN_WALLETS=pub(ADMIN), ADMIN_TOKEN="adm",
                  ROBOT_TOKEN="bot", SECURE_COOKIE="0", OPERATOR_TZ="UTC", PRICE_USDC_PER_HOUR="25",
                  RESULTS_TTL_HOURS="72", SESSION_TYPES="operator-run,remote-view")

import solana_pay  # noqa: E402
import server  # noqa: E402
import vault  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MINT = solana_pay.USDC_MAINNET


def fake_tx(memo, *, owner=pub(TREASURY), mint=MINT, pre=0, post=50_000_000, err=None, block_time=1):
    bal = lambda amt: [{"accountIndex": 2, "mint": mint, "owner": owner,
                        "uiTokenAmount": {"amount": str(amt), "decimals": 6}}]
    return {"blockTime": block_time,
            "meta": {"err": err, "preTokenBalances": bal(pre), "postTokenBalances": bal(post)},
            "transaction": {"message": {"instructions": [
                {"program": "spl-token", "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                 "parsed": {"type": "transferChecked", "info": {"mint": mint}}},
                {"program": "spl-memo", "programId": "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr", "parsed": memo}]}}}


def check_verifier():
    v = lambda tx, **kw: solana_pay.verify_usdc_payment(
        tx, **{"booking_id": "abc", "treasury": pub(TREASURY), "mint": MINT, "min_units": 50_000_000,
               "deadline": 100, **kw})
    assert v(fake_tx("raas:abc")) == (True, "ok")
    assert v(None) == (False, "not_found")
    assert v(fake_tx("raas:abc", err={"InstructionError": [0, "x"]})) == (False, "tx_failed")
    assert v(fake_tx("raas:xyz")) == (False, "memo_mismatch")
    assert v(fake_tx("raas:abc", mint="So11111111111111111111111111111111111111112")) == (False, "amount_short")
    assert v(fake_tx("raas:abc", owner=pub(OTHER))) == (False, "amount_short")
    assert v(fake_tx("raas:abc", pre=10, post=49_999_999)) == (False, "amount_short")
    assert v(fake_tx("raas:abc", block_time=101)) == (False, "late")


def login(c, key):
    c.cookies.clear()
    ch = c.post("/api/auth/challenge", json={"wallet": pub(key)}).json()
    sig = base64.b64encode(key.sign(ch["message"].encode()).signature).decode()
    r = c.post("/api/auth/verify", json={"wallet": pub(key), "nonce": ch["nonce"], "signature": sig})
    assert r.status_code == 200, r.text
    # replaying the same nonce must fail
    assert c.post("/api/auth/verify", json={"wallet": pub(key), "nonce": ch["nonce"], "signature": sig}).status_code == 401


def check_flow():
    adm, bot = {"Authorization": "Bearer adm"}, {"Authorization": "Bearer bot"}
    with TestClient(server.app) as c:
        assert c.get("/api/robot/jobs").status_code == 403
        assert c.get("/api/admin/bookings").status_code == 403
        assert c.put("/api/admin/availability", headers=adm,
                     json={"slots": [[d, h] for d in range(7) for h in range(24)]}).json()["count"] == 168

        login(c, RENTER)
        free = [s["start"] for s in c.get("/api/slots").json() if s["free"]]
        b = c.post("/api/bookings", json={"slot_start": free[0], "hours": 2, "session_type": "operator-run",
                                          "waiver": True}).json()
        assert b["status"] == "held" and b["price_units"] == 50_000_000 and b["memo"] == f"raas:{b['id']}"
        assert c.post("/api/bookings", json={"slot_start": free[0], "hours": 1, "session_type": "teleop",
                                             "waiver": True}).status_code == 400  # teleop not offered yet
        assert c.post("/api/bookings", json={"slot_start": free[0], "hours": 1, "session_type": "operator-run",
                                             "waiver": False}).status_code == 400

        sig1 = base58.b58encode(os.urandom(64)).decode()
        server.solana_pay.fetch_tx = lambda url, s: fake_tx(f"raas:{b['id']}", block_time=int(b["hold_expires"]))
        paid = c.post(f"/api/bookings/{b['id']}/pay", json={"signature": sig1}).json()
        assert paid["status"] == "paid", paid

        login(c, OTHER)
        # double booking: the second hour of the first booking is taken
        assert c.post("/api/bookings", json={"slot_start": free[1], "hours": 1, "session_type": "operator-run",
                                             "waiver": True}).status_code == 409
        assert c.get(f"/api/bookings/{b['id']}").status_code == 404  # not their booking
        b2 = c.post("/api/bookings", json={"slot_start": free[5], "hours": 1, "session_type": "remote-view",
                                           "waiver": True}).json()
        server.solana_pay.fetch_tx = lambda url, s: fake_tx(f"raas:{b2['id']}", post=25_000_000)
        assert c.post(f"/api/bookings/{b2['id']}/pay", json={"signature": sig1}).status_code == 409  # replay

        login(c, RENTER)
        blob = os.urandom(3 * 2**20 + 7)
        evil = c.post(f"/api/bookings/{b['id']}/upload-link", params={"name": "../evil model.onnx"}).json()["url"]
        up = c.put(evil, content=blob).json()
        assert up["size"] == len(blob) and "/" not in up["name"]

        # signed direct transfer: the link is the authorization; tampering or reuse past expiry fails
        link = c.post(f"/api/bookings/{b['id']}/upload-link", params={"name": "big.bin"}).json()["url"]
        assert c.put(link.replace("big.bin", "evil.bin", 1), content=b"x").status_code == 403
        direct = c.put(link, content=b"direct-bytes").json()
        dl = c.get(f"/api/bookings/{b['id']}/files/{direct['id']}/link").json()["url"]
        assert c.get(dl).content == b"direct-bytes"
        assert c.get(dl.replace("exp=", "exp=1")).status_code == 403

        assert c.get("/api/robot/jobs", headers=bot).json()["active"] == []
        assert c.post(f"/api/admin/bookings/{b['id']}/start", headers=adm).json()["status"] == "active"
        job = c.get("/api/robot/jobs", headers=bot).json()["active"][0]
        assert c.get(f"/api/robot/files/{job['files'][0]['id']}", headers=bot).content == blob

        result = b"accuracy=0.93\n"
        assert c.put(f"/api/robot/bookings/{b['id']}/results", params={"name": "results.txt"},
                     content=result, headers=bot).status_code == 200
        assert c.post(f"/api/admin/bookings/{b['id']}/complete", headers=adm).json()["status"] == "complete"
        assert c.get("/api/robot/jobs", headers=bot).json()["purge"] == [b["id"]]
        c.post(f"/api/robot/bookings/{b['id']}/purged", headers=bot)
        assert c.get("/api/robot/jobs", headers=bot).json()["purge"] == []

        mine = c.get(f"/api/bookings/{b['id']}").json()
        res = [f for f in mine["files"] if f["kind"] == "result"][0]
        assert c.get(c.get(f"/api/bookings/{b['id']}/files/{res['id']}/link").json()["url"]).content == result
        assert c.get("/api/admin/peaq-pending", headers=adm).json()[0]["usd_cents"] == 5000

        # tamper: a truncated blob must not decrypt
        path = vault.blob_path(TMP, b["id"], up["id"])
        with server.db() as con:
            key = vault.unwrap(server.VAULT_MASTER, server.booking_row(con, b["id"])["vault_key"])
        cut = path.with_suffix(".cut")
        cut.write_bytes(path.read_bytes()[:-10])
        try:
            b"".join(vault.read_stream(cut, key, up["id"]))
            raise AssertionError("truncated blob decrypted")
        except Exception as e:
            assert not isinstance(e, AssertionError)
        cut.unlink()

        # TTL purge = crypto-shred: blobs gone, key gone, downloads refused
        with server.db() as con:
            server.sweep(con, now=mine["completed_at"] + server.RESULTS_TTL + 1)
            row = server.booking_row(con, b["id"])
        assert row["status"] == "purged" and row["vault_key"] is None and not path.exists()
        assert c.get(f"/api/bookings/{b['id']}/files/{res['id']}/link").status_code == 404
        assert c.post(f"/api/bookings/{b['id']}/upload-link", params={"name": "x"}).status_code == 409


def claims_of(bid):
    with server.db() as con:
        return con.execute("SELECT COUNT(*) FROM slot_claims WHERE booking_id=?", (bid,)).fetchone()[0]


def check_regressions():
    """One check per fix from the backend review."""
    adm, bot = {"Authorization": "Bearer adm"}, {"Authorization": "Bearer bot"}
    sig = lambda: base58.b58encode(os.urandom(64)).decode()
    with TestClient(server.app) as c:
        login(c, RENTER)
        free = [s["start"] for s in c.get("/api/slots").json() if s["free"]]
        book = lambda start, hours=1: c.post("/api/bookings", json={
            "slot_start": start, "hours": hours, "session_type": "operator-run", "waiver": True}).json()
        on_time = lambda b: fake_tx(f"raas:{b['id']}", post=b["price_units"], block_time=int(b["hold_expires"]))

        # 1. cancel lands while /pay waits on the RPC: the payment still wins, with its slot and key
        b = book(free[10], hours=2)
        with server.db() as con:
            stale = server.booking_row(con, b["id"])
        assert c.post(f"/api/bookings/{b['id']}/cancel").status_code == 200
        with server.db() as con:
            assert server.apply_payment(con, stale, sig(), on_time(b)) == "paid"
            assert server.booking_row(con, b["id"])["vault_key"] is not None
        assert claims_of(b["id"]) == 2
        # ...and a cancel or the sweep can no longer clobber a paid booking
        assert c.post(f"/api/bookings/{b['id']}/cancel").status_code == 409
        with server.db() as con:
            assert not server.expire_hold(con, b["id"])

        # 2. two overlapping /pay calls on an expired-but-paid-on-time booking: no bogus needs_refund
        b2 = book(free[14])
        with server.db() as con:
            server.expire_hold(con, b2["id"])
            stale = server.booking_row(con, b2["id"])
            first, s2 = server.apply_payment(con, stale, sig(), on_time(b2)), sig()
            assert (first, server.apply_payment(con, stale, s2, on_time(b2))) == ("paid", "paid")
        assert claims_of(b2["id"]) == 1

        # 3. inputs and results have separate budgets; parallel-safe quota stays enforced
        real_max = server.MAX_UPLOAD
        server.MAX_UPLOAD = 1024
        try:
            link = lambda n: c.post(f"/api/bookings/{b2['id']}/upload-link", params={"name": n}).json()["url"]
            assert c.put(link("a.bin"), content=b"x" * 1024).status_code == 200
            assert c.put(link("b.bin"), content=b"y").status_code == 413
            assert c.post(f"/api/admin/bookings/{b2['id']}/start", headers=adm).status_code == 200
            assert c.put(f"/api/robot/bookings/{b2['id']}/results", params={"name": "r.txt"},
                         content=b"z" * 1000, headers=bot).status_code == 200
        finally:
            server.MAX_UPLOAD = real_max

        # 4. admin purge refuses live sessions and paid slots; works once complete
        assert c.post(f"/api/admin/bookings/{b2['id']}/purge", headers=adm).status_code == 409    # active
        assert c.post(f"/api/admin/bookings/{b['id']}/purge", headers=adm).status_code == 409     # paid
        assert c.post(f"/api/admin/bookings/{b2['id']}/complete", headers=adm).status_code == 200
        assert c.post(f"/api/admin/bookings/{b2['id']}/purge", headers=adm).json()["status"] == "purged"

        # 5. peaqOS: pending list survives refunds/purges, and the mark-posted route is reachable once
        assert c.post(f"/api/admin/bookings/{b['id']}/refunded", headers=adm).json()["status"] == "refunded"
        pending = c.get("/api/admin/peaq-pending", headers=adm)
        assert pending.status_code == 200 and b2["id"] in [p["id"] for p in pending.json()]
        assert c.post(f"/api/admin/bookings/{b2['id']}/peaq-posted", headers=adm, json={"ref": "tx1"}).status_code == 200
        assert c.post(f"/api/admin/bookings/{b2['id']}/peaq-posted", headers=adm, json={"ref": "tx2"}).status_code == 409
        assert b2["id"] not in [p["id"] for p in c.get("/api/admin/peaq-pending", headers=adm).json()]

        # 6. a payment the browser never reported is found on chain before the hold expires
        b5 = book(free[18])
        sig5 = sig()
        with server.db() as con:
            con.execute("UPDATE bookings SET hold_expires=? WHERE id=?", (b5["hold_expires"] - 900, b5["id"]))
        server._treasury_accounts.clear()
        server.solana_pay.token_accounts = lambda url, owner, mint: ["TreasuryUsdcAccount"]
        server.solana_pay.memo_payments = lambda url, acct: {b5["id"]: sig5}
        server.solana_pay.fetch_tx = lambda url, s: fake_tx(f"raas:{b5['id']}", post=b5["price_units"], block_time=1)
        with server.db() as con:
            server.rescue_unreported(con)
            row = server.booking_row(con, b5["id"])
        assert row["status"] == "paid" and row["pay_sig"] == sig5

        # 7. oversized JSON bodies are refused before any handler buffers them
        assert c.post("/api/auth/challenge", content=b"{" + b" " * 70_000 + b"}",
                      headers={"Content-Type": "application/json"}).status_code == 413

    # 8. fail closed on price and key parsing
    assert server._price_units("25") == 25_000_000 and server._price_units("1.5") == 1_500_000
    assert server._price_units("0.0000001") == 0 and server._price_units("abc") == 0
    assert server._hex_key("zz") == b""
    real_price = server.PRICE_UNITS
    server.PRICE_UNITS = 0
    try:
        server.check_config()
        raise AssertionError("booted with a zero price")
    except SystemExit:
        pass
    finally:
        server.PRICE_UNITS = real_price
    assert solana_pay.MEMO_ID.findall("[17] raas:abcDEF12_-x; [5] other") == ["abcDEF12_-x"]


if __name__ == "__main__":
    try:
        check_verifier()
        check_flow()
        check_regressions()
        print("RAAS-CORE-OK")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
