import { AnimatePresence, motion } from "motion/react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

interface ThemeToggleProps {
  className?: string;
  size?: number;
}

export function ThemeToggle({ className, size = 34 }: ThemeToggleProps) {
  const { isDark, toggle } = useTheme();

  return (
    <button
      type="button"
      onClick={toggle}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      className={cn(
        "relative grid place-items-center rounded-full bg-[#F4F4F9] text-[#6b6a80] transition-transform active:scale-90 hover:text-foreground cursor-pointer dark:bg-[#17171d] dark:text-[#9ca3af] outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:active:scale-100",
        className,
      )}
      style={{ width: size, height: size }}
    >
      <AnimatePresence mode="wait" initial={false}>
        {isDark ? (
          <motion.div
            key="moon"
            initial={{ scale: 0.5, rotate: -40, opacity: 0 }}
            animate={{ scale: 1, rotate: 0, opacity: 1 }}
            exit={{ scale: 0.5, rotate: 40, opacity: 0 }}
            transition={{ type: "spring", stiffness: 450, damping: 25 }}
            className="grid place-items-center"
          >
            <Moon className="h-4 w-4 fill-indigo-400/20 text-indigo-400" />
          </motion.div>
        ) : (
          <motion.div
            key="sun"
            initial={{ scale: 0.5, rotate: 40, opacity: 0 }}
            animate={{ scale: 1, rotate: 0, opacity: 1 }}
            exit={{ scale: 0.5, rotate: -40, opacity: 0 }}
            transition={{ type: "spring", stiffness: 450, damping: 25 }}
            className="grid place-items-center"
          >
            <Sun className="h-4 w-4 text-amber-500 fill-amber-500/20" />
          </motion.div>
        )}
      </AnimatePresence>
    </button>
  );
}

export function ThemeSwitch({ className }: { className?: string }) {
  const { isDark, toggle } = useTheme();

  return (
    <button
      type="button"
      role="switch"
      aria-checked={isDark}
      aria-label="Toggle dark mode"
      onClick={toggle}
      className={cn(
        "relative flex h-8 w-16 cursor-pointer items-center rounded-full p-1 transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        isDark ? "bg-[#18181f] border border-border" : "bg-[#ebebf0] border border-border",
        className,
      )}
    >
      <div className="absolute inset-0 flex items-center justify-between px-2 text-[10px] pointer-events-none">
        <Sun className={cn("h-3.5 w-3.5 transition-opacity", isDark ? "opacity-30 text-muted-foreground" : "opacity-0")} />
        <Moon className={cn("h-3.5 w-3.5 transition-opacity", isDark ? "opacity-0" : "opacity-30 text-muted-foreground")} />
      </div>
      <motion.div
        layout
        transition={{ type: "spring", stiffness: 600, damping: 32 }}
        className={cn(
          "grid h-6 w-6 place-items-center rounded-full shadow-sm text-foreground",
          isDark ? "bg-[#252530] text-indigo-400 ml-auto" : "bg-white text-amber-500 mr-auto",
        )}
      >
        {isDark ? <Moon className="h-3.5 w-3.5 fill-indigo-400/20" /> : <Sun className="h-3.5 w-3.5 fill-amber-500/20" />}
      </motion.div>
    </button>
  );
}

export default ThemeToggle;
