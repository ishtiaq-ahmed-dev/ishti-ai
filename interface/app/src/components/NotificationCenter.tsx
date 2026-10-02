// The bell's inbox: a small popover "mini chat" (App.tsx) and the full Notifications tab (Tasks page).
// Data lives in the backend (ishti/notifications.py); this file only renders and marks read.
import { useEffect, useState } from "react";
import { Bell, BellRing, Briefcase, CheckCheck, CircleAlert, Download, TriangleAlert, X } from "lucide-react";
import { getJSON, postJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

export type Notif = {
  id: number;
  ts: number;
  kind: "reminder" | "job" | "download" | "alert" | "error" | string;
  title: string;
  body?: string;
  read: boolean;
};

const KIND: Record<string, { label: string; icon: typeof Bell; tint: string }> = {
  reminder: { label: "Reminder", icon: BellRing, tint: "bg-violet-500/12 text-violet-500 dark:text-violet-400" },
  job: { label: "Job finished", icon: Briefcase, tint: "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400" },
  download: { label: "Download", icon: Download, tint: "bg-blue-500/12 text-blue-600 dark:text-blue-400" },
  alert: { label: "Alert", icon: TriangleAlert, tint: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  error: { label: "Error", icon: CircleAlert, tint: "bg-red-500/12 text-red-600 dark:text-red-400" },
};

export function timeAgo(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
}

function meta(kind: string) {
  return KIND[kind] ?? KIND.alert;
}

function Row({ n, onOpen }: { n: Notif; onOpen?: (n: Notif) => void }) {
  const { label, icon: Icon, tint } = meta(n.kind);
  return (
    <button
      type="button"
      onClick={() => onOpen?.(n)}
      className={cn(
        "flex w-full items-start gap-2.5 rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-muted/60 cursor-pointer",
        !n.read && "bg-accent-soft/40",
      )}
    >
      <span className={cn("mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg", tint)}>
        <Icon className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className={cn("truncate text-xs", n.read ? "text-muted-foreground" : "font-semibold text-foreground")}>
            {n.title}
          </span>
          {!n.read && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" title="Unread" />}
        </span>
        {n.body ? <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{n.body}</span> : null}
        <span className="mt-1 block text-[10px] uppercase tracking-wide text-muted-foreground/80">
          {label} · {timeAgo(n.ts)}
        </span>
      </span>
    </button>
  );
}

export function NotificationPopover({
  items,
  unread,
  onMarkAll,
  onMarkOne,
  onViewAll,
  onClose,
}: {
  items: Notif[];
  unread: number;
  onMarkAll: () => void;
  onMarkOne: (id: number) => void;
  onViewAll: () => void;
  onClose: () => void;
}) {
  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-background/95 shadow-2xl backdrop-blur-md">
      <div className="flex items-center justify-between gap-2 border-b border-border/60 px-3 py-2.5">
        <div className="flex items-center gap-2 text-xs font-semibold">
          <Bell className="h-3.5 w-3.5 text-accent" />
          Notifications
          {unread > 0 && (
            <span className="rounded-full bg-accent px-1.5 py-0.5 text-[10px] font-bold text-white">{unread}</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onMarkAll}
            disabled={unread === 0}
            title="Mark all read"
            className="flex items-center gap-1 rounded-lg px-1.5 py-1 text-[10px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-default disabled:opacity-40 cursor-pointer"
          >
            <CheckCheck className="h-3 w-3" /> Mark all read
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Close"
            className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <div className="scroll-thin max-h-[min(60vh,400px)] overflow-y-auto p-1.5">
        {items.length ? (
          items.map((n) => <Row key={n.id} n={n} onOpen={(x) => { if (!x.read) onMarkOne(x.id); }} />)
        ) : (
          <p className="px-3 py-8 text-center text-xs text-muted-foreground">You're all caught up.</p>
        )}
      </div>
      <button
        type="button"
        onClick={onViewAll}
        className="w-full border-t border-border/60 bg-muted/30 px-3 py-2 text-xs font-medium text-accent transition-colors hover:bg-muted cursor-pointer"
      >
        View all notifications
      </button>
    </div>
  );
}

/** The full list, used as a tab on the Tasks page (#notifications). Self-contained: it loads on mount and
 *  refreshes whenever App.tsx dispatches "ishti-notification" after a /ws push or a read action. */
export function NotificationsPanel() {
  const [items, setItems] = useState<Notif[]>([]);
  const [unread, setUnread] = useState(0);

  const load = () => {
    getJSON<{ notifications: Notif[]; unread: number }>("/api/notifications")
      .then((d) => {
        setItems(d.notifications ?? []);
        setUnread(d.unread ?? 0);
      })
      .catch((err) => console.warn("[notifications] load failed:", err));
  };

  useEffect(() => {
    load();
    const onSync = () => load();
    window.addEventListener("ishti-notification", onSync);
    return () => window.removeEventListener("ishti-notification", onSync);
  }, []);

  const sync = () => window.dispatchEvent(new CustomEvent("ishti-notification"));

  return (
    <div className="rounded-2xl border border-border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 px-4 py-3">
        <div className="text-sm font-semibold">
          Notifications
          {unread > 0 && (
            <span className="ml-2 rounded-full bg-accent px-2 py-0.5 text-[10px] font-bold text-white">
              {unread} unread
            </span>
          )}
        </div>
        {unread > 0 && (
          <button
            type="button"
            onClick={() => postJSON("/api/notifications/read", {}).catch(() => {}).finally(sync)}
            className="flex items-center gap-1.5 rounded-xl border border-border px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
          >
            <CheckCheck className="h-3.5 w-3.5" /> Mark all read
          </button>
        )}
      </div>
      <div className="divide-y divide-border/40">
        {items.length ? (
          items.map((n) => (
            <Row key={n.id} n={n} onOpen={(x) => { if (!x.read) postJSON("/api/notifications/read", { id: x.id }).catch(() => {}).finally(sync); }} />
          ))
        ) : (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">
            Nothing yet — reminders, finished jobs, downloads, alerts and errors appear here.
          </p>
        )}
      </div>
    </div>
  );
}
