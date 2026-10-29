#!/usr/bin/env python3
"""Define AGENT_ID in every whitelabel's HTML, so the mic works.

The error a user sees:

    Error de microfono o conexion: AGENT_ID is not defined

That is a ReferenceError in the browser, not a microphone problem and
not a connection problem. The page calls:

    conv = await Conversation.startSession({
      agentId: AGENT_ID, ...

and `AGENT_ID` is never declared.

## How it got that way

Audit commit c30b869 (security, 17 Oct) removed the injection of the
ElevenLabs agent id into the browser bundle - and correctly so: it is
a credential, and the bundle is public. The commit's own words:

    deploy_web.sh: removed sed-injection of UBOT_API_KEY and
    __AGENT_ID__; new sanity check refuses to deploy if any sk-* or
    x-api-key appears in the published HTML.

    web/index.html and web/agent.html: removed const API_KEY, AGENT_ID
    and the x-api-key request header.

But it removed the DECLARATION and left the USE. So every page that
was deployed from that point on ReferenceErrors the moment somebody
pressed the microphone.

Four of the five whitelabels have it:

    ubotpay.com      0
    ustartkpay.com   0
    usonicpay.com    0
    utronpay.com     0
    usolpay.com     1   <- the only one that works

## What this does

It declares the constant, in the same place the other deployment
already does it - immediately before the call that uses it.

The id is the PUBLIC agent id, the same one the standard ElevenLabs
widget on the page connects to. It is not a secret: it is in the
widget's own configuration on the same public page. What the security
audit removed was the API KEY, and that is still not here.

So the security posture is unchanged - no key, no credential, just the
identifier of which agent to talk to, which the page already
publishes.

Idempotent: if the constant is already there, nothing changes.

Usage: fix_agent_id.py <html-file> [...]
"""
import os
import shutil
import sys

# The public agent id. The same one the standard widget on the page
# uses, and the same one the one deployment that works already has.
AGENT_ID = "agent_3601m4yz1xhgffmb1mb4j0sab89a"

OLD = (
    "    setTalkUI(true);\n"
    "    conv = await Conversation.startSession({"
)

NEW = (
    "    setTalkUI(true);\n"
    "    // The ElevenLabs Conversations endpoint needs a real agent id.\n"
    "    // This one was removed from the bundle during the 17 Oct security\n"
    "    // audit - correctly, it sat next to the API key injection - but\n"
    "    // the USE of it stayed, so the microphone threw a ReferenceError:\n"
    "    //\n"
    "    //     Error de microfono o conexion: AGENT_ID is not defined\n"
    "    //\n"
    "    // which reads like a microphone fault and is not one. This is the\n"
    "    // PUBLIC agent id - the same one the standard widget on this page\n"
    "    // already connects to. No key, no credential: just which agent to\n"
    "    // talk to, and the page already says so.\n"
    "    const AGENT_ID = \"%s\";\n"
    "    conv = await Conversation.startSession({" % AGENT_ID
)


def patch(text):
    if "const AGENT_ID" in text:
        return text, False
    if OLD not in text:
        return None, False
    return text.replace(OLD, NEW, 1), True


def main():
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        return 1

    changed = skipped = failed = 0
    for path in paths:
        if not os.path.exists(path):
            print(f"  {path}: does not exist")
            failed += 1
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        out, did = patch(src)
        if out is None:
            print(f"  {path}: the anchor was not found - the page may be "
                  f"structured differently")
            failed += 1
            continue
        if not did:
            print(f"  {path}: already declared")
            skipped += 1
            continue
        shutil.copy2(path, path + ".bak-agentid")
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"  {path}: AGENT_ID is now declared")
        changed += 1

    print(f"  {changed} patched, {skipped} already fine, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
