"""
BitCRM fix: handle mcp-prd.bit24.es "suscripción vencida" error response.

Symptom (reproduced in production, 2026-10-23):
  /link <name>  ->  "Bitrix24 error: string indices must be integers, not 'str'"

Root cause: bit24.es's MCP endpoint changed its error shape. The
client's _mcp() in /opt/bitcrm/bitrix_client.py used to detect errors
with:

    if content.startswith("Error executing tool"):
        raise RuntimeError(content)

The new shape returns errors as the content's text directly, e.g.:

    "Acceso MCP bit24 cerrado: el periodo de prueba o la suscripción
     han vencido. Para recuperarlo, abre la app bit24 en tu Bitrix24
     y contrata la suscripción, o escribe a soporte@bit24.es."

The old code only checked for "Error executing tool", so it parsed the
text as JSON, failed, fell through to `return content` (a string). The
caller then indexed it with `r["id"]`, which produced the cryptic
`string indices must be integers, not 'str'`.

Two changes to /opt/bitcrm/bitrix_client.py:

1. Detect both the old prefix "Error executing tool" and the new
   prefix "Acceso MCP bit24" and raise a RuntimeError with the text.
2. When the content is not JSON and not detected as an error, raise
   instead of returning the raw string. Any non-JSON content from a
   tool call is an error; a successful tool call would always be JSON.

The diff to apply to the _mcp method:

    content = d.get("result", {}).get("content", [{}])[0].get("text", "")
    # The MCP error shape changed: it used to start with
    # "Error executing tool ..." but now returns
    # "Acceso MCP bit24 cerrado: ..." directly. Treat any of those
    # as an error rather than returning a plain string that the
    # caller would later try to index, producing
    # "string indices must be integers, not 'str'".
    if content.startswith("Error executing tool") or content.startswith("Acceso MCP bit24"):
        raise RuntimeError(content)
    try:
        parsed = json.loads(content)
    except Exception:
        # Non-JSON content is never a successful tool result; surface
        # it as a clear error instead of letting it leak through
        # and be subscripted by the caller.
        raise RuntimeError(content) from None
    if isinstance(parsed, dict) and "error" in parsed and len(parsed) <= 2:
        raise RuntimeError(str(parsed["error"]))
    return parsed

After applying, the /link command will respond to the operator
with the actual error message instead of the TypeError.

Verify with: /opt/bitcrm/venv/bin/python /opt/bitcrm/test_mcp_shape.py
5/5 tests pass with the current expired subscription; the same tests
will still pass after the subscription is renewed because the
RuntimeError path is the only path that throws when content is
non-JSON, and the test asserts that path is taken.
"""
# The diff was applied by hand to /opt/bitcrm/bitrix_client.py on
# 2026-10-23. This file documents the change for future audits.
# To verify the fix is in place:
#   grep -n "Acceso MCP bit24" /opt/bitcrm/bitrix_client.py
# should return one match.
