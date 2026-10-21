"""
Regression tests for the Telegram surface (audit 20 Oct round 13).

/code was the only handler without the private-chat guard every other handler has.
It is not directly exploitable — auth_link_telegram_confirm still requires the
6-digit OTP plus the bot's API key — but the user types a secret into a group
transcript and the bot replies with linking instructions to everyone present. The
guard exists so that never happens, not only so an attacker is stopped.

The three raw-error leaks matter more than they look. `reply_text(f"...{r.text}")`
sends the API's response body straight into the chat, and those bodies can carry
internal detail: upstream payloads, hostnames, occasionally a stack-shaped string.
The user cannot act on any of it and it should not be in a chat log.

    python3 test_telegram_surface.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TG_PY = os.path.join(HERE, "tg.py")
RESULTS = []

src = open(TG_PY, encoding="utf-8").read()


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


def handler_body(name):
    """Source of one handler function, by name."""
    m = re.search(rf'async def {re.escape(name)}\(', src)
    if not m:
        return ""
    nxt = re.search(r'(?:async )?def \w+\(', src[m.start() + 10:])
    end = m.start() + 10 + nxt.start() if nxt else len(src)
    return src[m.start():end]


def t_every_command_handler_is_guarded():
    """The invariant: no handler may run in a group. /code was the outlier."""
    unguarded = []
    for cmd, fn in re.findall(r'CommandHandler\("([^"]+)",\s*(\w+)', src):
        if "_require_private" not in handler_body(fn):
            unguarded.append("/" + cmd)
    check("no command handler runs in a group", unguarded, [])


def t_message_handlers_are_guarded():
    unguarded = []
    for fn in ("on_start", "on_voice", "on_text"):
        if "_require_private" not in handler_body(fn):
            unguarded.append(fn)
    check("no message handler runs in a group", unguarded, [])


def t_code_guard_is_documented():
    """A guard whose reason is unclear gets removed as 'dead code' later."""
    body = handler_body("on_code")
    check("/code explains why it is guarded", "group" in body.lower(), True)
    check("and references the audit round", "round 13" in body, True)


def t_no_raw_error_body_to_the_chat():
    """r.text[:200] in a reply_text means the API's body lands in a chat log."""
    leaks = [ln.strip()[:70] for ln in src.split("\n")
             if "reply_text" in ln and ("r.text" in ln or ".text[" in ln)]
    check("no handler echoes the raw response body", leaks, [])


def t_no_exception_text_to_the_chat():
    """str(e) on an httpx exception can include the full URL and host."""
    leaks = [ln.strip()[:70] for ln in src.split("\n")
             if "reply_text" in ln and "str(e)" in ln]
    check("no handler echoes str(exception)", leaks, [])


def t_errors_are_logged_server_side():
    """Dropping the detail from the reply must not mean dropping it entirely —
    otherwise the operator loses the only copy."""
    body = handler_body("on_code")
    check("/code logs the detail", "log.warning" in body, True)
    check("on_approve logs the detail", "log.warning" in handler_body("on_approve"), True)


def t_user_messages_stay_actionable():
    """Replacing the raw body must leave the user something they can act on."""
    body = handler_body("on_code")
    check("/code error tells the user what to do",
          "ubotpay.com" in body or "expired" in body, True)
    ap = handler_body("on_approve")
    check("/approve error mentions co-signer", "co-signer" in ap, True)


def t_module_still_parses():
    check("tg.py parses", bool(ast.parse(src)), True)


def t_link_handler_docstring_position():
    """on_link puts a docstring after the guard, so it is not a docstring at all.
    Cosmetic, but it means the note about the retired command is invisible to
    anyone reading help() or the source in an editor."""
    body = handler_body("on_link")
    first = next((l.strip() for l in body.split("\n")[1:] if l.strip()), "")
    check("on_link docstring is the first statement", first.startswith('"""'), True)


if __name__ == "__main__":
    t_every_command_handler_is_guarded()
    t_message_handlers_are_guarded()
    t_code_guard_is_documented()
    t_no_raw_error_body_to_the_chat()
    t_no_exception_text_to_the_chat()
    t_errors_are_logged_server_side()
    t_user_messages_stay_actionable()
    t_module_still_parses()
    t_link_handler_docstring_position()
    print("=" * 78)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
