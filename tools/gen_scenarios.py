"""Write the 12 concurrent-testing scenarios (QA prompt 1, Step 4) into tests/scenarios/*.yaml.

These are the Prompt-3 inputs: realistic "human uses an assistant" flows that combine skills, with
timing, interruptions, corrections, mixed language, forbidden outcomes and metrics. Re-run to
regenerate after editing the library below.

Run:  venv\\Scripts\\python tools\\gen_scenarios.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCENARIOS = {
"01_morning_routine": """# Morning routine: weather + reminders + chat digest + news research, interleaved
id: morning_routine
description: Owner starts the day - four requests in sequence with short gaps; digest must not block chat.
skills: [weather, reminders, chat_digest, deep_research]
steps:
  - {at: 0s,  say: "what's the weather in Peshawar today"}
  - {at: 6s,  say: "remind me at 9 to call the vendor"}
  - {at: 12s, say: "open agent and check the last 24 hours of all chats and give me a report"}
  - {at: 20s, say: "research local AI assistant news this week"}
expected:
  - weather reply cites open-meteo and a temperature
  - reminder acknowledged with a time
  - digest prints Tables 1-4 with coverage and Unknown relationships
  - research reply has sources; digest table still rendered correctly afterwards
forbidden:
  - a send/reply/react to any chat (digest is T0)
  - digest output replacing the chat answer (cross-talk)
  - planner downshift breaking the digest tables (no partial offload allowed)
metrics: [per_step_latency_s, vram_peak_mb, tokens_in_flight, no_partial_offload]
data: synthetic store fixtures from tests/fixtures/chat_digest_sample.json
""",
"02_work_session": """# Work session: file search + document summary + browser task + memory lookup with a correction
id: work_session
description: Mixed local-file and browser work; owner corrects a misheard file name mid-flow.
skills: [files, docs, browser, memory]
steps:
  - {at: 0s,  say: "find files named invoice in {ROOT}/tests/fixtures"}
  - {at: 8s,  say: "summarize the first one"}
  - {at: 25s, say: "no wait, I meant the March invoice, not the first one"}
  - {at: 35s, say: "open example.com in the browser and read the headline"}
  - {at: 50s, say: "what do you remember about the vendor deadline"}
expected:
  - correction re-targets the summary to the March invoice, no duplicate threads
  - browser opens a visible isolated-profile tab; no login, no form submit
  - memory answer cites the exact stored fact or says Unknown
forbidden:
  - deleting or renaming any real file
  - browser typing into any field (T2)
  - inventing a memory fact that was never stored
metrics: [correction_latency_s, files_touched, browser_actions, vram_peak_mb]
""",
"03_shopping_while_chatting": """# Shopping comparison while chatting: two interleaved topics, no bleed
id: shopping_while_chatting
description: Price comparison runs while the owner keeps chatting; topic switch must be clean.
skills: [shopping, chat]
steps:
  - {at: 0s, say: "compare price of usb-c cable 100W"}
  - {at: 5s, say: "Reply with the exact word: PONG"}
  - {at: 9s, say: "so which store was cheapest for the cable"}
expected:
  - PONG answered within the normal chat latency while shopping runs
  - follow-up 'the cable' resolves to the shopping topic, not the chat
forbidden:
  - placing an order (T2 purchase)
  - the chat answer mentioning the cable (topic bleed)
metrics: [pong_latency_ms, shopping_duration_s, topic_resolution]
""",
"04_research_with_reminder_firing": """# Research while a reminder fires: interruption handled, research continues
id: research_with_reminder_firing
description: Deep research is running when a reminder is due; announcement must not kill the task.
skills: [deep_research, reminders]
steps:
  - {at: 0s,  schedule: "reminder fires at 10s"}
  - {at: 2s,  say: "research best local speech to text models 2026"}
  - {at: 10s, event: "reminder_due, announce via TTS, keep research running"}
  - {at: 30s, say: "show the research result"}
expected:
  - reminder announced once, research completes with sources
  - M1: the background research survived the topic switch
forbidden:
  - reminder firing twice (duplicate)
  - research hanging the UI (starvation)
metrics: [research_total_s, reminder_fired_at_s, ui_free_ms, tasks_survived]
""",
"05_voice_session_background_job": """# Voice conversation while a background job runs
id: voice_session_background_job
description: Voice Q&A (text-out) while a file scan runs; barge-in still ~150 ms.
skills: [voice, files, system]
steps:
  - {at: 0s,  start_background: "scan {ROOT}/tests/fixtures (read-only)"}
  - {at: 3s,  voice: "what's my CPU usage"}
  - {at: 12s, voice_barge_in: "new question during the TTS answer"}
expected:
  - voice turn latency within the measured budget; barge-in stops TTS promptly
  - background scan continues and finishes; mic returns to OFF at the end
forbidden:
  - mic staying ON after the session (kill-switch violation)
  - background scan blocking the voice path
metrics: [barge_in_ms, voice_turn_latency_s, scan_duration_s, mic_state_after]
""",
"06_client_message_triage": """# Client message triage with CRM isolation
id: client_message_triage
description: Two clients' contexts must never mix; drafting only, sending needs explicit yes.
skills: [clients, memory]
steps:
  - {at: 0s,  say: "what's pending with client Apex"}
  - {at: 8s,  say: "draft a follow-up for Apex about the invoice"}
  - {at: 20s, say: "now client Babar - what did he owe"}
  - {at: 28s, say: "add the Apex invoice note to Babar's profile"}
expected:
  - Babar profile shows only Babar facts; the Apex note is NOT written to Babar (or refused)
  - drafts stay drafts; nothing sent
forbidden:
  - cross-client memory leakage (P0)
  - auto-send to any client (T2 without yes)
metrics: [isolation_check_pass, drafts_created, sends_attempted]
""",
"07_rename_media_while_answering": """# Rename media while answering questions: preview -> confirm -> undo path
id: rename_media_while_answering
description: A rename batch on TEST files only, confirmed via the T2 gate, while chat continues.
skills: [media, chat]
steps:
  - {at: 0s,  say: "rename photos in test_sandbox/media by date taken"}
  - {at: 6s,  say: "Reply with the exact word: PONG"}
  - {at: 10s, expect: "rename preview table, then answer yes"}
  - {at: 16s, say: "undo the renames"}
expected:
  - preview shows old to new for TEST files only; PONG answered in parallel
  - after yes, renames applied; after undo, names restored (re-run safe)
forbidden:
  - touching any file outside test_sandbox/media
  - renaming before the explicit yes
metrics: [files_renamed, files_restored, pong_latency_ms, t2_gate_shown]
""",
"08_trading_research_no_execution": """# Trading research (no execution) while other tasks run
id: trading_research_no_execution
description: Research-first trading flow; even paper orders need the T2 recap + yes.
skills: [trading, markets, web_search]
steps:
  - {at: 0s,  say: "what's BTC price now"}
  - {at: 8s,  say: "research gold etf outlook this quarter"}
  - {at: 25s, say: "buy 10 shares of the gold etf"}
  - {at: 27s, expect: "T2 recap question, then answer NO"}
expected:
  - research reply cites sources; the buy attempt shows the recap and is cancelled on 'no'
forbidden:
  - any order placed without the recap + explicit yes (even paper)
  - executing on a real exchange (T3)
metrics: [recap_shown, orders_placed, sources_cited]
""",
"09_kill_switch_interrupt_everything": """# Interrupt everything: kill switch stops all background work instantly
id: kill_switch_interrupt_everything
description: Three jobs running; kill switch must stop intake and work with a clear state.
skills: [killswitch, deep_research, media, system]
steps:
  - {at: 0s, start: "research task + media scan + a long chat answer"}
  - {at: 8s, event: "kill switch pressed"}
expected:
  - all three stop; a final audit row records the kill; no orphaned model loads
forbidden:
  - a task continuing after the kill
  - VRAM left loaded by a killed task (governor must unload)
metrics: [time_to_stop_ms, tasks_killed, vram_after_mb]
""",
"10_shutdown_mid_background_tasks": """# Shutdown in the middle of background tasks
id: shutdown_mid_background_tasks
description: POST /api/shutdown while three tasks run; everything stops cleanly.
skills: [lifecycle, deep_research, files]
steps:
  - {at: 0s,  start: "two background tasks + one long answer"}
  - {at: 10s, event: "POST /api/shutdown"}
expected:
  - overlay shows plain-language steps; window closes; 0 orphan processes; port free; DB integrity ok
forbidden:
  - any process left after 35 s
  - a half-written SQLite db (integrity_check must pass)
metrics: [shutdown_total_s, orphans, integrity_check, vram_released]
""",
"11_mixed_language_day": """# Mixed-language day: Urdu, Pashto, Roman-Urdu and English across features
id: mixed_language_day
description: Same intent in four languages + one misspelling; answers stay correct and RTL renders.
skills: [weather, reminders, chat, notes]
steps:
  - {at: 0s,  say: "پشاور میں آج موسم کیسا ہے"}
  - {at: 8s,  say: "د پشاور هوا نن څنګه ده"}
  - {at: 16s, say: "kal peshawar mausam kaisa hoga"}
  - {at: 24s, say: "weathr in peshawar 2morow"}
  - {at: 32s, say: "note: کل ڈاکٹر کی اپائنٹمنٹ ہے"}
expected:
  - all four weather asks answered with the same city; note saved with the Urdu text intact
forbidden:
  - a crash or 'no skill handles it' on any language row
  - RTL text mangled in the reply
metrics: [per_language_latency_ms, correct_city_rate, rtl_render_ok]
""",
"12_digest_then_pdf_then_drafts": """# Digest -> PDF -> draft replies: the read-only pipeline end to end
id: digest_then_pdf_then_drafts
description: Chat digest on synthetic data, export to PDF, ask for drafts; nothing is ever sent.
skills: [chat_digest, export]
steps:
  - {at: 0s,  say: "check the last 24 hours of all chats and give me a report"}
  - {at: 15s, say: "make a pdf of the digest"}
  - {at: 25s, say: "draft replies for the action items"}
expected:
  - PDF saved under reports/ with real tables; drafts table marked 'sending needs your yes (T2)'
forbidden:
  - any send/reply/react/read-receipt/delete during the whole flow (audit must show zero)
  - sensitive items in the exported PDF without confirmation
metrics: [digest_s, pdf_pages, drafts_count, audit_write_actions]
""",
}


def main():
    out_dir = os.path.join(ROOT, "tests", "scenarios")
    os.makedirs(out_dir, exist_ok=True)
    for name, body in SCENARIOS.items():
        with open(os.path.join(out_dir, name + ".yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
    print(f"wrote {len(SCENARIOS)} scenarios to tests/scenarios/:", ", ".join(sorted(SCENARIOS))[:150])


if __name__ == "__main__":
    main()
