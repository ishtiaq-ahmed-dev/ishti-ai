// First-run choice: this PC (Local) or a cloud API. Shown once until the owner saves a choice; the choice is kept by the
// backend (data/setup.json). Keys use the OS credential store or an environment variable; never config/localStorage.
import { useEffect, useState } from "react";
import { getJSON, postJSON } from "@/lib/api";

type SetupState = {
  chosen: boolean;
  choice: { provider?: string; api_provider?: string; model?: string };
  api_key_env: string;
  api_key_set: boolean;
  defaults: Record<string, string>;
};

const API_PROVIDERS = [
  { id: "anthropic", label: "Anthropic (Claude)" },
  { id: "openai", label: "OpenAI (ChatGPT)" },
  { id: "gemini", label: "Google Gemini" },
  { id: "openrouter", label: "OpenRouter (many models)" },
  { id: "agentrouter", label: "AgentRouter (gpt-6.1-sol)" },
];

export function FirstRunSetup() {
  const [state, setState] = useState<SetupState | null>(null);
  const [mode, setMode] = useState<"local" | "api">("local");
  const [apiProvider, setApiProvider] = useState("anthropic");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    getJSON<SetupState>("/api/setup").then(s => {
      setState(s);
      if (s.choice.provider === "local" || s.choice.provider === "api") setMode(s.choice.provider);
      if (s.choice.api_provider) setApiProvider(s.choice.api_provider);
      if (s.choice.model) setModel(s.choice.model);
    }).catch(e => setError(e instanceof Error ? e.message : "Could not load model setup."));
  }, []);

  if (!state) return error ? <p role="alert" className="p-3 text-sm text-danger">Model setup: {error}</p> : null;
  // stays open while the saved provider is refusing, so the owner sees why instead of failing on the first question
  if (state.chosen && (state.choice.provider !== "api" || state.api_key_set) && check?.ok !== false) return null;

  const save = async () => {
    setBusy(true);
    setError("");
    setCheck(null);
    try {
      const res = await postJSON<{ check?: { ok: boolean; message: string } }>("/api/setup", mode === "local"
        ? { provider: "local" }
        : { provider: "api", api_provider: apiProvider, model: model.trim(), ...(apiKey ? { api_key: apiKey } : {}) });
      setApiKey("");
      if (res.check) setCheck(res.check);
      const fresh = await getJSON<SetupState>("/api/setup");
      setState(fresh);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the choice.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="dialog" aria-label="Choose how ISHTI thinks" className="fixed inset-x-4 bottom-4 z-50 mx-auto max-w-lg rounded-2xl border border-border bg-card p-4 shadow-lg">
      <h2 className="text-sm font-semibold text-foreground">How should ISHTI think?</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Choose how answers are generated. Cloud mode uses a local keyword memory index; voice components are optional.
      </p>
      <fieldset className="mt-3 space-y-2 text-xs">
        <legend className="sr-only">Where answers are generated</legend>
        <label className="flex items-start gap-2">
          <input type="radio" name="mode" checked={mode === "local"} onChange={() => setMode("local")} autoFocus={mode === "local"} />
          <span><strong>On this PC</strong> (Ollama, needs an NVIDIA graphics card). Model inference stays on this PC. Requested web tools and connected services can use the internet.</span>
        </label>
        <label className="flex items-start gap-2">
          <input type="radio" name="mode" checked={mode === "api"} onChange={() => setMode("api")} autoFocus={mode === "api"} />
          <span><strong>Cloud API</strong> (you pay the provider per message; no graphics card needed). Your messages go to that provider.</span>
        </label>
      </fieldset>
      {mode === "api" && (
        <div className="mt-3 space-y-2 text-xs">
          <select className="w-full rounded-lg border border-border bg-background px-2 py-1" value={apiProvider}
            onChange={(e) => setApiProvider(e.target.value)} aria-label="API provider">
            {API_PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <input className="w-full rounded-lg border border-border bg-background px-2 py-1" value={model}
            onChange={(e) => setModel(e.target.value)} placeholder={`Model (default: ${state.defaults[apiProvider] ?? ""})`}
            aria-label="Model name" />
          <input type="password" autoComplete="off" value={apiKey} onChange={e => setApiKey(e.target.value)}
            aria-label="API key" placeholder="API key (saved only in this PC's OS credential store)"
            className="w-full rounded-lg border border-border bg-background px-2 py-1" />
          <p className="text-muted-foreground">
            Or set the environment variable <code>{state.api_key_env}</code> to your key, then restart ISHTI.
            {state.api_key_set ? " It is set now." : " It is not set yet."}
          </p>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-xs text-danger">{error}</p>}
      {check && !check.ok && (
        <p role="alert" className="mt-2 text-xs text-danger">Saved, but the provider refused a test message: {check.message}</p>
      )}
      <div className="mt-3 flex justify-end">
        <button type="button" onClick={save} disabled={busy}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">
          {busy ? "Saving..." : "Save choice"}
        </button>
      </div>
    </div>
  );
}
