from PIL import Image, ImageDraw, ImageFilter, ImageFont
import math, os

HERE = os.path.dirname(os.path.abspath(__file__))
F = lambda w, s: ImageFont.truetype(os.path.join(HERE, f"Poppins-{w}.ttf"), s)

NAVY = (21, 22, 76)
NAVY_DK = (11, 13, 42)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def radial_bg(w, h, cx, cy, c0, c1, r):
    """Radial gradient background."""
    img = Image.new("RGB", (w, h), c1)
    px = img.load()
    for y in range(h):
        for x in range(w):
            d = math.hypot(x - cx, y - cy) / r
            t = min(1.0, d)
            px[x, y] = lerp(c0, c1, t ** 0.9)
    return img


def orb(d, hot=(0.36, 0.28)):
    """Glossy orb d×d RGBA. Hotspot upper-left."""
    s = 4  # supersample
    D = d * s
    im = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    px = im.load()
    hx, hy = hot[0] * D, hot[1] * D
    R = D / 2
    stops = [(0.0, (191, 224, 255)), (0.28, (111, 174, 255)), (0.55, (76, 125, 240)),
             (0.85, (108, 92, 231)), (1.0, (60, 47, 168))]
    maxd = math.hypot(max(hx, D - hx), max(hy, D - hy))
    for y in range(D):
        for x in range(D):
            if math.hypot(x - R, y - R) <= R:
                t = math.hypot(x - hx, y - hy) / maxd
                for i in range(len(stops) - 1):
                    if stops[i][0] <= t <= stops[i + 1][0]:
                        tt = (t - stops[i][0]) / (stops[i + 1][0] - stops[i][0])
                        c = lerp(stops[i][1], stops[i + 1][1], tt)
                        break
                px[x, y] = (*c, 255)
    # inner soft highlight
    hl = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.ellipse([hx - R * 0.55, hy - R * 0.55, hx + R * 0.55, hy + R * 0.55], fill=(255, 255, 255, 60))
    hl = hl.filter(ImageFilter.GaussianBlur(R * 0.35))
    mask = Image.new("L", (D, D), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, D - 1, D - 1], fill=255)
    hl.putalpha(Image.eval(hl.split()[3], lambda a: a).point(lambda v: v) )
    im = Image.alpha_composite(im, Image.composite(hl, Image.new("RGBA", (D, D), (0, 0, 0, 0)), mask))
    return im.resize((d, d), Image.LANCZOS)


def waveform(draw, cx, cy, scale=1.0, color=(255, 255, 255, 255)):
    """5 rounded bars: 35/60/90/60/35 at scale=1 (orb 380px)."""
    w, gap = 12 * scale, 14 * scale
    hs = [35, 60, 90, 60, 35]
    total = 5 * w + 4 * gap
    x = cx - total / 2
    for h in hs:
        h = h * scale
        draw.rounded_rectangle([x, cy - h / 2, x + w, cy + h / 2], radius=w / 2, fill=color)
        x += w + gap


def glow_layer(w, h, cx, cy, r, color=(74, 92, 216), alpha=150, blur=None):
    g = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, alpha))
    return g.filter(ImageFilter.GaussianBlur(blur or r * 0.45))


WAVE_SCALE = 1.0

def place_orb(base, cx, cy, d):
    base = base.convert("RGBA")
    base = Image.alpha_composite(base, glow_layer(*base.size, cx, cy, d * 0.75))
    o = orb(d)
    base.alpha_composite(o, (int(cx - d / 2), int(cy - d / 2)))
    dr = ImageDraw.Draw(base)
    waveform(dr, cx, cy, scale=d / 380 * WAVE_SCALE)
    return base


def main():
    global WAVE_SCALE
    WAVE_SCALE = 1.45
    # ---------- AVATAR 400x400 (render 800 then downscale) ----------
    S = 800
    av = radial_bg(S, S, S * 0.5, S * 0.42, (34, 35, 110), NAVY_DK, S * 0.75)
    av = place_orb(av, S / 2, S / 2, int(S * 0.84))
    av = av.convert("RGB").resize((400, 400), Image.LANCZOS)
    av.save(os.path.join(HERE, "avatar_400.png"))
    # also a 2x for crispness
    av2 = radial_bg(S, S, S * 0.5, S * 0.42, (34, 35, 110), NAVY_DK, S * 0.75)
    av2 = place_orb(av2, S / 2, S / 2, int(S * 0.84)).convert("RGB")
    av2.save(os.path.join(HERE, "avatar_800.png"))

    WAVE_SCALE = 1.0
    # ---------- BANNER 1500x500 (render 3000x1000) ----------
    W, H = 3000, 1000
    bn = radial_bg(W, H, W * 0.26, H * 0.5, (34, 35, 110), NAVY_DK, W * 0.55)
    bn = place_orb(bn, W * 0.26, H * 0.5, 560)
    dr = ImageDraw.Draw(bn)

    # Text block right of orb
    tx = W * 0.47
    title = "Say it. Confirm it. Settled."
    f_title = F("Bold", 112)
    f_sub = F("Regular", 48)
    f_chip = F("Medium", 36)

    dr.text((tx, H * 0.5 - 150), title, font=f_title, fill=(255, 255, 255))
    sub = "Voice-first finance agent · USDC on Avalanche · 90+ languages"
    dr.text((tx, H * 0.5 - 10), sub, font=f_sub, fill=(179, 181, 217))

    # chips
    chips = ["ElevenLabs", "Ubot", "Avalanche", "x402 agent rail"]
    cx = tx
    cy = H * 0.5 + 95
    ov = Image.new("RGBA", bn.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for c in chips:
        tw = od.textlength(c, font=f_chip)
        pad = 34
        box = [cx, cy, cx + tw + pad * 2, cy + 72]
        od.rounded_rectangle(box, radius=36, fill=(255, 255, 255, 28), outline=(255, 255, 255, 70), width=2)
        od.text((cx + pad, cy + 12), c, font=f_chip, fill=(255, 255, 255, 235))
        cx += tw + pad * 2 + 24
    bn = Image.alpha_composite(bn, ov)
    dr = ImageDraw.Draw(bn)

    # bottom-right url
    f_url = F("Medium", 40)
    u = "ubotpay.com"
    uw = dr.textlength(u, font=f_url)
    dr.text((W - uw - 90, H - 110), u, font=f_url, fill=(190, 206, 255))

    bn = bn.convert("RGB").resize((1500, 500), Image.LANCZOS)
    bn.save(os.path.join(HERE, "banner_1500x500.png"))
    print("done")


if __name__ == "__main__":
    main()
