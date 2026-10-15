"""
x402 client — U-bot paying another agent.
  1. GET resource → 402 with requirements
  2. sign EIP-3009 TransferWithAuthorization offline (no gas, no tx)
  3. retry with X-PAYMENT header → service settles and answers
"""
import os, json, base64, time, httpx
import chain


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
        auth = chain.sign_authorization(payer_key, acc["payTo"], amount, valid_seconds=acc.get("maxTimeoutSeconds", 60) + 540)
        x_payment = base64.b64encode(json.dumps({"x402Version": 1, "scheme": acc["scheme"], "network": acc["network"],
                                                 "payload": {"authorization": auth}}).encode()).decode()
        r2 = await c.get(url, headers={"X-PAYMENT": x_payment})
        body = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {"raw": r2.text[:300]}
        return {"ok": r2.status_code == 200, "paid": r2.status_code == 200, "status": r2.status_code, "price_usdc": amount,
                "payee": acc["payTo"], "service": acc.get("description"), "latency_s": round(time.time() - t0, 2), "body": body}
