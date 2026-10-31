# Security audit — 31 Oct 2026

Trigger: "creo que nos están atacando".

**Verdict: there is no attack.** Everything that looked alarming had
an explanation, and every one of those explanations was checked rather
than assumed. What the audit DID find is three real defects in our
own configuration - none of them an intrusion, all of them fixed.

---

## 1. The access logs: is anyone getting in?

### SSH

Every accepted connection uses **one** key - the agent's own:

```
256 SHA256:mGHGSOqzzi1+cRb8Tqziu1o+J9ZyCHZa3zn6448Rtiw hermes-agent (ED25519)
```

That is the ONLY key in `authorized_keys`. There are no others.

The 50 distinct source addresses are all in `187.127.64.105-154`. That
is this agent's own egress pool - `api.ipify.org` reports
`187.127.64.113` from here. So the addresses are ours, rotating.

```
failed password attempts:  0
invalid users:             18
```

Zero failed passwords is not luck - `PasswordAuthentication` is off,
so a password is never even considered. Brute force has nothing to
attack.

fail2ban is running with the `sshd` jail.

### The network

```
0.0.0.0:22    sshd
0.0.0.0:80    nginx
0.0.0.0:443   nginx
127.0.0.1:8090-8095   the five deployments
127.0.0.1:8091        the data agent
127.0.0.1:65529       monarx-agent (Hostinger's)
```

Only 22, 80 and 443 are reachable. ufw: `deny (incoming)` by default.

### The web

4,509 attempts in the rotated nginx logs, and the usual internet
noise:

```
3467  /wp-admin/install.php?step=1
  74  //xmlrpc.php?rsd
  63  /.env
  41  /.git/config
```

None of them got anything. But the responses were wrong - see below.

### The processes

Nothing unfamiliar. Only the five deployments, nginx, the data agent,
fail2ban, the Hostinger agent and the kernel threads. No shells, no
 miners, no reverse shells.

---

## 2. The money: has anything moved?

### The keys derive exactly the addresses we watch

This is the check that matters. It derives each treasury address
from the private key ON the server and compares it with the address
the guard-watch monitors:

```
Solana:  BX6sfCwA9CkVqfpppco3HgXi3gCMhxQgBFoH4yLpJk55   == the guard
Tron:    TKVC8AzsercmUg69cmdojaVTnAixmWpCWb            == the guard
```

Same key, same address. The keys have not been swapped.

### The balances

```
Avalanche treasury   0.292492 AVAX
Solana treasury      0.099626 SOL      (gas)
Sonic treasury       0 S
Solana USDC          0                 (no token account at all)
```

### The transactions

Avalanche's treasury has 25 transactions. The most recent is
**21 Oct**. All of them go to the USDC contract or back to itself -
the payments. None to an unknown address.

The payments table:

```
0xbE440bf22FFDDdD8372DDf66a4beffEB8D41B209   settled   8
0x000000000000000000000000000000000000dEaD    failed    1
0x000000000000000000000000000000000000dEaD    pending   1
```

Yours, and the burn address (the dual-control test).

### The one thing that looked bad

The Solana RPC returns signatures marked `finalized` with `err=None`
for transactions that **do not exist** - `getTransaction` answers
"WrongSize". Twenty of them, in pairs, timestamped to the second.

That is a behaviour of the public RPC, not a movement:

- the treasury holds 0 USDC and has no token account
- the key on the server derives exactly the address being queried
- the balance did not change

---

## 3. The secrets

```
ubotpay      ANTHROPIC_API_KEY  d3e4c3d564f9
ubotpay      UBOT_API_KEY       29373f936a19
ubotpay      ELEVENLABS_API_KEY 1df655176304
usolpay      USOL_API_KEY       d6cec8272e8a
usolpay      ELEVENLABS_API_KEY 2e0f28dee393
usonicpay    USONIC_API_KEY     219e4f39ef4e
utronpay     UTRON_API_KEY      ddea7b3aee6b
ustartkpay   USTART_API_KEY     4adf6e9ac1d7
```

**Every API key is different in every deployment.** The only shared
one is `TRONRENTAL_WEBHOOK_SECRET` between ubotpay and utronpay -
deliberate, it is the same service on two domains.

The permissions:

```
.env                    600   (all five)
secrets/*.key           600   (all four)
secrets/*.json          600
/root/backups/2026-*    700   with the files at 600
```

The webhooks use `hmac.compare_digest` - constant time - and answer
503 if unconfigured.

The agent endpoints answer 401 without a key and 401 with a wrong
one.

---

## 4. What was actually wrong

### The API answered 200 with HTML for unknown paths

Every whitelabel serves web and API from one server block, so the
SPA catch-all applied to the API host:

```
https://api.ubotpay.com/.env           -> 200  text/html
https://api.ubotpay.com/.git/config    -> 200  text/html
https://api.ubotpay.com/server.key     -> 200  text/html
```

No secret leaked - the fallback serves index.html. But:

- a scanner sees 200 and keeps digging; a 404 would have ended it
- an API client gets HTML where it expected JSON
- the 200 makes the path look like it EXISTS

4,509 attempts in the logs got 200.

Fixed with a `map` in the http block and `if ($map) { return 404; }` -
one map per deployment, unique names, because a map is global (the
same lesson as the CORS collision).

Now every API host answers 404 for a path it has no route for, and
the five websites are untouched.

### A world-readable nonce database

```
-rw-r--r-- /opt/ubotpay/dataagent_nonces.db
```

The `.env` files are 600 because the deploy script chmods them. A
database the service CREATES at runtime was covered by nobody, and
the default umask made it 644.

All five services now carry `UMask=0077`. The umask is the real fix -
chmod alone would have left the next file the service creates just as
exposed.

### A world-readable backup

```
drwxr-xr-x /root/backups/predeploy-20261020T104426Z/
-rw-r--r-- .../ubotpay.db
```

Now 700, with the database at 600. The dated backups were already
right.

---

## 5. The checks

`deploy/check_public_endpoints.py` now covers the paths that must
not exist, on all five API domains:

```
65 endpoints checked, 0 failures
```

including:

```
/.env  /.git/config  /server.key  /seedphrase.txt   ->  404 everywhere
```

---

## 6. What is left

Not security findings - the ordinary backlog:

1. The 255 orphaned Chromium processes, at load 56. The host is slow
   and that is why the suites take 478 seconds instead of 30.
2. Four treasuries that cannot send: Avalanche has no gas, Sonic has
   no S, Starknet is not deployed, Solana has no USDC.

Nothing here was moved. No funds were touched.
