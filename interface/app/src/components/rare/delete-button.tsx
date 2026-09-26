// From Rare UI - https://rareui.com - Copyright (c) 2026 Swami Malode.
// MIT + Commons Clause + Attribution license: see THIRD_PARTY_NOTICES.md at the repo root.
"use client";

import { useEffect, useRef, useState } from "react";
import type { ComponentProps, ReactNode } from "react";
import {
  animate,
  AnimatePresence,
  motion,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
  useTransform,
  type Transition,
} from "motion/react";
import { cn } from "@/lib/utils";

const HINGE = "3px 6px";
const LID_OPEN = -35;
const WALL_TOP = 6;
const WALL_TOP_OPEN = 13.5;
const WALL_BASE = 20;

const HOLD = { deleted: 1200, kept: 500 };

const EASE = [0.32, 0.72, 0, 1] as const;
const EASE_LID = [0.34, 1.1, 0.64, 1] as const;

const WIDTH = { duration: 0.45, ease: EASE } as const;
const LID = { duration: 0.5, ease: EASE_LID } as const;
const WALL = { duration: 0.45, ease: EASE } as const;
const IN = { duration: 0.35, ease: EASE, delay: 0.1 } as const;
const OUT = { duration: 0.25, ease: EASE } as const;
const TAP = { duration: 0.2, ease: EASE } as const;
const SWAP = { duration: 0.2, ease: EASE } as const;
const SETTLE = { duration: 0.4, ease: EASE } as const;
const PRESS = {
  type: "spring",
  stiffness: 520,
  damping: 18,
  mass: 0.5,
} as const;
const INSTANT = { duration: 0 } as const;

const SURFACE = "bg-[#F4F4F9] dark:bg-[#262626]";
const RECESS = "bg-[#E7E7EF] dark:bg-[#1B1B1B]";
const GLYPH = "text-[#868593] dark:text-[#9B9AA7]";
const FOCUS = "outline-none focus-visible:ring-2 focus-visible:ring-[#868593]";
const ACCENT = "#FF5F2E";

const LIFT =
  "shadow-[0_0.5px_1px_rgba(0,0,0,0.05),0_1px_3px_rgba(0,0,0,0.08),inset_0_0.5px_0_rgba(255,255,255,0.9)] dark:shadow-[0_0.5px_1px_rgba(0,0,0,0.35),0_1.5px_4px_rgba(0,0,0,0.25),inset_0_0.5px_0_rgba(255,255,255,0.07)]";

const ICON = {
  viewBox: "0 0 24 24",
  fill: "none",
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
} as const;

const panelMotion = {
  hidden: { opacity: 0, x: -6, transition: OUT },
  shown: { opacity: 1, x: 0, transition: { ...IN, staggerChildren: 0.05 } },
};

const circleMotion = {
  hidden: { opacity: 0, scale: 0.9, transition: OUT },
  shown: { opacity: 1, scale: 1, transition: IN },
};

function Circle({
  label,
  size = "md",
  onClick,
  children,
}: {
  label: string;
  size?: "sm" | "md";
  onClick: (e: React.MouseEvent) => void;
  children: ReactNode;
}) {
  const reduced = useReducedMotion() ?? false;
  const isSm = size === "sm";

  return (
    <motion.div className="flex" variants={reduced ? undefined : circleMotion}>
      <motion.button
        type="button"
        aria-label={label}
        onClick={(e) => {
          e.stopPropagation();
          onClick(e);
        }}
        whileHover={reduced ? undefined : { scale: 1.05 }}
        whileTap={reduced ? undefined : { scale: 0.88 }}
        transition={PRESS}
        className={cn(
          "grid place-items-center rounded-full transition-colors duration-200 hover:bg-[#FAFAFD] dark:hover:bg-[#2C2C2C]",
          FOCUS,
          SURFACE,
          LIFT,
          isSm ? "h-5 w-5" : "h-7 w-7",
        )}
      >
        <svg
          {...ICON}
          width={isSm ? "10" : "14"}
          height={isSm ? "10" : "14"}
          stroke="currentColor"
          strokeWidth={isSm ? "3" : "3.5"}
        >
          {children}
        </svg>
      </motion.button>
    </motion.div>
  );
}

type Status = "idle" | "deleted" | "kept";

export type DeleteButtonProps = Omit<
  ComponentProps<"div">,
  "onAnimationStart" | "onDrag" | "onDragStart" | "onDragEnd"
> & {
  onConfirm?: () => void;
  onCancel?: () => void;
  size?: "sm" | "md";
};

export function DeleteButton({
  className,
  size = "md",
  onConfirm,
  onCancel,
  ...props
}: DeleteButtonProps) {
  const reduced = useReducedMotion() ?? false;
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const trigger = useRef<HTMLButtonElement>(null);
  const timing = (transition: Transition) => (reduced ? INSTANT : transition);

  const isSm = size === "sm";
  const TILE = isSm ? 26 : 48;
  const PANEL = isSm ? 52 : 84;
  const iconSize = isSm ? 14 : 20;

  const top = useMotionValue(WALL_TOP);
  const wall = useTransform(top, (y) => WALL_BASE - y);
  const bin = useMotionTemplate`M19 ${top}v${wall}a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V${top}`;
  const settle = useMotionValue(1);

  useEffect(() => {
    const walls = animate(
      top,
      open ? WALL_TOP_OPEN : WALL_TOP,
      reduced ? INSTANT : WALL,
    );
    return () => walls.stop();
  }, [open, reduced, top]);

  useEffect(() => {
    if (status === "idle") return;
    const nudge =
      status === "kept" && !reduced
        ? animate(settle, [1, 0.86, 1], SETTLE)
        : null;
    const done = setTimeout(() => setStatus("idle"), HOLD[status]);
    return () => {
      nudge?.stop();
      clearTimeout(done);
    };
  }, [status, reduced, settle]);

  const resolve = (next: Exclude<Status, "idle">, e?: React.MouseEvent) => {
    e?.stopPropagation();
    setOpen(false);
    setStatus(next);
    trigger.current?.focus();
    (next === "deleted" ? onConfirm : onCancel)?.();
  };

  return (
    <motion.div
      data-slot="delete-button"
      data-state={open ? "open" : "closed"}
      data-status={status}
      onClick={(e) => e.stopPropagation()}
      className={cn(
        "relative shrink-0 select-none overflow-hidden",
        isSm ? "h-6.5 rounded-lg" : "h-12 rounded-2xl",
        SURFACE,
        GLYPH,
        className,
      )}
      animate={{ width: open ? TILE + PANEL : TILE }}
      transition={timing(WIDTH)}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) resolve("kept");
      }}
      {...props}
    >
      <motion.button
        ref={trigger}
        type="button"
        aria-label="Delete"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          if (open) return resolve("kept", e);
          setStatus("idle");
          setOpen(true);
        }}
        whileTap={reduced ? undefined : { scale: 0.94 }}
        transition={TAP}
        className={cn(
          "relative z-10 grid place-items-center cursor-pointer",
          isSm ? "h-6.5 w-6.5 rounded-lg" : "h-12 w-12 rounded-2xl",
          FOCUS,
        )}
      >
        <AnimatePresence mode="wait" initial={false}>
          {status === "deleted" ? (
            <motion.svg
              key="done"
              {...ICON}
              width={iconSize}
              height={iconSize}
              stroke={ACCENT}
              strokeWidth="2.5"
              initial={{ opacity: 0, scale: 0.6 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.6 }}
              transition={timing(SWAP)}
            >
              <motion.path
                d="M4 12.5 9.5 18 20 7"
                initial={reduced ? undefined : { pathLength: 0 }}
                animate={reduced ? undefined : { pathLength: 1 }}
                transition={SETTLE}
              />
            </motion.svg>
          ) : (
            <motion.svg
              key="bin"
              {...ICON}
              width={iconSize}
              height={iconSize}
              stroke="currentColor"
              strokeWidth="2"
              className="overflow-visible"
              style={{ scale: settle }}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={timing(SWAP)}
            >
              <motion.path d={bin} />
              <motion.g
                style={{ transformBox: "view-box", transformOrigin: HINGE }}
                animate={{ rotate: open ? LID_OPEN : 0 }}
                transition={timing(LID)}
              >
                <path d="M3 6h18" />
                <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </motion.g>
            </motion.svg>
          )}
        </AnimatePresence>
      </motion.button>

      <span role="status" aria-live="polite" className="sr-only">
        {status === "deleted" ? "Deleted" : status === "kept" ? "Kept" : ""}
      </span>

      <AnimatePresence>
        {open && (
          <motion.div
            key="panel"
            style={{ width: PANEL }}
            className={cn(
              "absolute inset-y-0 right-0 flex items-center justify-center",
              isSm ? "gap-1 rounded-lg pr-0.5" : "gap-2 rounded-2xl",
              RECESS,
            )}
            variants={reduced ? undefined : panelMotion}
            initial="hidden"
            animate="shown"
            exit="hidden"
          >
            <span
              aria-hidden
              className={cn(
                "absolute -left-1.25 top-1/2 z-20 h-2.5 w-1.5 -translate-y-1/2 [clip-path:polygon(100%_0,0_50%,100%_100%)]",
                RECESS,
              )}
            />
            <Circle size={size} label="Confirm delete" onClick={(e) => resolve("deleted", e)}>
              <path d="M4 12.5 9.5 18 20 7" stroke={ACCENT} />
            </Circle>
            <Circle size={size} label="Cancel" onClick={(e) => resolve("kept", e)}>
              <path d="M6 6 18 18M18 6 6 18" />
            </Circle>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export default DeleteButton;
