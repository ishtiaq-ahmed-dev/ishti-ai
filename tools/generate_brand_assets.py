"""Generate official ISHTI brand assets with refined kerning, floating sparkle,
lighter tagline, warmer amber-gold color, and tight high-legibility viewBoxes:

1. FULL WORDMARK + TAGLINE (SVG + High-Res PNG)
2. WORDMARK ONLY (SVG + High-Res PNG)
3. STANDALONE APP ICON (Circle + Squircle, SVG + High-Res PNG)
4. FAVICON (16px-tested ultra-sharp Navy + White Star SVG + PNG + ICO)
"""
import os
import shutil
from fontTools.ttLib import TTFont
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.boundsPen import BoundsPen
from PIL import Image, ImageDraw, ImageFont

# Brand Palette
# Deep luxury midnight navy
NAVY_HEX = "#13243e"
NAVY_RGBA = (19, 36, 62, 255)

# Warmer, richer burnished amber gold (hue 35-36°, avoiding yellow alert tone)
GOLD_HEX = "#ce8418"
GOLD_RGBA = (206, 132, 24, 255)

# Pure white for 16px-safe favicon & dark mode
WHITE_HEX = "#ffffff"
WHITE_RGBA = (255, 255, 255, 255)
TRANSPARENT = (0, 0, 0, 0)


def get_vector_wordmark_paths():
    """Extract and compute mathematically balanced optical kerning for 'ISHTI'."""
    font = TTFont("C:/Windows/Fonts/segoeuib.ttf")
    glyph_set = font.getGlyphSet()
    cmap = font["cmap"].getBestCmap()

    chars = ["I", "S", "H", "T", "I"]
    glyphs = [glyph_set[cmap[ord(c)]] for c in chars]
    bounds = []
    for g in glyphs:
        bp = BoundsPen(glyph_set)
        g.draw(bp)
        bounds.append(bp.bounds)

    TARGET_GAP = 240
    x_offsets = [0]
    for i in range(len(chars) - 1):
        prev_max_x = bounds[i][2] + x_offsets[i]
        gap = TARGET_GAP
        if chars[i] == "H" and chars[i + 1] == "T":
            gap = TARGET_GAP + 20
        elif chars[i] == "T" and chars[i + 1] == "I":
            # Extra optical spacing so T's overhanging crossbar does not crowd the second I
            gap = TARGET_GAP + 50
        next_x = prev_max_x + gap - bounds[i + 1][0]
        x_offsets.append(next_x)

    min_font_x = bounds[0][0] + x_offsets[0]
    font_h = 1434.0  # cap height
    target_h = 54.0
    s = target_h / font_h

    baseline_y = 80.0
    start_svg_x = 16.0

    combined_path = []
    for c, g, xoff in zip(chars, glyphs, x_offsets):
        tx = start_svg_x - min_font_x * s + xoff * s
        ty = baseline_y
        tpen_rec = SVGPathPen(glyph_set)
        tpen = TransformPen(tpen_rec, (s, 0, 0, -s, tx, ty))
        g.draw(tpen)
        combined_path.append(tpen_rec.getCommands())

    full_letter_path = " ".join(combined_path)
    return full_letter_path, chars, x_offsets, min_font_x, s, start_svg_x, baseline_y, target_h


def star_svg_d(cx, cy, R, c=0.25, tip_r=1.2):
    """Generate SVG path d for a thickened 4-point sparkle star."""
    t = tip_r
    return (
        f"M {cx - t:.2f},{cy - R + t:.2f} "
        f"A {t:.2f},{t:.2f} 0 0 1 {cx + t:.2f},{cy - R + t:.2f} "
        f"Q {cx + c*R:.2f},{cy - c*R:.2f} {cx + R - t:.2f},{cy - t:.2f} "
        f"A {t:.2f},{t:.2f} 0 0 1 {cx + R - t:.2f},{cy + t:.2f} "
        f"Q {cx + c*R:.2f},{cy + c*R:.2f} {cx + t:.2f},{cy + R - t:.2f} "
        f"A {t:.2f},{t:.2f} 0 0 1 {cx - t:.2f},{cy + R - t:.2f} "
        f"Q {cx - c*R:.2f},{cy + c*R:.2f} {cx - R + t:.2f},{cy + t:.2f} "
        f"A {t:.2f},{t:.2f} 0 0 1 {cx - R + t:.2f},{cy - t:.2f} "
        f"Q {cx - c*R:.2f},{cy - c*R:.2f} {cx - t:.2f},{cy - R + t:.2f} Z"
    )


def star_polygon_points(cx, cy, R, c=0.25, tip_r=1.5, steps=32):
    """Generate dense anti-aliased polygon points for Pillow rendering."""
    pts = []
    t = tip_r
    quads = [
        ((cx, cy - R + t), (cx + c * R, cy - c * R), (cx + R - t, cy)),
        ((cx + R - t, cy), (cx + c * R, cy + c * R), (cx, cy + R - t)),
        ((cx, cy + R - t), (cx - c * R, cy + c * R), (cx - R + t, cy)),
        ((cx - R + t, cy), (cx - c * R, cy - c * R), (cx, cy - R + t)),
    ]
    for p0, p1, p2 in quads:
        for i in range(steps):
            s = i / steps
            x = (1 - s) ** 2 * p0[0] + 2 * (1 - s) * s * p1[0] + s ** 2 * p2[0]
            y = (1 - s) ** 2 * p0[1] + 2 * (1 - s) * s * p1[1] + s ** 2 * p2[1]
            pts.append((x, y))
    return pts


# ---------------------------------------------------------------------------
# 1. FULL WORDMARK + TAGLINE
# ---------------------------------------------------------------------------
def generate_full_wordmark():
    letter_path, chars, x_offsets, min_font_x, s, start_x, baseline_y, target_h = get_vector_wordmark_paths()
    # Sparkle star: center cx = 212.0 (nudged right), cy = 11.5 (nudged up), R = 11.5
    star_path = star_svg_d(212.0, 11.5, R=11.5, c=0.25, tip_r=1.0)

    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 228 112" width="100%" height="100%" fill="none">
  <!-- ISHTI Full Wordmark + Tagline -->
  <!-- Floating Sparkle Star nudged up-right of second 'I' -->
  <path d="{star_path}" fill="{GOLD_HEX}" />
  <!-- ISHTI Wordmark with Optical Kerning -->
  <path d="{letter_path}" fill="{NAVY_HEX}" />
  <!-- Refined Tagline: Lighter weight, elegant architectural tracking -->
  <text x="16" y="102" font-family="system-ui, -apple-system, 'Segoe UI', Inter, Roboto, sans-serif" font-size="10" font-weight="500" fill="{NAVY_HEX}" fill-opacity="0.85" letter-spacing="0.38em">PERSONAL AI ASSISTANT</text>
</svg>"""

    # High-Res Master PNG (super-sampled 6x for 1368x672 crisp output)
    scale = 6
    W, H = 228 * scale, 112 * scale
    im = Image.new("RGBA", (W, H), TRANSPARENT)
    draw = ImageDraw.Draw(im)

    font_main = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", int(target_h * scale))
    font_sub = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", int(10 * scale))

    # Star
    star_pts = star_polygon_points(212.0 * scale, 11.5 * scale, 11.5 * scale, c=0.25, tip_r=1.0 * scale)
    draw.polygon(star_pts, fill=GOLD_RGBA)

    # Letters
    for c, xoff in zip(chars, x_offsets):
        lx = (start_x - min_font_x * s + xoff * s) * scale
        ly = (baseline_y - 1434.0 * s) * scale
        draw.text((lx, ly), c, font=font_main, fill=NAVY_RGBA)

    # Tagline
    tagline = "PERSONAL AI ASSISTANT"
    ch_w = [draw.textbbox((0, 0), ch, font=font_sub)[2] for ch in tagline]
    total_w = 189.83 * scale
    spacing = (total_w - sum(ch_w)) / (len(tagline) - 1)
    cur_x = 16.0 * scale
    cur_y = 90.0 * scale
    for ch, cw in zip(tagline, ch_w):
        draw.text((cur_x, cur_y), ch, font=font_sub, fill=(19, 36, 62, 215))
        cur_x += cw + spacing

    out_png = im.resize((1368, 672), Image.Resampling.LANCZOS)
    return svg_content, out_png


# ---------------------------------------------------------------------------
# 2. WORDMARK ONLY (no tagline)
# ---------------------------------------------------------------------------
def generate_wordmark_only():
    letter_path, chars, x_offsets, min_font_x, s, start_x, baseline_y, target_h = get_vector_wordmark_paths()
    star_path = star_svg_d(212.0, 11.5, R=11.5, c=0.25, tip_r=1.0)

    # Tight viewBox: height 88
    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 228 88" width="100%" height="100%" fill="none">
  <!-- ISHTI Wordmark Only (No Tagline) -->
  <path d="{star_path}" fill="{GOLD_HEX}" />
  <path d="{letter_path}" fill="{NAVY_HEX}" />
</svg>"""

    scale = 6
    W, H = 228 * scale, 88 * scale
    im = Image.new("RGBA", (W, H), TRANSPARENT)
    draw = ImageDraw.Draw(im)

    font_main = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", int(target_h * scale))
    star_pts = star_polygon_points(212.0 * scale, 11.5 * scale, 11.5 * scale, c=0.25, tip_r=1.0 * scale)
    draw.polygon(star_pts, fill=GOLD_RGBA)

    for c, xoff in zip(chars, x_offsets):
        lx = (start_x - min_font_x * s + xoff * s) * scale
        ly = (baseline_y - 1434.0 * s) * scale
        draw.text((lx, ly), c, font=font_main, fill=NAVY_RGBA)

    out_png = im.resize((1368, 528), Image.Resampling.LANCZOS)
    return svg_content, out_png


# ---------------------------------------------------------------------------
# 3. STANDALONE APP ICON
# ---------------------------------------------------------------------------
def generate_standalone_icon():
    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <!-- ISHTI Standalone App Icon (Navy Circle + Warm Amber Gold Star) -->
  <circle cx="256" cy="256" r="236" fill="{NAVY_HEX}" />
  <path d="{star_svg_d(256, 256, 126, c=0.24, tip_r=5.5)}" fill="{GOLD_HEX}" />
</svg>"""

    # Squircle (rounded app icon) variant
    squircle_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <!-- ISHTI Standalone App Icon (Squircle / App Tile) -->
  <rect x="24" y="24" width="464" height="464" rx="108" fill="{NAVY_HEX}" />
  <path d="{star_svg_d(256, 256, 126, c=0.24, tip_r=5.5)}" fill="{GOLD_HEX}" />
</svg>"""

    canvas_size = 2048
    im = Image.new("RGBA", (canvas_size, canvas_size), TRANSPARENT)
    draw = ImageDraw.Draw(im)
    draw.ellipse([(96, 96), (canvas_size - 96, canvas_size - 96)], fill=NAVY_RGBA)
    pts = star_polygon_points(canvas_size // 2, canvas_size // 2, 504, c=0.24, tip_r=22.0)
    draw.polygon(pts, fill=GOLD_RGBA)

    out_png = im.resize((1024, 1024), Image.Resampling.LANCZOS)
    return svg_content, squircle_svg, out_png


# ---------------------------------------------------------------------------
# 4. FAVICON (16px-tested ultra-sharp)
# ---------------------------------------------------------------------------
def generate_favicon():
    # 32x32 viewbox with bold white star on navy circle for crisp tab clarity
    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="100%" height="100%" fill="none">
  <!-- ISHTI Favicon (16px-Safe Browser Icon) -->
  <circle cx="16" cy="16" r="15" fill="{NAVY_HEX}" />
  <path d="{star_svg_d(16, 16, 11.2, c=0.30, tip_r=0.8)}" fill="{WHITE_HEX}" />
</svg>"""

    # Master render
    im512 = Image.new("RGBA", (512, 512), TRANSPARENT)
    draw = ImageDraw.Draw(im512)
    draw.ellipse([(16, 16), (496, 496)], fill=NAVY_RGBA)
    pts = star_polygon_points(256, 256, 179, c=0.30, tip_r=13.0)
    draw.polygon(pts, fill=WHITE_RGBA)

    out_128 = im512.resize((128, 128), Image.Resampling.LANCZOS)
    out_32 = im512.resize((32, 32), Image.Resampling.LANCZOS)
    out_16 = im512.resize((16, 16), Image.Resampling.LANCZOS)

    return svg_content, out_128, out_32, out_16


def main():
    os.makedirs("assets", exist_ok=True)
    os.makedirs("interface/app/public", exist_ok=True)
    os.makedirs("interface/web_ui/static", exist_ok=True)

    print("Generating 1. FULL WORDMARK + TAGLINE...")
    full_svg, full_png = generate_full_wordmark()
    with open("assets/ishti_full_wordmark.svg", "w", encoding="utf-8") as f:
        f.write(full_svg)
    full_png.save("assets/ishti_full_wordmark.png", "PNG")

    print("Generating 2. WORDMARK ONLY...")
    wm_svg, wm_png = generate_wordmark_only()
    with open("assets/ishti_wordmark.svg", "w", encoding="utf-8") as f:
        f.write(wm_svg)
    wm_png.save("assets/ishti_wordmark.png", "PNG")

    print("Generating 3. STANDALONE APP ICON...")
    icon_svg, squircle_svg, icon_png = generate_standalone_icon()
    with open("assets/ishti_app_icon.svg", "w", encoding="utf-8") as f:
        f.write(icon_svg)
    with open("assets/ishti_app_icon_squircle.svg", "w", encoding="utf-8") as f:
        f.write(squircle_svg)
    icon_png.save("assets/ishti_app_icon.png", "PNG")
    icon_png.resize((512, 512), Image.Resampling.LANCZOS).save("assets/ishti_app_icon_512.png")
    icon_png.resize((192, 192), Image.Resampling.LANCZOS).save("assets/ishti_app_icon_192.png")

    print("Generating 4. FAVICON (16px-tested)...")
    fav_svg, fav_128, fav_32, fav_16 = generate_favicon()
    with open("assets/ishti_favicon.svg", "w", encoding="utf-8") as f:
        f.write(fav_svg)
    fav_128.save("assets/ishti_favicon.png", "PNG")
    fav_32.save("assets/ishti_favicon_32.png", "PNG")
    fav_16.save("assets/ishti_favicon_16.png", "PNG")
    fav_128.save("assets/favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])

    print("Deploying assets to interface/app/public/ and interface/web_ui/static/...")
    with open("interface/app/public/favicon.svg", "w", encoding="utf-8") as f:
        f.write(fav_svg)
    with open("interface/app/public/ishti-icon.svg", "w", encoding="utf-8") as f:
        f.write(icon_svg)
    with open("interface/app/public/ishti-logo.svg", "w", encoding="utf-8") as f:
        f.write(full_svg)
    with open("interface/app/public/ishti-wordmark.svg", "w", encoding="utf-8") as f:
        f.write(wm_svg)

    fav_128.save("interface/app/public/favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    fav_32.save("interface/app/public/favicon.png", "PNG")
    icon_png.resize((192, 192), Image.Resampling.LANCZOS).save("interface/app/public/ishti-icon.png", "PNG")
    full_png.save("interface/app/public/ishti-logo.png", "PNG")

    with open("interface/web_ui/static/favicon.svg", "w", encoding="utf-8") as f:
        f.write(fav_svg)
    full_png.save("interface/web_ui/static/ishti_logo.png", "PNG")

    print("[SUCCESS] All brand assets updated and deployed successfully!")


if __name__ == "__main__":
    main()
