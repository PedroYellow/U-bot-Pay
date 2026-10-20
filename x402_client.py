"""
x402 client — U-bot paying another agent.
  1. GET resource → 402 with requirements
  2. sign EIP-3009 TransferWithAuthorization offline (no gas, no tx)
  3. retry with X-PAYMENT header → service settles and answers
"""
import os, json, base64, time, httpx
import chain

# Addresses we are willing to sign an EIP-3009 authorization for.
#
# Audit 20 Oct round 7: pay_and_fetch signed whatever `payTo` the remote 402
# asked for. The per-call budget capped the damage at UBOT_AGENT_MAX_USDC, but
# the client was still willing to authorise a transfer to any address a service
# named. Defence in depth, mirroring the M5 bridge allowlist: if the expected
# payee is configured, refuse to sign for anything else.
#
# Empty = no allowlist, only the per-call budget applies. That is the demo
# default so a fresh checkout still works; set it before real funds are involved.
_ALLOWED_PAYEES = {
    a.lower().strip()
    for a in os.environ.get("UBOT_X402_PAYEES", "").split(",")
    if a.strip()
}


def _payee_allowed(pay_to: str) -> bool:
    if not _ALLOWED_PAYEES:
        return True                      # no allowlist configured
    return (pay_to or "").lower().strip() in _ALLOWED_PAYEES


async def pay_and_fetch(url: str, payer_key: str, max_usdc: float = 5.0) -> dict:
    t0 = time.time()
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.get(url)
        if r.status_code != 402:
            return {"ok": r.status_code == 200, "paid": False, "status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]}
        req = r.json()
        acc = req["accepts"][0]
        amount = float(acc.get("amount_usdc") or int(acc["maxAmountRequired"]) / 10 ** chain.decimals())
        if amount > max_usdc:
            return {"ok": False, "paid": False, "error": f"price {amount} USDC exceeds agent budget {max_usdc}"}
        # Never sign for a payee the operator has not allowlisted.
        if not _payee_allowed(acc.get("payTo")):
            return {"ok": False, "paid": False,
                    "error": f"refusing to pay unapproved payee {acc.get('payTo')}"}
        # The authorization is only good for a short window; do not let a remote
        # ask for a signature that stays valid for hours.
        valid_for = min(int(acc.get("maxTimeoutSeconds", 60)) + 540, 1800)
        auth = chain.sign_authorization(payer_key, acc["payTo"], amount, valid_seconds=valid_for)
        x_payment = base64.b64encode(json.dumps({"x402Version": 1, "scheme": acc["scheme"], "network": acc["network"],
                                                 "payload": {"authorization": auth}}).encode()).decode()
        r2 = await c.get(url, headers={"X-PAYMENT": x_payment})
        body = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {"raw": r2.text[:300]}
        return {"ok": r2.status_code == 200, "paid": r2.status_code == 200, "status": r2.status_code, "price_usdc": amount,
                "payee": acc["payTo"], "service": acc.get("description"), "latency_s": round(time.time() - t0, 2), "body": body}
