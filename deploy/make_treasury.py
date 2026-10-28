#!/usr/bin/env python3
"""Generate a treasury key for a whitelabel chain deployment.

The key is written to disk on the machine that runs it and never leaves
it: the private key goes into a 0600 file, the public address into a
0644 file next to it. Nothing is printed to stdout except the address.

Run on the VPS:
    /opt/ubotpay/venv/bin/python make_treasury.py <rpc_url> <secret_dir>

The address is derived from the key, then proved: a message is signed
and recovered, and the recovered address must match. A key that cannot
sign for its own address is worthless, and a treasury key that cannot
sign for its own address is a bug that would not show up until the first
payment.
"""
import os
import sys
import json

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

RPC = sys.argv[1] if len(sys.argv) > 1 else "https://rpc.soniclabs.com"
SECRET_DIR = sys.argv[2] if len(sys.argv) > 2 else "/opt/ubotpay/secrets"
KEY_NAME = sys.argv[3] if len(sys.argv) > 3 else "sonic_treasury"

os.makedirs(SECRET_DIR, mode=0o700, exist_ok=True)
os.chmod(SECRET_DIR, 0o700)

acct = Account.create()
w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 20}))
if not w3.is_connected():
    sys.exit(f"cannot reach {RPC}")
addr = w3.to_checksum_address(acct.address)

# prove the key controls the address
message = "ubotpay treasury control proof"
sig = acct.sign_message(encode_defunct(text=message))
recovered = w3.eth.account.recover_message(encode_defunct(text=message),
                                           signature=sig.signature)
if recovered.lower() != addr.lower():
    sys.exit("FATAL: the recovered address does not match the key")

key_hex = acct.key.hex()
if not key_hex.startswith("0x"):
    key_hex = "0x" + key_hex

key_path = os.path.join(SECRET_DIR, f"{KEY_NAME}.key")
pub_path = os.path.join(SECRET_DIR, f"{KEY_NAME}.pub")

# refuse to overwrite an existing key: a treasury key that is replaced
# without moving the funds is a dead address
if os.path.exists(key_path):
    sys.exit(f"refusing to overwrite {key_path}")

with open(key_path, "w") as fh:
    fh.write(key_hex)
os.chmod(key_path, 0o600)

with open(pub_path, "w") as fh:
    json.dump({
        "address": addr,
        "chain_id": w3.eth.chain_id,
        "rpc": RPC,
        "key_file": key_path,
        "control_proof": "signed+recovered OK",
    }, fh, indent=2)
os.chmod(pub_path, 0o644)

# the balance is what the operator needs next: the key has none until it
# is funded with the chain's native token
try:
    bal = w3.eth.get_balance(addr)
except Exception:
    bal = None

print(json.dumps({
    "address": addr,
    "chain_id": w3.eth.chain_id,
    "key_file": key_path,
    "pub_file": pub_path,
    "control_proof": "OK",
    "native_balance": None if bal is None else str(bal),
}, indent=2))
