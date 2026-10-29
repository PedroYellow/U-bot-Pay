#!/usr/bin/env bash
# Weekly: rebuild blocklist from public feeds, redeploy site (full tree + _upload/blocklist.sqlite), then import it out of webroot.
set -euo pipefail
ROOT=/data/workspace/thecryptobeggar; SITE=$ROOT/site; SCR=/data/cache/scratch
set -a; . /data/.env; set +a; export HOSTINGER_API_TOKEN="$HOSTINGER_API_KEY"
T=$(cat $ROOT/admin_token.txt); B=https://thecryptobeggar.io
cd $SCR && python3 $ROOT/build_blocklist.py $SCR/blocklist.sqlite
mkdir -p $SITE/_upload && cp $SCR/blocklist.sqlite $SITE/_upload/ && echo 'Require all denied' > $SITE/_upload/.htaccess
cd $SITE && python3 - <<'PY'
import zipfile,os
z=zipfile.ZipFile('/data/cache/scratch/tcb_sync.zip','w',zipfile.ZIP_DEFLATED)
for root,dirs,files in os.walk('.'):
    for f in files:
        if f.startswith(('.bak_','.rl_','.notify','.authfail')): continue
        p=os.path.join(root,f); z.write(p,os.path.relpath(p,'.'))
z.close()
PY
# mcp_call.py takes <tool> <domain> [extra-json], and for
# hosting_deployStaticWebsite it builds the arguments itself from the
# domain and the archive path. The old call passed both inside the
# JSON, which mcp_call.py would then merge on top of a domain it
# already had.
#
# The path was relative to $SCR, and mcp_call.py is not in
# /data/cache/scratch - so the sync died with "can't open file" every
# week, after building the blocklist and the archive: the work was
# done and the deploy was not.
#
# It is searched for in several places rather than hardcoded to one.
# The copy in /data/workspace/fop_school was the one that worked, but
# that is another project's directory: if it is ever cleaned up, the
# weekly sync breaks silently - and it breaks AFTER the work, which
# is the worst way to break.
MCP_CALL=""
for cand in /data/scripts/mcp_call.py \
            /data/workspace/fop_school/mcp_call.py \
            /data/scripts/hostinger_mcp.py; do
    if [ -f "$cand" ]; then
        MCP_CALL="$cand"
        break
    fi
done
if [ -z "$MCP_CALL" ]; then
    echo "mcp_call.py not found - looked in:"
    echo "  /data/scripts/mcp_call.py"
    echo "  /data/workspace/fop_school/mcp_call.py"
    echo "  /data/scripts/hostinger_mcp.py"
    exit 1
fi
echo "  the MCP helper: $MCP_CALL"
# The success check PARSES the response. It used to grep for
# '"status": "success"' - with a space - but the MCP returns compact
# JSON, so that string never appears. The grep failed on every
# deploy, including the ones that worked, and the sync died with
# "deploy failed" after the site was already live.
#
# The MCP wraps the payload: {"result":{"content":[{"type":"text",
# "text":"<json>"}]}}, so the inner text is parsed and the deploy
# status is read from it. Reading the value rather than matching the
# serialisation means it survives a change in the server's spacing.
deploy_response=$(timeout 900 python3 "$MCP_CALL" hosting_deployStaticWebsite thecryptobeggar.io "$SCR/tcb_sync.zip") || { echo "deploy failed"; exit 1; }
echo "$deploy_response" | python3 /data/scripts/check_hostinger_deploy.py || { echo "deploy failed"; exit 1; }
SHA=$(sha256sum $SCR/blocklist.sqlite | cut -d' ' -f1)
for i in $(seq 1 12); do sleep 20; R=$(curl -s -X POST "$B/api.php?action=import_blocklist" -H "Authorization: Bearer $T" -H "Content-Type: application/json" -d "{\"sha256\":\"$SHA\"}"); echo "$R" | grep -q '"ok":true' && { rm -rf $SITE/_upload; echo "imported: $R"; exit 0; }; done
echo "import failed: $R"; exit 1
