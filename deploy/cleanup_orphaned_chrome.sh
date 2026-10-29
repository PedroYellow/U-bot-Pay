#!/bin/sh
# Clean up the orphaned Chromium processes on the VPS.
#
# WHAT THIS IS
#
# 255 Chromium processes from earlier browser testing, across 23
# separate browsers, each with its own CDP port (9333-9370) and its
# own /tmp/chrome-*-prof profile. They have been reparented to init
# and have been burning 218% of CPU for three and a half days.
#
# WHY THEY EXIST
#
# Each browser session in the earlier testing left its Chromium tree
# running. The driver exited without cleaning them up, so the children
# were reparented to init, where they sat.
#
# WHY IT MATTERS
#
# The host has 2 CPUs. With 255 processes competing for them:
#
#     import web3        -> 43 seconds
#     import dataagent   -> 78 seconds
#     import api         -> 46 seconds
#
# The running services are NOT affected - they imported web3 once, at
# boot, and their health endpoints answer in 8-516 ms. But every NEW
# process pays 30-78 seconds, so every test and every deploy crawls,
# and any measurement describes the load rather than the code.
#
# WHAT IT KILLS AND WHAT IT KEEPS
#
# It kills every Chromium process EXCEPT the ones on the
# chrome-cdp-prof profile - that is the browser the DOM-inspection
# skill connects to, and it is the one a person might be using.
#
# The evidence that the rest are orphans:
#
#     a driver process    none - no node or python process is
#                          controlling a browser
#     established sockets  none - ss shows no ESTABLISHED connection
#                          to any of the 23 CDP ports
#     the ages            89h, 101h, 102h, 103h, 114h, 118h
#                         the YOUNGEST is 89 hours old
#
# Every one of them shows about:blank as its page. They are not
# loading anything.
#
# REVERSIBILITY
#
# The browser is not a service. The next browser session starts its
# own. Nothing that is running depends on these processes.
#
# Usage:
#   sh cleanup_orphaned_chrome.sh          # report only, change nothing
#   sh cleanup_orphaned_chrome.sh --go     # do it
set -u

KEEP_PROFILE="chrome-cdp-prof"
DOIT=0
[ "${1:-}" = "--go" ] && DOIT=1

echo "════════ before ════════"
before=$(ps -eo pid,args | grep '[m]s-playwright/.*/chrome ' | wc -l)
load_before=$(cut -d' ' -f1-3 /proc/loadavg)
echo "  the chrome processes: $before"
echo "  the load:             $load_before"

if [ "$DOIT" -eq 0 ]; then
    echo
    echo "  this is a DRY RUN - nothing was changed."
    echo "  re-run with --go to do it."
    echo
    echo "  the orphans that would be killed:"
    ps -eo pid,etimes,args | grep '[m]s-playwright/.*/chrome ' | grep -v "$KEEP_PROFILE" \
        | awk '{printf "    %-8s %8ss  %s\n", $1, $2, $4}' | head -6
    echo "    ... and $(( before - $(ps -eo pid,args | grep '[m]s-playwright/.*/chrome ' | grep "$KEEP_PROFILE" | wc -l) )) more"
    echo
    echo "  the CDP browser, which would be KEPT:"
    ps -eo pid,etimes,args | grep '[m]s-playwright/.*/chrome ' | grep "$KEEP_PROFILE" \
        | awk '{printf "    %-8s %8ss  %s\n", $1, $2, $4}' | head -3
    echo
    echo "  the CDP profile is the one the DOM-inspection skill uses."
    echo "  Everything else has been idle for 89+ hours."
    exit 0
fi

echo
echo "════════ killing the orphans ════════"
# The kill is done in one pass by profile, so it does not walk the
# process table once per process - which on a host at load 58 is
# itself slow, and is why an earlier attempt timed out.
#
# The match is on the BINARY path, not on the word "chrome". A grep
# for "[c]hrome" also matches the grep itself, the awk, and this
# script's own shell - and a pkill that matches its own shell kills
# the shell before it can report what it did.
ps -eo pid,args | grep '[m]s-playwright/.*/chrome ' | grep -v "$KEEP_PROFILE" \
    | awk '{print $1}' > /tmp/chrome_orphan_pids.txt

count=$(wc -l < /tmp/chrome_orphan_pids.txt)
echo "  the orphans: $count"

if [ "$count" -gt 0 ]; then
    xargs -a /tmp/chrome_orphan_pids.txt -r kill -9 2>/dev/null
    sleep 3
fi

# anything left that is not the CDP profile
left=$(ps -eo pid,args | grep '[m]s-playwright/.*/chrome ' | grep -v "$KEEP_PROFILE" \
       | awk '{print $1}' | wc -l)
if [ "$left" -gt 0 ]; then
    echo "  $left survived the first pass - a second pass"
    ps -eo pid,args | grep '[m]s-playwright/.*/chrome ' | grep -v "$KEEP_PROFILE" \
        | awk '{print $1}' | xargs -r kill -9 2>/dev/null
    sleep 2
fi

rm -f /tmp/chrome_orphan_pids.txt

echo
echo "════════ after ════════"
after=$(ps -eo pid,args | grep '[m]s-playwright/.*/chrome ' | wc -l)
load_after=$(cut -d' ' -f1-3 /proc/loadavg)
echo "  the chrome processes: $after  (was $before)"
echo "  the load:             $load_after  (was $load_before)"
echo
echo "  the CDP browser, still alive:"
curl -s --max-time 5 http://127.0.0.1:9333/json/version 2>/dev/null \
    | head -c 120
echo
echo
echo "  the load falls gradually - it is a 1, 5 and 15 minute average,"
echo "  so give it a minute or two to read lower."
