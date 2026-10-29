# The host is loaded, and it changes what the numbers mean

28-29 Oct 2026.

## What happened

A test suite was reported failing:

    FAIL  test_seed_for_idempotent.py

The test had been passing. Chasing it found something else entirely:
**the host was at load average 53 on 2 CPUs**, and the cause was
**255 orphaned Chromium processes** from earlier browser testing,
consuming 218% of CPU. The oldest was three days old.

## What that does to the measurements

`import web3` took **43 seconds**. The chain:

    import web3            43 s
    import eth_account     22 s
    import eth_abi          9 s
    import eth_keys         7 s
    import importlib.metadata  3.4 s

Almost none of it was CPU - the syscalls accounted for 0.01 s. The
time was spent waiting for a CPU that 255 browser processes had
taken.

So every number measured on this host while that was true is a
measurement of the load, not of the code.

## Production was NOT affected

This matters, because it is easy to assume the opposite.

The running services imported web3 ONCE, at boot. Their health
endpoints answered in 8-516 ms while the host was at load 70:

    8090  health=200 in 0.008577s
    8092  health=200 in 0.094041s
    8093  health=200 in 0.103278s
    8094  health=200 in 0.516414s
    8095  health=200 in 0.084876s

All seven services stayed active and all three sites answered 200
throughout. The load hurts **new processes** - tests, deploys, a
restart - not the ones already running.

## The test suite, with the load accounted for

    39 passed, 0 failed

with a 300-second timeout per test instead of 150. The six tests
that import the application each pay the ~35 second import; the rest
are quick.

So the suite is GREEN. The earlier failures were the load, and the
one real failure - the concurrent race on a shared test database - was
fixed separately.

## The rule

Before believing a slow or failing measurement on this host, check
the load:

    uptime
    pgrep -c chrome

A load average above about 5 on 2 CPUs means the numbers describe the
host, not the code.

## Why the orphans accumulated

Each browser session in the earlier testing left its Chromium tree
running, and each one is about 9-11 processes. Nothing cleaned them
up - the browser driver exited and the children were reparented to
init, where they sat for days.

The live one - the CDP browser on port 9333 that the DOM-inspection
skill uses - is not an orphan and must not be killed.

---

## The evidence that all 23 browsers are orphans

29 Oct 2026. Before killing anything, this was established:

    a driver process      none - no node or python process is
                          controlling a browser
    established sockets   none - ss shows no ESTABLISHED
                          connection to any of the 23 CDP ports
    the ages              89h, 101h, 102h, 103h, 114h, 118h
                          the YOUNGEST is 89 hours old

So nothing is attached to any of them. The one on port 9333 - the
profile the DOM-inspection skill would connect to - is not "in use"; it
is a leftover from the frontend testing three and a half days ago,
showing the usolpay site it was last pointed at.

Each browser leaves about 9-11 processes, and the driver exits
without cleaning them up, so they are reparented to init and sit
there burning CPU.

Killing them is reversible: the next browser session starts its own.
