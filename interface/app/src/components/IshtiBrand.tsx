import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

type BrandVariant = "full" | "wordmark" | "icon" | "favicon" | "lockup" | "squircle";

export type IshtiBrandProps = Omit<ComponentProps<"svg">, "width" | "height"> & {
  variant?: BrandVariant;
  shape?: "squircle" | "circle";
  size?: number | string;
  width?: number | string;
  height?: number | string;
  themeAdaptive?: boolean;
  title?: string;
};

// Official Warm Amber-Gold (hue ~35°, rich & premium, avoiding warning/caution yellow)
const GOLD_COLOR = "#ce8418";
const NAVY_COLOR = "#13243e";

// Mathematically kerned vector glyph paths for "ISHTI" (even optical gaps across all letters)
const ISHTI_VECTOR_PATH =
  "M28.16 80.0H16.0V26.0H28.16Z " +
  "M37.43 77.93V65.88Q40.70 68.63 44.54 70.00Q48.38 71.38 52.30 71.38Q54.60 71.38 56.31 70.96Q58.03 70.55 59.17 69.81Q60.32 69.08 60.89 68.08Q61.45 67.08 61.45 65.92Q61.45 64.33 60.55 63.09Q59.64 61.85 58.08 60.79Q56.52 59.74 54.37 58.76Q52.23 57.78 49.74 56.77Q43.41 54.13 40.31 50.33Q37.20 46.52 37.20 41.14Q37.20 36.92 38.90 33.89Q40.59 30.86 43.51 28.90Q46.43 26.94 50.27 26.02Q54.11 25.10 58.40 25.10Q62.62 25.10 65.88 25.60Q69.13 26.11 71.88 27.17V38.43Q70.53 37.49 68.93 36.77Q67.33 36.05 65.63 35.58Q63.94 35.11 62.26 34.89Q60.59 34.66 59.08 34.66Q57.01 34.66 55.31 35.06Q53.62 35.45 52.45 36.17Q51.28 36.88 50.64 37.88Q50.00 38.88 50.00 40.12Q50.00 41.48 50.72 42.55Q51.44 43.62 52.75 44.58Q54.07 45.54 55.95 46.47Q57.84 47.39 60.21 48.37Q63.45 49.72 66.03 51.25Q68.61 52.77 70.45 54.69Q72.30 56.62 73.28 59.08Q74.26 61.55 74.26 64.82Q74.26 69.34 72.54 72.41Q70.83 75.48 67.89 77.38Q64.95 79.28 61.06 80.11Q57.16 80.94 52.83 80.94Q48.38 80.94 44.37 80.19Q40.36 79.44 37.43 77.93Z " +
  "M130.03 80.0H117.82V58.01H95.46V80.0H83.29V26.0H95.46V47.54H117.82V26.0H130.03Z " +
  "M182.74 35.90H167.34V80.0H155.14V35.90H139.82V26.0H182.74Z " +
  "M205.83 80.0H193.67V26.0H205.83Z";

// Sparkle star centered exactly above the 2nd 'I' like a tittle / dot
const FLOATING_SPARKLE_PATH =
  "M 198.75,1.00 A 1.00,1.00 0 0 1 200.75,1.00 " +
  "Q 202.62,8.62 210.25,10.50 A 1.00,1.00 0 0 1 210.25,12.50 " +
  "Q 202.62,14.38 200.75,22.00 A 1.00,1.00 0 0 1 198.75,22.00 " +
  "Q 196.88,14.38 189.25,12.50 A 1.00,1.00 0 0 1 189.25,10.50 " +
  "Q 196.88,8.62 198.75,1.00 Z";

// Centered sparkle star for app icon (R=126 in 512x512)
const APP_ICON_SPARKLE_PATH =
  "M 250.50,135.50 A 5.50,5.50 0 0 1 261.50,135.50 " +
  "Q 286.24,225.76 376.50,250.50 A 5.50,5.50 0 0 1 376.50,261.50 " +
  "Q 286.24,286.24 261.50,376.50 A 5.50,5.50 0 0 1 250.50,376.50 " +
  "Q 225.76,286.24 135.50,261.50 A 5.50,5.50 0 0 1 135.50,250.50 " +
  "Q 225.76,225.76 250.50,135.50 Z";

export function IshtiBrand({
  variant = "full",
  shape = "squircle",
  size,
  width,
  height,
  themeAdaptive = true,
  title,
  className,
  style,
  ...props
}: IshtiBrandProps) {
  // Theme adaptive fill: solid navy on light mode, pure crisp white on dark mode via CSS variables
  const textFill = themeAdaptive ? "var(--brand-text, #13243e)" : NAVY_COLOR;
  const taglineFill = themeAdaptive ? "var(--brand-text, #13243e)" : NAVY_COLOR;

  // Dimensions resolution
  let resolvedWidth: number | string | undefined = width;
  let resolvedHeight: number | string | undefined = height;

  if (variant === "icon" || variant === "favicon" || variant === "squircle") {
    const s = size ?? width ?? height ?? (variant === "favicon" ? 24 : 36);
    resolvedWidth = s;
    resolvedHeight = s;
  } else if (variant === "wordmark") {
    // 228 x 88 (aspect ratio 228 / 88 ≈ 2.5909)
    if (resolvedHeight !== undefined && resolvedWidth === undefined) {
      resolvedWidth = typeof resolvedHeight === "number" ? Math.round(resolvedHeight * (228 / 88)) : "auto";
    } else if (resolvedWidth !== undefined && resolvedHeight === undefined) {
      resolvedHeight = typeof resolvedWidth === "number" ? Math.round(resolvedWidth * (88 / 228)) : "auto";
    } else if (size !== undefined && resolvedWidth === undefined && resolvedHeight === undefined) {
      if (typeof size === "number") {
        resolvedHeight = size;
        resolvedWidth = Math.round(size * (228 / 88));
      } else {
        resolvedHeight = size;
        resolvedWidth = "auto";
      }
    } else if (resolvedWidth === undefined && resolvedHeight === undefined) {
      resolvedHeight = 28;
      resolvedWidth = Math.round(28 * (228 / 88)); // 73px
    }
  } else if (variant === "full") {
    // 228 x 112 (aspect ratio 228 / 112 ≈ 2.0357)
    if (resolvedHeight !== undefined && resolvedWidth === undefined) {
      resolvedWidth = typeof resolvedHeight === "number" ? Math.round(resolvedHeight * (228 / 112)) : "auto";
    } else if (resolvedWidth !== undefined && resolvedHeight === undefined) {
      resolvedHeight = typeof resolvedWidth === "number" ? Math.round(resolvedWidth * (112 / 228)) : "auto";
    } else if (size !== undefined && resolvedWidth === undefined && resolvedHeight === undefined) {
      if (typeof size === "number") {
        if (size >= 80) {
          resolvedWidth = size;
          resolvedHeight = Math.round(size * (112 / 228));
        } else {
          resolvedHeight = size;
          resolvedWidth = Math.round(size * (228 / 112));
        }
      } else {
        resolvedHeight = size;
        resolvedWidth = "auto";
      }
    } else if (resolvedWidth === undefined && resolvedHeight === undefined) {
      resolvedWidth = 140;
      resolvedHeight = Math.round(140 * (112 / 228)); // 69px
    }
  }

  // 1. STANDALONE APP ICON (Squircle with Round Corners or Circle)
  if (variant === "icon" || variant === "squircle") {
    const isSquircle = variant === "squircle" || shape === "squircle";
    return (
      <svg
        viewBox="0 0 512 512"
        width={resolvedWidth}
        height={resolvedHeight}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className={cn("shrink-0 select-none", className)}
        style={style}
        {...props}
      >
        {title && <title>{title}</title>}
        {isSquircle ? (
          <rect x="24" y="24" width="464" height="464" rx="108" fill={NAVY_COLOR} />
        ) : (
          <circle cx="256" cy="256" r="236" fill={NAVY_COLOR} />
        )}
        <path d={APP_ICON_SPARKLE_PATH} fill={GOLD_COLOR} />
      </svg>
    );
  }

  // 2. FAVICON (16px Ultra-sharp Navy Circle + Bold White Star)
  if (variant === "favicon") {
    return (
      <svg
        viewBox="0 0 32 32"
        width={resolvedWidth}
        height={resolvedHeight}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className={cn("shrink-0 select-none", className)}
        style={style}
        {...props}
      >
        <circle cx="16" cy="16" r="15" fill={NAVY_COLOR} />
        <path
          d="M 15.20,5.60 A 0.80,0.80 0 0 1 16.80,5.60 Q 19.36,12.64 26.40,15.20 A 0.80,0.80 0 0 1 26.40,16.80 Q 19.36,19.36 16.80,26.40 A 0.80,0.80 0 0 1 15.20,26.40 Q 12.64,19.36 5.60,16.80 A 0.80,0.80 0 0 1 5.60,15.20 Q 12.64,12.64 15.20,5.60 Z"
          fill="#ffffff"
        />
      </svg>
    );
  }

  // 3. WORDMARK ONLY (No Tagline, Tight 228x88 ViewBox)
  if (variant === "wordmark") {
    return (
      <svg
        viewBox="0 0 228 88"
        width={resolvedWidth}
        height={resolvedHeight}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className={cn("select-none shrink-0", className)}
        style={style}
        {...props}
      >
        {title && <title>{title}</title>}
        {/* Floating Sparkle Star nudged up-right of second 'I' */}
        <path d={FLOATING_SPARKLE_PATH} fill={GOLD_COLOR} />
        {/* Mathematically kerned ISHTI letters */}
        <path d={ISHTI_VECTOR_PATH} fill={textFill} className="transition-colors" />
      </svg>
    );
  }

  // 4. SIDEBAR HORIZONTAL LOCKUP (Icon 36px + Wordmark & Subtitle)
  if (variant === "lockup") {
    return (
      <div
        role="button"
        tabIndex={0}
        title={title}
        className={cn("flex items-center gap-2.5 select-none shrink-0 transition-opacity hover:opacity-85 active:scale-[0.98]", className)}
        style={style as React.CSSProperties}
        {...(props as any)}
      >
        <svg viewBox="0 0 512 512" width={34} height={34} fill="none" className="shrink-0 drop-shadow-xs">
          <rect x="24" y="24" width="464" height="464" rx="108" fill={NAVY_COLOR} />
          <path d={APP_ICON_SPARKLE_PATH} fill={GOLD_COLOR} />
        </svg>
        <div className="flex flex-col leading-none">
          <div className="flex items-center">
            <span
              className="font-extrabold text-[17px] tracking-[0.05em] transition-colors relative"
              style={{ color: textFill }}
            >
              ISHTI
              <span className="absolute -top-1.5 right-[1px] text-[11px] font-bold text-[#ce8418] pointer-events-none select-none">
                ✦
              </span>
            </span>
          </div>
          <span
            className="text-[9.5px] font-semibold tracking-[0.24em] transition-colors mt-1 opacity-80"
            style={{ color: taglineFill }}
          >
            PERSONAL AI
          </span>
        </div>
      </div>
    );
  }

  // 5. FULL WORDMARK + TAGLINE (Tight 228x112 ViewBox)
  return (
    <svg
      viewBox="0 0 228 112"
      width={resolvedWidth}
      height={resolvedHeight}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("select-none shrink-0", className)}
      style={style}
      {...props}
    >
      {title && <title>{title}</title>}
      {/* Floating Sparkle Star nudged up-right of second 'I' */}
      <path d={FLOATING_SPARKLE_PATH} fill={GOLD_COLOR} />
      {/* Mathematically kerned ISHTI letters */}
      <path d={ISHTI_VECTOR_PATH} fill={textFill} className="transition-colors" />
      {/* Refined Tagline: Clearly readable & architectural tracking */}
      <text
        x="16"
        y="102"
        fill={taglineFill}
        className="font-sans font-bold transition-colors select-none"
        style={{
          fontSize: 12.5,
          letterSpacing: "0.22em",
          fontWeight: 700,
          fontFamily: "system-ui, -apple-system, 'Segoe UI', Inter, sans-serif",
        }}
      >
        PERSONAL AI ASSISTANT
      </text>
    </svg>
  );
}

export default IshtiBrand;
