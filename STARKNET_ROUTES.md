# Starknet: the routes, verified

28 Oct 2026. Everything here was checked against the chain or against
the source's own documentation - not inferred from a guide.

## The correction

Earlier I recorded that "LI.FI does not support Starknet", and that
StarkGate was the candidate. The first half is right and the second
was incomplete.

**Circle's CCTP IS live on Starknet.** Circle's own site lists
Starknet among the CCTP blockchains, and there is an official
"Transfer USDC between Starknet and Arc" quickstart in their
developer docs.

And the contracts are **deployed on mainnet** - read from the chain:

    TokenMessengerMinterV2  0x07d421B9cA8aA32DF259965cDA8ACb93F7599F69209A41872AE84638B2A20F2a
      class hash 0x3de0c68dad6bab78275624542e25ffab0d4a80f5bfdfc19765d177746cb3c35
    MessageTransmitterV2    0x02EBB5777B6dD8B26ea11D68Fdf1D2c85cD2099335328Be845a28c77A8AEf183
      class hash 0x1a82c735c08fb38dbc5b7b6e5f57973ad3a8fcf224a5f082fb9a7c8388f7ba1

    CCTP domain 25 = Starknet

And the native USDC, read from the contract:

    0x053c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8
    name "USD Coin"   symbol "USDC"   decimals 6

### The shape is different on Starknet

On EVM, CCTP is two separate contracts - `TokenMessengerV2` and
`TokenMinterV2`. On Starknet it is ONE combined
`TokenMessengerMinterV2`, in **Cairo** contracts.

So a port of the EVM CCTP code does not work as-is. The entry points
are Cairo, the ABI is different, and the "burn then mint" flow is
driven from a Cairo account contract - which is exactly the shape
this deployment already has.

## What is NOT true

**LI.FI does not support Starknet.** Its live chain list has no
Starknet, so the bridge cannot route through it.

**The bridge has no CCTP support at all.** `bridge.py` uses LI.FI
only, and Starknet is not in its chain list. So the CCTP finding is a
**funding route**, not an existing feature of the codebase.

Adding it means:
  1. a Starknet entry in the bridge's chain list
  2. a Cairo CCTP client - a burn on the source chain, an attestation,
     a mint on the destination
  3. the message format, which is not the EVM one

That is a real piece of work, and it is the piece that would make
Starknet a *destination* rather than just a chain we can read.

## The routes to fund a Starknet account

| route | what it moves | state |
|---|---|---|
| **StarkGate** | ETH, STRK and others from Ethereum | the official bridge. Web UI. |
| **Circle CCTP** | native USDC, burn-and-mint | contracts verified live on mainnet |
| **Foundation faucet** | STRK on Sepolia | working, no auth, proof of work |

### StarkGate

`https://starkgate.starknet.io/bridge/deposit` - it has a
"Manual destination address" field, so a Starknet account address can
be the destination.

One fact from StarkGate's own FAQ, and it is the load-bearing one:

> On Starknet, your account is deployed only when you initiate your
> first transaction. If the account is "not yet deployed," it means it
> hasn't performed any activity on-chain.

That is exactly the state the first bootstrap got into - funded at an
address with no contract - and it is a VALID state. The funds are
there; the account is not deployed until it sends something.

So the correct order is:

    1. derive the address from the key
    2. bridge the funds to it
    3. the first transaction deploys the account

And the deployment costs gas in STRK, so the account needs STRK
**before** it can deploy. On Sepolia the faucet covers that. On
mainnet, the bridge has to send STRK as well as USDC.

### Circle CCTP

The contracts are on mainnet and the domain is 25. But the flow is
burn-and-mint with an attestation step in between, and it is not
implemented in this codebase.

## What we have

    The account       0x4af96d3945b91f77f2f36abfd45291ec5e0d7d753709ee2a5484a34f05ce259
                      deployed on Sepolia, and it signs
    The engine        reads mainnet, 31/31 interface checks
    The deployment    /opt/ustartkpay, port 8095, serving Starknet
    The key           /opt/ustartkpay/secrets/account.json (0600)

## What we do not have

    A MAINNET account. The treasury address is empty on purpose - an
    address that does not exist is not a lie the dashboard can
    publish, and the engine runs read-only until it does.

    A funded mainnet account. That needs STRK from StarkGate and
    USDC from either StarkGate or CCTP - and the USDC route depends on
    which one we build first.

    A mainnet deployment of the account contract. The Sepolia one is
    deployed; the mainnet class is verified (oz v1.0.0) but the
    contract is not deployed on mainnet.
