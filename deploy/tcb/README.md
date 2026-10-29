# The blocklist sync — thecryptobeggar.io

The weekly job: rebuild the blocklist from public feeds, deploy the
site, then import the database out of the webroot.

## The three files

| file | what it is |
|---|---|
| `tcb_sync_blocklist.sh` | the job. It is what the cron runs. |
| `check_hostinger_deploy.py` | reads the MCP response and decides whether the deploy worked. |
| `mcp_call.py` | a small JSON-RPC client for the Hostinger MCP server. |

They are here because **neither `thecryptobeggar` nor `fop_school` is
a git repository**, and that is exactly how this job broke.

## The bugs that were here, and why

All three were the same shape: the job died **after** doing the work,
so the site was stale and nobody knew why.

**1. `mcp_call.py` was not where the script looked for it.** The call
was `cd $SCR && python3 mcp_call.py`, and `$SCR` is
`/data/cache/scratch`. The file was in `fop_school`. So:

    python3: can't open file '/data/cache/scratch/mcp_call.py'

every week. The blocklist was built, the archive was zipped, and then
the deploy never happened.

**2. The success check grepped for JSON that is not there.** It looked
for `"status": "success"` — with a space. The MCP returns **compact**
JSON:

    {"upload":{"status":"success",...},"deploy":{"status":"success",...}}

so that string never appeared. The grep failed on every deploy,
**including the ones that worked**, and the job died with "deploy
failed" while the site was already live.

**3. The arguments were malformed.** The old call passed the domain
and the archive inside the JSON. `mcp_call.py` takes them as
arguments and builds the arguments itself.

The check now **parses** the response and reads the deploy status from
it, rather than matching a serialisation — so it survives the server
changing its spacing.

## Where the files live now

The script searches for the helper in several places rather than
hardcoding one path, and fails with a clear message if it finds
none:

    /data/scripts/mcp_call.py
    /data/workspace/fop_school/mcp_call.py
    /data/scripts/hostinger_mcp.py

The copy in `/data/scripts/` is the one this uses. The `fop_school`
one is there because that is where it was found — but that is another
project's directory, and if it is ever cleaned up the weekly sync
breaks silently. Which is why it is searched for rather than
hardcoded.

## Running it

    bash /data/scripts/tcb_sync_blocklist.sh

The cron job is `TCB blocklist weekly sync`, every Monday at 05:00.

It ends by importing the blocklist out of the webroot and checking
the site's own stats endpoint, so a run that half-worked is visible
rather than silent.
