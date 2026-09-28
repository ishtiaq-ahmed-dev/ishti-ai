"""Skills & Data audit: does every skill match its manifest, its dependencies and its own route?

The dispatcher resolves requests by asking each skill's `matches(text)` (a precise regex) in priority order, then by
the semantic router. A skill whose matcher fires on *other* skills' request shapes steals them - that is how "write a
python function ..." reached `apps` and `ishti.check` lost two labelled requests (ISSUES.md Issue 9). Reading the code
does not show that; running every skill's matcher over the project's own labelled request set does.

Checks, per skill:
  1. contract   - manifest parses, and has name/route/description/tier/scope; `run(text, session)` exists; `matches`
                  exists and returns a bool; tier is T0-T3; priority is an int; `laya` is a bool when present.
  2. over-reach - how many of the labelled requests that belong to ANOTHER skill this matcher claims, and whether any
                  of those (with this skill's priority) would actually change the routed answer.
  3. deps       - every `needs.python` import resolves in this environment, every `needs.models` name is installed in
                  Ollama, and `needs.mcp` names are either empty or mappable to a configured MCP server.

    python tools/check_skills.py [--verbose]
Exit code: 0 when nothing is wrong, 1 otherwise.
"""
import argparse
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ishti import CONFIG  # noqa: E402


def labelled():
    """The project's own labelled request set, read out of ishti/check.py (its ground truth for routing)."""
    import ast
    tree = ast.parse(open(os.path.join(ROOT, "ishti", "check.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "LABELLED":
            return [(ast.literal_eval(pair.elts[0]), ast.literal_eval(pair.elts[1])) for pair in node.value.elts]
    raise AssertionError("LABELLED is not in ishti/check.py any more")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    from ishti import dispatcher as d
    from ishti.skills import load_skills

    skills = load_skills()
    problems = []
    checks = 0

    # ---- 1. contract ----------------------------------------------------------------------------------------
    required = ("name", "route", "description", "tier", "scope")
    for name, s in sorted(skills.items()):
        m = s.manifest
        checks += 1
        if name != m.get("name"):
            problems.append(f"{name}: registry key != manifest name ({m.get('name')})")
        missing = [k for k in required if not m.get(k)]
        if missing:
            problems.append(f"{name}: manifest lacks {', '.join(missing)}")
        if m.get("tier") not in ("T0", "T1", "T2", "T3"):
            problems.append(f"{name}: tier {m.get('tier')!r} is not one of T0-T3")
        if not isinstance(m.get("priority", 50), int):
            problems.append(f"{name}: priority is not an int")
        if "laya" in m and not isinstance(m["laya"], bool):
            problems.append(f"{name}: laya flag is not a bool")
        if not callable(getattr(s, "matches", None)):
            problems.append(f"{name}: no matches(text) -> the skill can only be reached by the semantic router")
        if not callable(getattr(s, "run", None)):
            problems.append(f"{name}: no run(text, session)")
        scope = m.get("scope") or {}
        if not any(scope.get(k) for k in ("write", "net", "apps")):
            problems.append(f"{name}: scope declares nothing (write/net/apps)")

    # ---- 2. matcher over-reach ------------------------------------------------------------------------------
    cases = [(t, want) for t, want in labelled() if want in skills]
    overreach, real = {}, {}
    for text, want in cases:
        for name, s in skills.items():
            if name == want:
                continue
            try:
                hit = bool(s.matches(text))
            except Exception as exc:
                problems.append(f"{name}.matches({text[:40]!r}) raised {type(exc).__name__}: {exc}")
                continue
            if hit and name != "chat":  # chat's fallback rules are meant to be last-resort, priority 90
                overreach.setdefault(name, []).append((text, want))
        try:
            got = d.route(text, skills)
        except Exception as exc:
            problems.append(f"route({text[:40]!r}) raised {type(exc).__name__}: {exc}")
            continue
        if got["skill"] != want and got["source"] in ("rule", "learned"):
            real.setdefault(got["skill"], []).append((text, want, got["source"]))

    for name, hits in sorted(overreach.items(), key=lambda kv: -len(kv[1])):
        note = f"{name}: matcher claims {len(hits)} labelled request(s) that belong to other skills"
        if name in real:
            note += f" - {len(real[name])} of them would actually be answered by {name}: {real[name][0][0]!r}"
            problems.append(note)
        elif args.verbose:
            print(f"  (latent) {note}: " + "; ".join(f"{t[:40]!r}->{w}" for t, w in hits[:3]))
    for name, wrong in sorted(real.items()):
        if name not in overreach:
            problems.append(f"{name}: a rule sends {len(wrong)} request(s) here that belong elsewhere: "
                            f"{wrong[0][0]!r} (wanted {wrong[0][1]})")

    # ---- 3. dependency health -------------------------------------------------------------------------------
    ollama_models = set()
    try:
        import requests
        ollama_models = {m["name"] for m in requests.get(CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0] + "/api/tags",
                                                        timeout=8).json().get("models", [])}
    except Exception as exc:
        problems.append(f"could not list Ollama models ({type(exc).__name__}) - dependency check is incomplete")
    mcps = set(CONFIG.get("mcp_servers", {}) if isinstance(CONFIG.get("mcp_servers"), dict)
               else [s.get("name") for s in CONFIG.get("mcp_servers", [])])
    for name, s in sorted(skills.items()):
        needs = s.manifest.get("needs") or {}
        for pkg in needs.get("python", []):
            checks += 1
            if importlib.util.find_spec(pkg.split("==")[0].replace("-", "_")) is None:
                problems.append(f"{name}: needs.python '{pkg}' is not importable in this venv")
        for model in needs.get("models", []):
            checks += 1
            want = CONFIG["llm"]["models"].get(model, model)
            # resolve a missing model EXACTLY as ishti.llm.model_for does at runtime (llm.FALLBACK_MAP,
            # then the config fallback), so a role whose primary isn't pulled yet is not a false alarm
            if ollama_models and want not in ollama_models and f"{want}:latest" not in ollama_models:
                fb = {"qwen3:30b-a3b": "ishti-qwen2.5:14b",
                      "qwen3-coder:30b-a3b": "ishti-qwen2.5-coder:14b"}.get(want,
                      CONFIG["llm"].get("fallback", "ishti-qwen2.5:14b"))
                if fb not in ollama_models and f"{fb}:latest" not in ollama_models:
                    problems.append(f"{name}: needs.models '{model}' -> '{want}' is not installed in Ollama "
                                    f"(and no fallback from llm.FALLBACK_MAP/config is either)")
        for server in needs.get("mcp", []):
            checks += 1
            if server not in mcps and not any(server in str(x) for x in mcps):
                problems.append(f"{name}: needs.mcp '{server}' has no matching entry in config.json")

    print(f"[check_skills] {len(skills)} skills, {checks} assertions, {len(cases)} labelled requests replayed")
    if problems:
        print(f"[check_skills] {len(problems)} problem(s):")
        for p in problems:
            print("  - " + p)
        return 1
    print("[check_skills] OK - every manifest matches its behaviour (no matcher steals another skill's requests), "
          "every declared dependency is present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
