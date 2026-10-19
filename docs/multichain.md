# Ubotpay Multichain Roadmap

Goal: a user with USDC on any EVM chain can pay a recipient on any other EVM chain. The system bridges the USDC atomically.

## Status today (October 2026)

- Avalanche C-Chain (43114) and Fuji (43113) are enabled and working end-to-end
- 5 more chains registered but `enabled=False`: Base (8453), Polygon (137), Arbitrum (42161), Ethereum (1), Optimism (10)
- Frontend already asks the wallet to `wallet_switchEthereumChain` if the user is on the wrong chain
- Backend can read any USDC balance on any EVM chain via the `chain.py` registry

## Phase 1 — Validate and enable more chains (1-2h)

1. Confirm USDC contract addresses against Circle's official docs:
   - Base: `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
   - Polygon: `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`
   - Arbitrum: `0xaf88d065e77c8cC2239327C5EDb3A432268e5831`
   - Ethereum: `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`
   - Optimism: `0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85`
2. Flip `enabled=False` to `enabled=True` in `chain.py`
3. Test: `GET /balance/{address}?chain_id=137` returns a real number
4. Test: a USDC transfer between two addresses on the same chain works
5. Verify cross-chain message: the user sees the correct network in the popover

## Phase 2 — Chain selector in the connect modal (2-3h)

1. Add a "Source chain" picker to the wallet modal
2. User picks which chain they want to pay from
3. System calls `wallet_switchEthereumChain` to that chain
4. The existing flow continues: connect → sign nonce → verify → JWT
5. The user can pay in USDC on the chain they chose

## Phase 3 — Cross-chain bridge (1-2 days)

1. Add `bridge.py` module with partner integration
2. Default partner: **LI.FI** (best API, 25+ chains, clear docs)
3. Endpoint `GET /bridge/quote?from_chain=X&to_chain=Y&amount=N` returns the cheapest route
4. Endpoint `POST /bridge/execute` coordinates:
   - User signs `approve(usdc, bridge)` on source chain
   - User signs the bridge transaction
   - Backend tracks the bridge to completion
   - Backend confirms USDC delivery to recipient on destination chain
5. Add `bridges` table to track active bridges with status
6. Polling + webhooks for status updates

## Phase 4 — Cross-chain payment UI (1 day)

1. In the pay modal, when the user's chain differs from the recipient's chain:
   - "You have 50 USDC on Base"
   - "Recipient expects USDC on Avalanche"
   - "Bridge + send: cost $0.05, ETA 2 min"
   - "Bridge and send" button
2. Show bridge progress: pending → confirming → done
3. After success: the recipient sees the payment in their wallet

## Phase 5 — Partner fallback and monitoring (1 day)

1. Add Stargate as a fallback partner (different routing = different liquidity)
2. Cache the best partner per chain pair based on quote freshness
3. Monitor bridge success rates and slippage
4. Alert if any partner is down for >5 min

## Cost analysis (per bridge)

- LI.FI fee: 0.05-0.3% of amount
- Gas on source chain: $0.01-$2
- Gas on destination chain: $0.01-$2
- Total cost: typically 0.1-0.5% of payment amount
- Minimum payment to make bridge worthwhile: ~$5

## Risks and mitigations

- **Liquidity** — if a chain pair has low liquidity, bridge fails. Fallback to alternate partner.
- **MEV / sandwich attacks** — LI.FI uses optimized routes to minimize attack surface.
- **Regulatory** — pure USDC bridge, no KYC required, but we track all bridges for audit.
- **Time** — 2-5 min bridge. Mark payment as "pending" in the meantime so neither party thinks it failed.
- **Failed bridges** — if the bridge fails mid-flight, the user gets refunded automatically (LI.FI handles this on-chain). We just need to track and notify.

## Out of scope (for now)

- Cross-chain swaps (USDC → AVAX on a different chain)
- Non-EVM chains (Solana, Bitcoin, Sui)
- Fiat on/off ramps
- KYC / compliance flows

## Why LI.FI

- Best API quality and documentation
- Supports 25+ chains including all our targets
- Aggregates 20+ bridges and DEXs, so the user always gets the best route
- Has a hosted widget (`<li.fi-widget>`) for embedding the bridge UX directly in our modal
- Free tier: 100k quote requests/month
