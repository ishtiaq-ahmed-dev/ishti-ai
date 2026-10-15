"""Profile-isolated chat flow over the API (tools/qa/chat_flow_api.py).

Drives the contract-v2 chat surface exactly as the UI does:
  PUT  /api/profile                      switch active owner profile
  POST /api/chats                        create a chat in that profile
  POST /api/chats/{chat_id}/messages     persist + launch a server-side run
  GET  /api/runs/{run_id}                poll state + steps
  GET  /api/events?since_seq=N           confirm run_step / run_finished streamed

For every prompt it verifies: a reply arrives, steps are emitted for tool-needing prompts,
a WhatsApp draft asks for confirmation (T2 gate), and repeated client_msg_id never creates a
second run. Across profiles it verifies isolation: chats/runs/events never cross profiles.

Writes reports/qa/qa_chat_flow.json and appends defects to reports/qa/qa_defects.json.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _qa_common as qa  # noqa: E402

TERMINAL = ("done", "failed", "cancelled")

# (category, prompt, expects_steps, expects_t2_confirm)
PROMPTS: list[tuple[str, str, bool, bool]] = [
    ("weather", "What is the weather in Karachi right now?", True, False),
    ("weather", "Will it rain in Lahore tomorrow?", True, False),
    ("web_search", "Search the web for the latest news about Nvidia.", True, False),
    ("web_search", "Look up the current price of Bitcoin online.", True, False),
    ("web_search", "Find the Wikipedia page for the Silk Road and summarize it.", True, False),
    ("file_read", "Read the file README.md and summarize it.", True, False),
    ("file_read", "Open docs/ARCHITECTURE.md and tell me what it says.", True, False),
    ("file_read", "Read owner_profile.yaml and list its keys.", True, False),
    ("reminder", "Remind me to call the dentist tomorrow at 5pm.", True, False),
    ("reminder", "Set a reminder to pay the electricity bill on Friday.", True, False),
    ("reminder", "List my reminders.", True, False),
    ("whatsapp", "Draft a WhatsApp message to my brother saying I will call him later.", True, True),
    ("whatsapp", "Send a WhatsApp to Ahmed asking for the report.", True, True),
    ("whatsapp", "Write a WhatsApp reply to Ammi that I reached home safely.", True, True),
    ("pdf", "Summarize the PDF report.pdf.", True, False),
    ("pdf", "Give me the key points from invoice.pdf.", True, False),
    ("general", "Hello ISHTI, how are you?", False, False),
    ("general", "What is 17 times 23?", False, False),
    ("general", "Who am I?", False, False),
    ("general", "Summarize what you did today.", False, False),
    ("general", "My favourite colour is turquoise, remember that.", False, False),
    ("general", "What did I just tell you?", False, False),
    ("general", "Explain what an API is in one line.", False, False),
    ("general", "Translate 'good morning' to Urdu.", False, False),
    ("business", "List my CRM clients.", True, False),
    ("business", "Draft a short invoice email to the client.", True, True),
    ("general", "Give me a 3-point plan for a healthy morning routine.", False, False),
    ("general", "Write a haiku about rain.", False, False),
    ("general", "What is the capital of Australia?", False, False),
    ("general", "Tell me a fun fact about the sun.", False, False),
]

CONFIRM_MARKERS = ("shall i", "confirm", "yes or no", "go ahead", "?", "would you like")


def last_seq(base: str) -> int:
    resp = qa.http(base, "GET", "/api/events?since_seq=0&limit=1")
    data = resp.json() or {}
    return int(data.get("last_seq") or 0)


def wait_for_run(base: str, run_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        resp = qa.http(base, "GET", f"/api/runs/{run_id}")
        data = resp.json() or {}
        last = data
        if data.get("state") in TERMINAL:
            return data
        time.sleep(0.4)
    last.setdefault("state", "timeout")
    return last


def events_for(base: str, since_seq: int) -> list[dict]:
    resp = qa.http(base, "GET", f"/api/events?since_seq={since_seq}&limit=5000")
    return (resp.json() or {}).get("events", [])


def main() -> int:
    args = parse_args()
    qa.require_test_mode()
    base = qa.safe_base_url(args.url)
    output = qa.repo_output(args.output)
    defects = qa.DefectStore()

    prompts = PROMPTS[: args.prompts] if args.prompts else PROMPTS
    result = {"schema": 1, "started_at": qa.iso_now(), "base_url": base,
              "reps": args.reps, "profiles": ["personal", "business"],
              "prompt_count": len(prompts), "runs": [], "repetitions": [], "errors": []}
    qa.dump_json(output, result)
    defects_before = len(defects.rows)

    for rep in range(1, args.reps + 1):
        rep_fails = 0
        for profile in ("personal", "business"):
            r = qa.http(base, "PUT", "/api/profile", body={"active": profile})
            if r.status != 200:
                result["errors"].append({"at": qa.iso_now(), "op": "set_profile",
                                         "profile": profile, "status": r.status})
            # create chat per profile
            ch = qa.http(base, "POST", "/api/chats", body={"profile": profile})
            chat = ch.json() or {}
            chat_id = chat.get("chat_id")
            if ch.status not in (200, 201) or not chat_id:
                rep_fails += 1
                defects.add(f"CHAT-{profile}-create", "chat_flow",
                            f"POST /api/chats {{'profile': '{profile}'}}",
                            "200/201 with a chat_id", f"status={ch.status} body={ch.body[:200]}",
                            "ishti/contract_api.py")
                continue
            # listing must be profile-scoped
            lst = (qa.http(base, "GET", "/api/chats").json() or {}).get("chats", [])
            if not any(c.get("chat_id") == chat_id for c in lst):
                rep_fails += 1
                defects.add(f"CHAT-{profile}-list", "chat_flow",
                            f"GET /api/chats after creating a {profile} chat",
                            "the new chat appears in the active profile's list",
                            f"chat {chat_id} missing from {len(lst)} rows",
                            "ishti/contract_api.py")

            chat_runs = 0
            for idx, (category, text, expect_steps, expect_t2) in enumerate(prompts):
                cid = f"qa-{rep}-{profile}-{idx}-{uuid.uuid4().hex[:6]}"
                before = last_seq(base)
                t0 = time.perf_counter()
                send = qa.http(base, "POST", f"/api/chats/{chat_id}/messages",
                               body={"text": text, "client_msg_id": cid}, timeout=30)
                sent = send.json() or {}
                run_id = sent.get("run_id")
                row = {"rep": rep, "profile": profile, "category": category,
                       "prompt": text, "send_status": send.status, "run_id": run_id,
                       "duplicate": sent.get("duplicate"), "checks": {}}
                if send.status not in (200, 201) or not run_id:
                    row["checks"]["send_ok"] = False
                    row["error"] = send.body[:300]
                    rep_fails += 1
                    result["runs"].append(row)
                    qa.dump_json(output, result)
                    continue
                chat_runs += 1
                run = wait_for_run(base, run_id, args.run_timeout)
                row["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                row["state"] = run.get("state")
                steps = run.get("steps") or []
                row["step_count"] = len(steps)
                events = events_for(base, before)
                row["event_types"] = sorted({e.get("type") for e in events})
                answer = run.get("text") or ""

                reply_ok = run.get("state") == "done" and bool(answer.strip())
                row["checks"]["reply_arrives"] = reply_ok
                if not reply_ok:
                    rep_fails += 1
                    defects.add(f"CHAT-{profile}-reply-{category}-{rep}", "chat_flow",
                                f"send '{text[:60]}' as {profile} (rep {rep})",
                                "run reaches 'done' with a non-empty reply",
                                f"state={run.get('state')} error={run.get('error')} text={answer[:120]!r}",
                                "ishti/run_executor.py")

                if expect_steps:
                    steps_ok = len(steps) >= 1 or "run_step" in row["event_types"]
                    row["checks"]["steps_emitted"] = steps_ok
                    if not steps_ok:
                        rep_fails += 1
                        defects.add(f"CHAT-{profile}-steps-{category}-{rep}", "chat_flow",
                                    f"send '{text[:60]}' as {profile} (rep {rep})",
                                    "a tool-needing prompt emits at least one run_step",
                                    f"steps={len(steps)} events={row['event_types']}",
                                    "ishti/run_executor.py")
                if expect_t2:
                    asks = any(m in answer.lower() for m in CONFIRM_MARKERS)
                    row["checks"]["t2_asks_confirm"] = asks
                    if not asks:
                        rep_fails += 1
                        defects.add(f"CHAT-{profile}-t2-{category}-{rep}", "chat_flow",
                                    f"send '{text[:60]}' as {profile} (rep {rep})",
                                    "a T2 WhatsApp draft asks the owner to confirm before acting",
                                    f"reply did not ask for confirmation: {answer[:160]!r}",
                                    "ishti/test_mode.py")

                # duplicate-run guard: same client_msg_id must not launch a second run
                dup = qa.http(base, "POST", f"/api/chats/{chat_id}/messages",
                              body={"text": text, "client_msg_id": cid}, timeout=30)
                dup_data = dup.json() or {}
                dup_ok = (bool(dup_data.get("duplicate"))
                          and dup_data.get("run_id") == run_id)  # same run, no second launch
                row["checks"]["no_duplicate_run"] = dup_ok
                if not dup_ok:
                    rep_fails += 1
                    defects.add(f"CHAT-{profile}-dup-{rep}-{idx}", "chat_flow",
                                f"resend client_msg_id {cid}",
                                "second send returns duplicate=true with no new run",
                                f"status={dup.status} body={dup.body[:200]}",
                                "ishti/contract_api.py")
                result["runs"].append(row)
                qa.dump_json(output, result)

        result["repetitions"].append({"rep": rep, "fails": rep_fails,
                                      "clean": rep_fails == 0, "at": qa.iso_now()})
        qa.dump_json(output, result)

    # cross-profile isolation (active profile is 'business' after the loop)
    iso = verify_isolation(base, result, defects)
    result["isolation"] = iso
    if not iso.get("ok", False):
        result["repetitions"].append({"rep": "isolation", "fails": 1, "clean": False})

    result["new_defects"] = len(defects.rows) - defects_before
    result["finished_at"] = qa.iso_now()
    qa.dump_json(output, result)
    defects.save()
    clean = sum(1 for x in result["repetitions"] if x.get("clean"))
    print(f"Chat flow: {len(result['runs'])} runs, {clean}/{len(result['repetitions'])} clean reps; "
          f"report={output}")
    return 0 if all(x.get("clean") for x in result["repetitions"]) else 1


def verify_isolation(base: str, result: dict, defects) -> dict:
    """Active profile is 'business'. A personal chat/run must be invisible from here."""
    out = {"ok": True, "checks": {}}
    pers = qa.http(base, "PUT", "/api/profile", body={"active": "personal"})
    ch = qa.http(base, "POST", "/api/chats", body={"profile": "personal"}).json() or {}
    p_chat = ch.get("chat_id")
    send = qa.http(base, "POST", f"/api/chats/{p_chat}/messages",
                   body={"text": "isolation probe personal", "client_msg_id": "iso-p"}).json() or {}
    p_run = send.get("run_id")
    wait_for_run(base, p_run, 30)

    qa.http(base, "PUT", "/api/profile", body={"active": "business"})
    biz_chats = [c.get("chat_id") for c in
                 (qa.http(base, "GET", "/api/chats").json() or {}).get("chats", [])]
    out["checks"]["personal_chat_hidden"] = p_chat not in biz_chats

    cross = qa.http(base, "GET", f"/api/runs/{p_run}")
    out["checks"]["cross_profile_run_404"] = cross.status == 404

    ev = events_for(base, 0)
    leaked = [e for e in ev if (e.get("data") or {}).get("run_id") == p_run]
    out["checks"]["events_filtered"] = not leaked

    msgs = qa.http(base, "GET", f"/api/chats/{p_chat}/messages")
    out["checks"]["cross_profile_messages_404"] = msgs.status == 404

    # restore personal
    qa.http(base, "PUT", "/api/profile", body={"active": "personal"})

    out["ok"] = all(out["checks"].values())
    if not out["ok"]:
        failed = [k for k, v in out["checks"].items() if not v]
        defects.add("CHAT-isolation", "chat_flow",
                    "create a personal chat+run, switch to business, query personal ids",
                    "every cross-profile lookup is hidden/filtered/404",
                    f"failed checks: {failed}",
                    "ishti/runs.py")
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ISHTI profile-isolated chat flow over the API.")
    parser.add_argument("--url", default="http://127.0.0.1:8899")
    parser.add_argument("--output", default=str(qa.AUDIT / "qa_chat_flow.json"))
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--prompts", type=int, default=0, help="0 = all 30 prompts")
    parser.add_argument("--run-timeout", type=float, default=180.0)
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
