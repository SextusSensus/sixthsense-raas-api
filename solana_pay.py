"""USDC-on-Solana payment check for RAAS bookings.

The browser is never trusted: the server re-reads the finalized transaction from RPC and checks it.
"""
import json
import urllib.request

USDC_MAINNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"


def rpc(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        out = json.load(r)
    if "error" in out:
        raise RuntimeError(out["error"])
    return out["result"]


def fetch_tx(url, signature):
    return rpc(url, "getTransaction", [signature, {
        "encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}])


def verify_usdc_payment(tx, *, booking_id, treasury, mint, min_units, deadline):
    """Return (ok, reason). `tx` is a jsonParsed getTransaction result (or None).

    reason: ok | not_found | tx_failed | memo_mismatch | amount_short | late.
    `late` means the payment itself is valid but landed after the hold expired -> refund by hand.
    """
    if tx is None:
        return False, "not_found"
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return False, "tx_failed"
    memo = f"raas:{booking_id}"
    instructions = tx["transaction"]["message"]["instructions"]
    if not any(i.get("program") == "spl-memo" and i.get("parsed") == memo for i in instructions):
        return False, "memo_mismatch"

    def treasury_units(key):
        return sum(int(b["uiTokenAmount"]["amount"]) for b in meta.get(key) or []
                   if b.get("owner") == treasury and b.get("mint") == mint)

    # Balance delta, not instruction parsing: counts only what actually landed in the treasury,
    # in the right mint, whatever instruction mix the wallet used.
    if treasury_units("postTokenBalances") - treasury_units("preTokenBalances") < min_units:
        return False, "amount_short"
    block_time = tx.get("blockTime")
    if block_time is None or block_time > deadline:
        return False, "late"
    return True, "ok"
