#!/usr/bin/env python3
"""The deploy script must not regenerate the secrets, and must set the prefix.

The Sonic deploy script writes $DIR/.env from scratch. It used to
generate a fresh JWT secret, a fresh voice secret and a fresh API key
every time it ran:

  - the JWT secret signs the session tokens, so regenerating it
    invalidates every issued JWT and logs everybody out
  - the voice secret authenticates the ElevenLabs voice webhook, so
    regenerating it makes the webhook fail closed with a 401
  - the API key is what the front-end and the DataAgent call this
    deployment with, so regenerating it breaks them

And it did not write DEPLOY_PREFIX at all. config.py turns cfg("PORT")
into USONIC_PORT only when the prefix is set; without it the service
falls back to USOL_* and every setting silently resolves to its
default - the same class of bug as the CORS map collision, where
nothing looks wrong and the deployment is quietly misconfigured.

This runs the .env step against a COPY of the tree, so it never
touches the live deployment.

Usage: test_deploy_env.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

SRC = "/opt/usonicpay"
LIVE_ENV = "/opt/usonicpay/.env"

failures = []


def check(cond, msg, detail=""):
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}" + (f"  [{detail}]" if detail else ""))
        failures.append(msg)


def main():
    script = os.path.join(SRC, "deploy", "deploy_usonicpay.sh")
    if not os.path.exists(script):
        print(f"  the deploy script is not at {script}")
        return 1

    print("=" * 72)
    print("D1: the script writes DEPLOY_PREFIX")
    print("=" * 72)
    with open(script, encoding="utf-8") as fh:
        src = fh.read()
    check("DEPLOY_PREFIX" in src,
          "the script mentions DEPLOY_PREFIX")
    check(re.search(r"^DEPLOY_PREFIX=USONIC$", src, re.M) is not None,
          "the script writes DEPLOY_PREFIX=USONIC")

    print()
    print("=" * 72)
    print("D2: the script preserves the secrets")
    print("=" * 72)
    # It must READ the existing values, not just generate new ones.
    check("read_existing" in src or "grep" in src,
          "the script reads the existing .env")
    check(src.count("head -c 32 /dev/urandom") >= 1,
          "the script can still generate a secret for a first deploy")

    print()
    print("=" * 72)
    print("D3: run it against a copy and compare")
    print("=" * 72)

    tmp = tempfile.mkdtemp(prefix="usonicpay-deploytest-")
    try:
        env_path = os.path.join(tmp, ".env")
        with open(env_path, "w", encoding="utf-8") as fh:
            fh.write(
                "DEPLOY_PREFIX=USONIC\n"
                "USONIC_PROFILE=usonicpay\n"
                "USONIC_PORT=8094\n"
                "USONIC_JWT_SECRET=THE-JWT-SECRET-MUST-SURVIVE\n"
                "USONIC_VOICE_SECRET=THE-VOICE-SECRET-MUST-SURVIVE\n"
                "USONIC_API_KEY=THE-API-KEY-MUST-SURVIVE\n"
            )

        before = open(env_path, encoding="utf-8").read()

        # extract only the .env step, and point it at the copy
        m = re.search(
            r'(^echo "── 2\. the \.env ──".*?^echo "  written, 0600"$)',
            src, re.S | re.M)
        if not m:
            print("  could not find the .env step in the script")
            return 1

        step = m.group(1)
        step = re.sub(r"^DIR=/opt/usonicpay$", f"DIR={tmp}", step, flags=re.M)

        step_path = os.path.join(tmp, "env_step.sh")
        with open(step_path, "w", encoding="utf-8") as fh:
            fh.write(step)

        r = subprocess.run(["sh", step_path], capture_output=True,
                           text=True, timeout=60)
        if r.returncode != 0:
            print(f"  the .env step failed: {r.stderr[:200]}")
            return 1

        after = open(env_path, encoding="utf-8").read()

        for key in ("DEPLOY_PREFIX",
                    "USONIC_JWT_SECRET",
                    "USONIC_VOICE_SECRET",
                    "USONIC_API_KEY"):
            old = re.search(rf"^{key}=(.*)$", before, re.M)
            new = re.search(rf"^{key}=(.*)$", after, re.M)
            if not new:
                check(False, f"{key} is written")
                continue
            if key == "DEPLOY_PREFIX":
                check(new.group(1) == "USONIC",
                      "DEPLOY_PREFIX is USONIC", new.group(1))
            else:
                oldval = old.group(1) if old else ""
                check(new.group(1) == oldval and oldval != "",
                      f"{key} survived the deploy",
                      f"{oldval[:20]} -> {new.group(1)[:20]}")

        # and the live deployment's own secrets must be intact too
        if os.path.exists(LIVE_ENV):
            live = open(LIVE_ENV, encoding="utf-8").read()
            check("DEPLOY_PREFIX=USONIC" in live,
                  "the live .env has DEPLOY_PREFIX")
            for key in ("USONIC_JWT_SECRET", "USONIC_VOICE_SECRET",
                        "USONIC_API_KEY"):
                mm = re.search(rf"^{key}=(.+)$", live, re.M)
                check(bool(mm and mm.group(1).strip()),
                      f"the live .env has {key}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 72)
    print(f"{len(failures)} failure(s)")
    print("=" * 72)
    for f in failures:
        print(f"  - {f}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
