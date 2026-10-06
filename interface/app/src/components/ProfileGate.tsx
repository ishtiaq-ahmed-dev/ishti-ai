// Start-up chooser: Personal or Business. Chats, memory and prompt layers stay separate per profile;
// the header switch (ProfileSwitcher) changes it later.
import { Briefcase, Heart } from "lucide-react";
import { useState } from "react";
import { chatStore, type Profile } from "@/lib/chat-store";
import { IshtiBrand } from "@/components/IshtiBrand";

const OPTIONS: { p: Profile; title: string; text: string; Icon: typeof Heart }[] = [
  { p: "personal", title: "Personal", text: "Family, friends, your own tasks and memory.", Icon: Heart },
  { p: "business", title: "Business", text: "Clients, partners and work. Separate chats and memory.", Icon: Briefcase },
];

export function ProfileGate({ onChosen }: { onChosen: () => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const choose = async (p: Profile) => {
    setBusy(true);
    setError("");
    try {
      await chatStore.setProfile(p);
      try { sessionStorage.setItem("ishti-profile-chosen", "1"); } catch {}
      onChosen();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-[60] grid place-items-center bg-background" role="dialog" aria-modal="true" data-testid="profile-gate">
      <div className="flex w-full max-w-xl flex-col items-center gap-6 px-6">
        <IshtiBrand variant="icon" size={56} />
        <h1 className="text-2xl font-semibold tracking-tight">Where do you want to go?</h1>
        <div className="grid w-full gap-3 sm:grid-cols-2">
          {OPTIONS.map(({ p, title, text, Icon }) => (
            <button key={p} onClick={() => void choose(p)} disabled={busy} data-testid={`gate-${p}`}
              className="flex cursor-pointer flex-col items-start gap-2 rounded-2xl border border-border bg-card/60 p-5 text-left transition-colors hover:border-accent hover:bg-accent/10">
              <Icon className="h-6 w-6 text-accent" aria-hidden />
              <span className="text-lg font-semibold">{title}</span>
              <span className="text-sm text-muted-foreground">{text}</span>
            </button>
          ))}
        </div>
        {error && <p role="alert" className="text-sm text-danger">Could not switch profile: {error}</p>}
        <p className="text-xs text-muted-foreground">You can switch any time from the top of the sidebar.</p>
      </div>
    </div>
  );
}
