// From Rare UI - https://rareui.com - Copyright (c) 2026 Swami Malode.
// MIT + Commons Clause + Attribution license: see THIRD_PARTY_NOTICES.md at the repo root.
// Optimized for hardware-accelerated 60/120fps fluid performance.
"use client";

import React, { useState } from "react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";

const themes = {
  black: {
    backFill: "black",
    backInsetShadow: "inset 0 0 6px 2px rgba(255,255,255,0.37)",
    flapFill: "#292929",
    flapFillOpacity: 0.4,
    flapStroke: "#979797",
    cardFill: "#F1F1F1",
    cardStroke: "#E0E0E0",
    cardLineFill: "#D4D4D4",
  },
  white: {
    backFill: "#ffffff",
    backInsetShadow: "inset 0 0 6px 2px rgba(178,178,178,0.25)",
    flapFill: "#f5f5f5",
    flapFillOpacity: 0.85,
    flapStroke: "#d4d4d4",
    cardFill: "#262626",
    cardStroke: "#404040",
    cardLineFill: "#737373",
  },
  blue: {
    backFill: "#50B1FD",
    backInsetShadow: "inset 0 0 6px 2px rgba(255,255,255,0.35)",
    flapFill: "#3a9ae8",
    flapFillOpacity: 0.55,
    flapStroke: "#7ec8ff",
    cardFill: "#F1F1F1",
    cardStroke: "#E0E0E0",
    cardLineFill: "#D4D4D4",
  },
} as const;

const sizeScales = {
  sm: 0.65,
  md: 1,
  lg: 1.35,
} as const;

type FolderComponentProps = Omit<React.ComponentProps<"div">, "color"> & {
  color?: "black" | "white" | "blue";
  size?: "sm" | "md" | "lg";
  allowOpen?: boolean;
};

const BASE_WIDTH = 321;
const BASE_HEIGHT = 270;

const FLAP_PATH =
  "M0 25C0 11.1929 11.1929 0 25 0H136.084C143.044 0 149.689 2.90139 154.42 8.00608L178.08 33.5343C182.811 38.639 189.456 41.5404 196.416 41.5404H296C309.807 41.5404 321 52.7333 321 66.5404V216C321 229.807 309.807 241 296 241H25C11.1929 241 0 229.807 0 216V25Z";

const SPRING_TRANSITION = {
  type: "spring" as const,
  stiffness: 240,
  damping: 22,
  mass: 0.8,
};

const FolderComponent = ({
  color = "black",
  size = "md",
  allowOpen = true,
  className,
  ...props
}: FolderComponentProps) => {
  const theme = themes[color] ?? themes.black;
  const scale = sizeScales[size];
  const [isHovered, setIsHovered] = useState(false);
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div
      data-slot="folder"
      className={cn(
        "relative w-full h-full flex items-center justify-center",
        className,
      )}
      {...props}
    >
      <div
        className="relative cursor-pointer select-none"
        style={{
          width: BASE_WIDTH * scale,
          height: BASE_HEIGHT * scale,
          touchAction: "manipulation",
          WebkitTapHighlightColor: "transparent",
        }}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => {
          setIsHovered(false);
          setIsOpen(false);
        }}
        onClick={() => {
          if (allowOpen) {
            setIsOpen((o) => !o);
          }
        }}
      >
        <div
          className="absolute top-1/2 left-1/2"
          style={{
            width: BASE_WIDTH,
            height: BASE_HEIGHT,
            transform: `translate(-50%, -50%) scale(${scale})`,
            perspective: 800 * scale,
            transformStyle: "preserve-3d",
            willChange: "transform",
          }}
        >
          {/* Back folder body */}
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
            <div
              style={{
                width: BASE_WIDTH,
                height: BASE_HEIGHT,
                borderRadius: 25,
                backgroundColor: theme.backFill,
                boxShadow: theme.backInsetShadow,
              }}
            />
          </div>

          {/* Documents fanning out in 3D */}
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center justify-center">
            {/* Card 1 (Right card) */}
            <motion.div
              className="absolute"
              style={{
                willChange: "transform",
                backfaceVisibility: "hidden",
                WebkitBackfaceVisibility: "hidden",
                transform: "translateZ(0)",
              }}
              animate={{
                y: isOpen ? -160 : isHovered ? -30 : -10,
                x: isOpen ? 70 : 40,
                rotate: isOpen ? 18 : isHovered ? 14 : 10,
              }}
              transition={{
                ...SPRING_TRANSITION,
                delay: isOpen ? 0.04 : isHovered ? 0.05 : 0,
              }}
            >
              <Card id={1} theme={theme} />
            </motion.div>

            {/* Card 2 (Center card) */}
            <motion.div
              className="absolute"
              style={{
                willChange: "transform",
                backfaceVisibility: "hidden",
                WebkitBackfaceVisibility: "hidden",
                transform: "translateZ(0)",
              }}
              animate={{
                y: isOpen ? -180 : isHovered ? -35 : -20,
                x: isOpen ? 0 : 3,
                rotate: isOpen ? -3 : isHovered ? -1 : 2,
              }}
              transition={{
                ...SPRING_TRANSITION,
                delay: isOpen ? 0.02 : isHovered ? 0.02 : 0,
              }}
            >
              <Card id={2} theme={theme} />
            </motion.div>

            {/* Card 3 (Left card) */}
            <motion.div
              className="absolute"
              style={{
                willChange: "transform",
                backfaceVisibility: "hidden",
                WebkitBackfaceVisibility: "hidden",
                transform: "translateZ(0)",
              }}
              animate={{
                y: isOpen ? -170 : isHovered ? -44 : -22,
                x: isOpen ? -65 : -40,
                rotate: isOpen ? -14 : isHovered ? -9 : -5,
              }}
              transition={{
                ...SPRING_TRANSITION,
                delay: 0,
              }}
            >
              <Card id={3} theme={theme} />
            </motion.div>
          </div>

          {/* Front folder flap: 3D rotating hinge */}
          <motion.div
            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 mt-4"
            style={{
              transformOrigin: "bottom center",
              transformStyle: "preserve-3d",
              width: 321,
              height: 241,
              backfaceVisibility: "hidden",
              WebkitBackfaceVisibility: "hidden",
              willChange: "transform",
            }}
            animate={{ rotateX: isOpen ? -55 : isHovered ? -45 : -15 }}
            transition={SPRING_TRANSITION}
          >
            <svg
              className="absolute inset-0"
              width="321"
              height="241"
              viewBox="0 0 321 241"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              style={{
                willChange: "transform",
                filter: "drop-shadow(0 2px 8px rgba(0,0,0,0.18))",
              }}
            >
              <path
                d={FLAP_PATH}
                fill={theme.flapFill}
                fillOpacity={theme.flapFillOpacity}
              />
              <path
                d="M25 0.5H136.084C142.905 0.5 149.417 3.3431 154.054 8.3457L177.713 33.874C182.539 39.0808 189.317 42.04 196.416 42.04H296C309.531 42.04 320.5 53.0092 320.5 66.54V216C320.5 229.531 309.531 240.5 296 240.5H25C11.469 240.5 0.5 229.531 0.5 216V25C0.5 11.469 11.469 0.5 25 0.5Z"
                stroke={theme.flapStroke}
                strokeWidth="1.25"
              />
            </svg>
          </motion.div>
        </div>
      </div>
    </div>
  );
};

export default FolderComponent;

export { FolderComponent as Folder };
export type { FolderComponentProps };

type Theme = (typeof themes)[keyof typeof themes];

const CARD_LINE_Y = [61, 75, 89, 103, 117, 132, 146, 160, 174];

const Card = ({ id: _id, theme }: { id: number; theme: Theme }) => {
  return (
    <div
      data-slot="folder-card"
      className="relative rounded-[20px] overflow-hidden select-none"
      style={{
        width: 164,
        height: 214,
        backgroundColor: theme.cardFill,
        boxShadow:
          theme.cardFill === "#262626"
            ? "inset 0 1px 2px rgba(255,255,255,0.1), 0 3px 10px rgba(0,0,0,0.35)"
            : "inset 0 1px 2px rgba(255,255,255,0.9), 0 3px 10px rgba(0,0,0,0.12)",
        border: `1px solid ${theme.cardStroke}`,
        willChange: "transform",
        backfaceVisibility: "hidden",
        WebkitBackfaceVisibility: "hidden",
      }}
    >
      <svg
        width="164"
        height="214"
        viewBox="0 0 164 214"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className="w-full h-full"
      >
        <rect
          x="14"
          y="31"
          width="135"
          height="12"
          rx="6"
          fill={theme.cardLineFill}
        />
        {CARD_LINE_Y.map((y) => (
          <React.Fragment key={y}>
            <rect
              x="15"
              y={y}
              width="64.5"
              height="5.8"
              rx="2.9"
              fill={theme.cardLineFill}
            />
            <rect
              x="84.5"
              y={y}
              width="64.5"
              height="5.8"
              rx="2.9"
              fill={theme.cardLineFill}
            />
          </React.Fragment>
        ))}
      </svg>
    </div>
  );
};
