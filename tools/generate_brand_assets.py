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
    # Sparkle star: centered directly above second 'I' like a tittle (dot)
    star_path = star_svg_d(199.75, 11.5, R=11.5, c=0.25, tip_r=1.0)

    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 228 112" width="100%" height="100%" fill="none">
  <!-- ISHTI Full Wordmark + Tagline -->
  <!-- Sparkle Star centered directly above second 'I' -->
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

    # Star centered directly above second 'I'
    star_pts = star_polygon_points(199.75 * scale, 11.5 * scale, 11.5 * scale, c=0.25, tip_r=1.0 * scale)
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
    star_path = star_svg_d(199.75, 11.5, R=11.5, c=0.25, tip_r=1.0)

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
    star_pts = star_polygon_points(199.75 * scale, 11.5 * scale, 11.5 * scale, c=0.25, tip_r=1.0 * scale)
    draw.polygon(star_pts, fill=GOLD_RGBA)

    for c, xoff in zip(chars, x_offsets):
        lx = (start_x - min_font_x * s + xoff * s) * scale
        ly = (baseline_y - 1434.0 * s) * scale
        draw.text((lx, ly), c, font=font_main, fill=NAVY_RGBA)

    out_png = im.resize((1368, 528), Image.Resampling.LANCZOS)
    return svg_content, out_png


# ---------------------------------------------------------------------------
# 3. STANDALONE APP ICON (Circle + Squircle with Round Corners)
# ---------------------------------------------------------------------------
def generate_standalone_icon():
    circle_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <!-- ISHTI Standalone App Icon (Navy Circle + Warm Amber Gold Star) -->
  <circle cx="256" cy="256" r="236" fill="{NAVY_HEX}" />
  <path d="{star_svg_d(256, 256, 126, c=0.24, tip_r=5.5)}" fill="{GOLD_HEX}" />
</svg>"""

    # Squircle (rounded app icon) variant: perfectly square with smooth round corners (rx=108, ~22% radius)
    squircle_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <!-- ISHTI Standalone App Icon (Squircle / Round Corners App Tile) -->
  <rect x="24" y="24" width="464" height="464" rx="108" fill="{NAVY_HEX}" />
  <path d="{star_svg_d(256, 256, 126, c=0.24, tip_r=5.5)}" fill="{GOLD_HEX}" />
</svg>"""

    canvas_size = 2048
    # 1. Circle Icon PNG
    im_circle = Image.new("RGBA", (canvas_size, canvas_size), TRANSPARENT)
    draw_c = ImageDraw.Draw(im_circle)
    draw_c.ellipse([(96, 96), (canvas_size - 96, canvas_size - 96)], fill=NAVY_RGBA)
    pts_c = star_polygon_points(canvas_size // 2, canvas_size // 2, 504, c=0.24, tip_r=22.0)
    draw_c.polygon(pts_c, fill=GOLD_RGBA)
    out_circle_png = im_circle.resize((1024, 1024), Image.Resampling.LANCZOS)

    # 2. Squircle Icon PNG (Master 1024x1024 with transparent background outside round corners)
    im_squircle = Image.new("RGBA", (canvas_size, canvas_size), TRANSPARENT)
    draw_s = ImageDraw.Draw(im_squircle)
    pad = 96
    corner_r = int((canvas_size - 2 * pad) * 0.233)  # ~432px on 1856px tile (rx=108 on 512)
    draw_s.rounded_rectangle([pad, pad, canvas_size - pad, canvas_size - pad], radius=corner_r, fill=NAVY_RGBA)
    pts_s = star_polygon_points(canvas_size // 2, canvas_size // 2, 504, c=0.24, tip_r=22.0)
    draw_s.polygon(pts_s, fill=GOLD_RGBA)
    out_squircle_png = im_squircle.resize((1024, 1024), Image.Resampling.LANCZOS)

    return circle_svg, squircle_svg, out_circle_png, out_squircle_png


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


# ---------------------------------------------------------------------------
# 5. GITHUB & HEADER BRAND LOGO (Rounded Squircle Icon + Wordmark + Tagline)
# ---------------------------------------------------------------------------
def generate_readme_brand_logos():
    """Generate the official horizontal lockup logo for GitHub README:
    Icon tile on the left with round corners + ISHTI bold wordmark + Tagline on the right.
    """
    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 140" width="100%" height="100%" fill="none">
  <defs>
    <style>
      .brand-title {{ font-family: system-ui, -apple-system, 'Segoe UI', Inter, sans-serif; font-weight: 800; font-size: 58px; letter-spacing: 0.04em; }}
      .brand-star {{ fill: {GOLD_HEX}; }}
      .brand-sub {{ font-family: system-ui, -apple-system, 'Segoe UI', Inter, sans-serif; font-weight: 700; font-size: 13.5px; letter-spacing: 0.28em; }}
      .brand-text-theme {{ fill: {NAVY_HEX}; }}
      @media (prefers-color-scheme: dark) {{
        .brand-text-theme {{ fill: #f8fafc; }}
      }}
    </style>
  </defs>
  <!-- ISHTI Round-Cornered Squircle App Icon -->
  <g transform="translate(10, 10)">
    <rect x="0" y="0" width="120" height="120" rx="28" fill="{NAVY_HEX}" />
    <path d="{star_svg_d(60, 60, 32.5, c=0.24, tip_r=1.5)}" fill="{GOLD_HEX}" />
  </g>
  <!-- ISHTI Wordmark with Accent Star Directly Above I -->
  <g transform="translate(154, 76)">
    <text x="0" y="0" class="brand-title brand-text-theme">ISHTI</text>
    <path d="{star_svg_d(147.5, -56, 11, c=0.25, tip_r=1.0)}" class="brand-star" />
  </g>
  <!-- Tagline -->
  <g transform="translate(156, 110)">
    <text x="0" y="0" class="brand-sub brand-text-theme" opacity="0.82">PERSONAL AI ASSISTANT</text>
  </g>
</svg>"""

    # Render High-DPI PNGs (2x supersampled: 1080 x 280)
    W, H = 1080, 280
    font_main = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 116)
    font_sub = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 27)

    def _render_png(dark: bool):
        im = Image.new("RGBA", (W, H), TRANSPARENT)
        draw = ImageDraw.Draw(im)

        # 1. Squircle Icon on the left
        pad = 20
        tile_size = 240
        radius = int(tile_size * 0.233)  # 56px radius
        draw.rounded_rectangle([pad, pad, pad + tile_size, pad + tile_size], radius=radius, fill=NAVY_RGBA)

        # Star in center of tile: center = (pad + tile_size//2, pad + tile_size//2) = (140, 140), R = 65
        star_pts = star_polygon_points(pad + tile_size // 2, pad + tile_size // 2, 65, c=0.24, tip_r=3.0)
        draw.polygon(star_pts, fill=GOLD_RGBA)

        # 2. Text on the right
        text_color = (248, 250, 252, 255) if dark else NAVY_RGBA
        sub_color = (226, 232, 240, 215) if dark else (19, 36, 62, 215)

        tx = 308
        ty = 38
        draw.text((tx, ty), "ISHTI", font=font_main, fill=text_color)

        # Gold star centered directly above final I (center = tx + 277.5, cy = 56)
        accent_pts = star_polygon_points(tx + 277.5, 56, 22, c=0.25, tip_r=2.0)
        draw.polygon(accent_pts, fill=GOLD_RGBA)

        # Tagline with letter spacing
        tagline = "PERSONAL AI ASSISTANT"
        ch_w = [draw.textbbox((0, 0), ch, font=font_sub)[2] for ch in tagline]
        target_w = 480
        spacing = (target_w - sum(ch_w)) / (len(tagline) - 1)
        sx = tx + 4
        sy = 194
        for ch, cw in zip(tagline, ch_w):
            draw.text((sx, sy), ch, font=font_sub, fill=sub_color)
            sx += cw + spacing

        return im

    png_light = _render_png(dark=False)
    png_dark = _render_png(dark=True)
    return svg_content, png_light, png_dark


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

    print("Generating 3. STANDALONE APP ICON (Circle + Squircle with Round Corners)...")
    circle_svg, squircle_svg, circle_png, squircle_png = generate_standalone_icon()
    with open("assets/ishti_app_icon.svg", "w", encoding="utf-8") as f:
        f.write(circle_svg)
    with open("assets/ishti_app_icon_squircle.svg", "w", encoding="utf-8") as f:
        f.write(squircle_svg)

    circle_png.save("assets/ishti_app_icon_circle.png", "PNG")
    squircle_png.save("assets/ishti_app_icon.png", "PNG")
    squircle_png.save("assets/ishti_app_icon_squircle.png", "PNG")
    squircle_png.resize((512, 512), Image.Resampling.LANCZOS).save("assets/ishti_app_icon_512.png")
    squircle_png.resize((192, 192), Image.Resampling.LANCZOS).save("assets/ishti_app_icon_192.png")

    print("Generating 4. WINDOWS APP ICO (Multi-resolution 16-256px with Round Corners)...")
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    # Save the true multi-resolution squircle icon with transparent rounded corners!
    squircle_png.save("assets/ishti.ico", format="ICO", sizes=ico_sizes)

    print("Generating 5. FAVICON (16px-tested)...")
    fav_svg, fav_128, fav_32, fav_16 = generate_favicon()
    with open("assets/ishti_favicon.svg", "w", encoding="utf-8") as f:
        f.write(fav_svg)
    fav_128.save("assets/ishti_favicon.png", "PNG")
    fav_32.save("assets/ishti_favicon_32.png", "PNG")
    fav_16.save("assets/ishti_favicon_16.png", "PNG")
    fav_128.save("assets/favicon.ico", format="ICO", sizes=ico_sizes)

    print("Generating 6. GITHUB & HEADER BRAND LOGOS...")
    readme_svg, readme_light_png, readme_dark_png = generate_readme_brand_logos()
    with open("assets/ishti_logo.svg", "w", encoding="utf-8") as f:
        f.write(readme_svg)
    readme_light_png.save("assets/ishti_logo.png", "PNG")
    readme_dark_png.save("assets/ishti_logo_dark.png", "PNG")

    print("Deploying assets to interface/app/public/ and interface/web_ui/static/...")
    with open("interface/app/public/favicon.svg", "w", encoding="utf-8") as f:
        f.write(fav_svg)
    with open("interface/app/public/ishti-icon.svg", "w", encoding="utf-8") as f:
        f.write(squircle_svg)
    with open("interface/app/public/ishti-logo.svg", "w", encoding="utf-8") as f:
        f.write(readme_svg)
    with open("interface/app/public/ishti-wordmark.svg", "w", encoding="utf-8") as f:
        f.write(wm_svg)

    squircle_png.save("interface/app/public/favicon.ico", format="ICO", sizes=ico_sizes)
    fav_32.save("interface/app/public/favicon.png", "PNG")
    squircle_png.resize((192, 192), Image.Resampling.LANCZOS).save("interface/app/public/ishti-icon.png", "PNG")
    readme_light_png.save("interface/app/public/ishti-logo.png", "PNG")
    readme_dark_png.save("interface/app/public/ishti-logo-dark.png", "PNG")

    with open("interface/web_ui/static/favicon.svg", "w", encoding="utf-8") as f:
        f.write(fav_svg)
    readme_light_png.save("interface/web_ui/static/ishti_logo.png", "PNG")
    squircle_png.resize((512, 512), Image.Resampling.LANCZOS).save("interface/web_ui/static/ishti_app_icon.png", "PNG")

    print("[SUCCESS] All brand assets updated, perfectly rounded, and deployed successfully!")


if __name__ == "__main__":
    main()

