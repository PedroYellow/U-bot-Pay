"""YouTube channel art for ubotpay: 2560x1440, safe area 1546x423 centered."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw, ImageFilter
from make_assets import radial_bg, place_orb, F, NAVY_DK

W, H = 2560, 1440
SAFE_W, SAFE_H = 1546, 423
sx, sy = (W - SAFE_W) // 2, (H - SAFE_H) // 2  # 507, 508

bg = radial_bg(W, H, W * 0.5, H * 0.5, (34, 35, 110), NAVY_DK, W * 0.5)
# subtle wide glow band behind safe area so TV crop still looks designed
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse([W*0.5-900, H*0.5-320, W*0.5+900, H*0.5+320], fill=(60, 70, 190, 70))
bg = Image.alpha_composite(bg.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(160)))
# decorative giant waveform bars (very low opacity) for the TV-only region
deco = Image.new("RGBA", (W, H), (0, 0, 0, 0)); dd = ImageDraw.Draw(deco)
bar_w, gap = 56, 44; hs = [0.22, 0.38, 0.55, 0.72, 0.88, 1.0, 0.88, 0.72, 0.55, 0.38, 0.22]
total = len(hs) * bar_w + (len(hs) - 1) * gap
for side in (-1, 1):
    x0 = W / 2 + side * 1120 - total / 2
    for i, h in enumerate(hs):
        bh = h * 760
        dd.rounded_rectangle([x0 + i * (bar_w + gap), H / 2 - bh / 2, x0 + i * (bar_w + gap) + bar_w, H / 2 + bh / 2], radius=bar_w / 2, fill=(120, 150, 255, 22))
bg = Image.alpha_composite(bg, deco.filter(ImageFilter.GaussianBlur(2)))
# corner light blobs
blob = Image.new("RGBA", (W, H), (0, 0, 0, 0)); bd = ImageDraw.Draw(blob)
bd.ellipse([-300, -300, 700, 500], fill=(108, 92, 231, 70)); bd.ellipse([W - 700, H - 500, W + 300, H + 300], fill=(76, 125, 240, 70))
bg = Image.alpha_composite(bg, blob.filter(ImageFilter.GaussianBlur(220)))

# orb on the left of the safe area
ORB = 280
ocx = sx + 60 + ORB / 2
ocy = H / 2 - 14
bg = place_orb(bg, ocx, ocy, ORB)
d = ImageDraw.Draw(bg)

# text block
tx = ocx + ORB / 2 + 70
f_brand = F("Bold", 54)
f_title = F("Bold", 76)
f_sub = F("Regular", 36)
f_chip = F("Medium", 28)

d.text((tx, ocy - 150), "ubotpay", font=f_brand, fill=(157, 196, 255))
d.text((tx, ocy - 94), "Say it. Confirm it. Settled.", font=f_title, fill=(255, 255, 255))
d.text((tx, ocy + 4), "Voice-first USDC payments on Avalanche · 90+ languages", font=f_sub, fill=(205, 210, 240))

# chips
ov = Image.new("RGBA", bg.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
cx, cy = tx, ocy + 64
for c in ["ElevenLabs", "Ubot", "Avalanche", "x402 agent rail"]:
    tw = od.textlength(c, font=f_chip); pad = 24
    od.rounded_rectangle([cx, cy, cx + tw + pad*2, cy + 54], radius=27, fill=(255, 255, 255, 40), outline=(255, 255, 255, 110), width=2)
    od.text((cx + pad, cy + 11), c, font=f_chip, fill=(255, 255, 255, 235))
    cx += tw + pad*2 + 16
bg = Image.alpha_composite(bg, ov)
d = ImageDraw.Draw(bg)

# socials bottom-right inside safe area
f_s = F("Medium", 34)
s = "@Ubotpay · t.me/Ubotpaybot · ubotpay.com"
sw = d.textlength(s, font=f_s)
d.text((tx, ocy + 128), s, font=f_s, fill=(190, 206, 255))

out = bg.convert("RGB")
out.save(os.path.join(os.path.dirname(__file__), "youtube_banner_2560x1440.png"), optimize=True)

# preview with safe-area guide
pv = out.copy(); pd = ImageDraw.Draw(pv)
pd.rectangle([sx, sy, sx + SAFE_W, sy + SAFE_H], outline=(255, 80, 80), width=4)
pd.rectangle([0, (H-1152)//2, W, (H+1152)//2], outline=(255, 200, 80), width=3)  # desktop 2560x1152
pv.resize((1280, 720)).save(os.path.join(os.path.dirname(__file__), "youtube_banner_preview.png"))
print("ok", out.size, os.path.getsize(os.path.join(os.path.dirname(__file__), "youtube_banner_2560x1440.png")) // 1024, "KB")
