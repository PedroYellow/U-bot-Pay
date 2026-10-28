#!/usr/bin/env python3
"""Make the sync bridge work when a loop is already running.

The bridge ran the coroutine on a NEW event loop:

    loop = asyncio.new_event_loop()
    loop.run_until_complete(coro)

That works when the caller is synchronous. But the test - and any
caller already inside an async function - is running inside a loop,
and asyncio refuses:

    RuntimeError: Cannot run the event loop while another loop is
    running

So the bridge runs the coroutine on a THREAD with its own loop when
there is already one running. A thread has no loop of its own, so a
fresh loop inside it is fine.

That is the standard way to call async code from sync code inside an
async context.

The tx-hash check was also wrong: a Starknet transaction hash is at
most 63 hex digits - the field is 251 bits - so a 64-digit hash is
not a Starknet hash. The check accepted it because it only required
"8 to 63 digits" and the test's 64-digit hash... no, it REJECTED it
correctly and the TEST was wrong: it used "0x" + 62 hex chars, which
is 63 digits with the 0x, and the body was 62 - so it should have
passed.

The real bug: the check strips the "0x" and then requires 8..63
digits, but a Starknet hash can be SHORTER than 63 after the leading
zeros are dropped. The test's 62-digit body is valid, so the length
check should accept it - and it does. The failure was elsewhere.

Usage: fix_sync_bridge.py <engine.py>
"""
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/data/workspace/ubotpay/deploy/ustartkpay/chains_starknet_engine.py"

src = open(PATH, encoding="utf-8").read()

OLD = '''    @staticmethod
    def _run(coro):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()'''

NEW = '''    @staticmethod
    def _run(coro):
        """Run a coroutine to completion from synchronous code.

        Two cases, because they need different things:

        - the caller is synchronous: a fresh event loop, on this thread
        - the caller is ALREADY inside a loop: asyncio refuses to run
          one loop inside another ("Cannot run the event loop while
          another loop is running"), so the coroutine goes to a THREAD
          with its own loop. A thread has no loop of its own, so a
          fresh one inside it is fine.

        The second case is not a rare caller: the test drives the
        engine from an async function, and so would any handler that
        already awaits something.
        """
        import asyncio
        import threading

        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False

        if not running:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()

        # Already inside a loop: run it on a thread.
        box = {}

        def _worker():
            loop = asyncio.new_event_loop()
            try:
                box["value"] = loop.run_until_complete(coro)
            except BaseException as e:      # noqa: BLE001 - carried to the caller
                box["error"] = e
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join()
        if "error" in box:
            raise box["error"]
        return box.get("value")'''

if OLD not in src:
    print("  the bridge was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(PATH, "w", encoding="utf-8").write(src)

try:
    compile(src, PATH, "exec")
    print(f"{PATH}: the bridge handles a running loop")
except SyntaxError as e:
    print(f"{PATH}: it does NOT parse: {e}")
    sys.exit(1)
