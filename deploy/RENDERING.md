# Rendering a page for inspection

The product is visual, so "it should look right" is not a check you can do
by reading CSS. These are the tools that make the check possible.

## What is available

| Where | What | Notes |
|---|---|---|
| `/data/cache/scratch/pw` | Playwright + Chromium 156 | local, no root needed |
| `/opt/render-venv` on the VPS | Playwright + Chromium 129 | root, system deps installed |
| `/data/cache/scratch/pwlibs/extract` | the shared libraries Chromium needs | see below |

## Running it locally, without root

The local Playwright install has the browser but the machine is not root,
so `apt-get install chromium` fails on the dpkg lock. The libraries it
needs can still be fetched and unpacked by hand:

```bash
# 1. resolve the package paths from the Debian index
curl -sL "https://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.xz" -o /tmp/P.xz
# 2. unpack them into a user-space prefix (no root needed)
for d in *.deb; do dpkg-deb -x "$d" extract/; done
# 3. point the loader at it
export LD_LIBRARY_PATH=/data/cache/scratch/pwlibs/extract/usr/lib/x86_64-linux-gnu
```

`xz` is not installed here, so the index is decompressed in Python
(`lzma.open`). With the libraries in place `ldd` reports 0 missing and
the browser runs.

## Two things that will hang the screenshot

Both of these cost an iteration each, so they are written down:

**1. `wait_until="networkidle"` never fires.** The page holds a websocket
for push notifications, so the network is never idle. Use
`wait_until="commit"` and then wait a fixed time.

**2. `page.screenshot()` waits for fonts and for the compositor to
settle.** The hero has a CSS animation that never stops, and the Google
Fonts stylesheet can be slow. Playwright's own screenshot times out. Two
fixes together: freeze the animations with
`add_style_tag(content="*,*::before,*::after{animation:none!important;transition:none!important}")`
and capture through CDP instead:

```python
cdp = ctx.new_cdp_session(p)
res = cdp.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
open(out, "wb").write(base64.b64decode(res["data"]))
```

CDP does not wait for fonts or animations.

**3. `--single-process` is required on the VPS.** Without it the browser
starts but the page never paints. The VPS also cannot reach `unpkg.com`
(the ElevenLabs widget), so that request hangs and blocks `load` —
route it to `abort()`.

## The script

`/data/cache/scratch/render_check.py` does the whole thing: screenshot,
plus the computed styles of the elements that matter, so the comparison
is against what the browser resolved rather than what the source appears
to say. `shot2.py` is the CDP-only variant that survives the animation
and font problems.

## Reading the computed styles is the point

The screenshot catches layout. The computed styles catch the rest. On
the Starknet theme, reading the computed values confirmed what the CSS
source only implied:

```
body     background-color: rgb(251, 251, 251)     #FBFBFB
         color:            rgb(12, 12, 79)         #0C0C4F
         font-family:      Inter, "Noto Sans Thai", ...
btn      border-radius:   4px
link     color:            rgb(16, 97, 255)        #1061FF
```

and the palette dump showed only the intended colours were painted
(`rgb(251,251,251)`, `rgb(255,255,255)`, `rgb(237,242,247)`,
`rgb(239,239,239)`, `rgb(12,12,79)`) with no leftover brand blues.
