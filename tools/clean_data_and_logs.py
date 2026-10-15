"""
Safe Database, Logs, and Runtime State Cleaner for ISHTI.
Creates a complete backup in backups/pre_clean_reset_<timestamp>/ before purging
any database, session, vector store, or log files.
"""

import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, "data")
BACKUP_BASE = os.path.join(ROOT, "backups")

# Things to preserve (do NOT delete)
PRESERVE_FILES = {
    "settings.json",
    "api_token",
    "audio_devices.json",
    "mic_state.json",
    "model_catalog.json",
    "ollama_settings.json",
    "mcp_tokens.json",
    "remote_policy.yaml",
    "ishti-qwen2.5-14b.modelfile.bak",
    "ishti-qwen2.5-coder-14b.modelfile.bak",
}

PRESERVE_DIRS = {
    "bin",
    "kokoro",
    "piper",
    "models",
    "chrome_profile",
    "mcp_browser_profile",
}

INITIAL_REFLECTIONS = """# ISHTI System Reflections & Self-Healing Audit Log
*Rolling log of errors, failure root causes, remediation strategies, and self-healing resolutions across sessions.*

---

## Log Format
Each self-healing event is logged using the following schema:
- **Timestamp**: ISO-8601 local timestamp
- **Task ID / Step**: Identifying name and subtask index
- **Active Model**: Model in execution
- **Error Category**: `ToolExecutionError`, `SelectorTimeout`, `ModelOutputMalformed`, `NetworkTimeout`, `PermissionBlocked`
- **Error Details**: Raw error message and stack excerpt
- **Attempted Fix**: Strategy executed (retry with sanitized prompt, model fallback, alternative selector, backoff)
- **Outcome**: `RESOLVED` or `ESCALATED_TO_USER`
- **Learned Pattern**: Rule or selector update propagated to Skill memory

---

### Initialized: System Ready
- **Timestamp**: 2026-10-03T11:00:00
- **Status**: Clean installation initialized
"""


def clean():
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_BASE, f"clean_reset_backup_{ts}")
    print(f"[*] Creating backup directory: {backup_dir}")
    os.makedirs(backup_dir, exist_ok=True)
    os.makedirs(os.path.join(backup_dir, "data"), exist_ok=True)

    # 1. Backup all files in data/ that are not models/binaries
    print("[*] Backing up data files...")
    for item in os.listdir(DATA_DIR):
        src = os.path.join(DATA_DIR, item)
        dst = os.path.join(backup_dir, "data", item)
        if item in PRESERVE_DIRS:
            continue
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        except Exception as e:
            print(f"  [warn] Backup failed for {item}: {e}")

    # Backup reports/*.pdf
    reports_dir = os.path.join(ROOT, "reports")
    if os.path.exists(reports_dir):
        os.makedirs(os.path.join(backup_dir, "reports"), exist_ok=True)
        for f in os.listdir(reports_dir):
            if f.endswith(".pdf"):
                try:
                    shutil.copy2(os.path.join(reports_dir, f), os.path.join(backup_dir, "reports", f))
                except Exception as e:
                    print(f"  [warn] Could not back up reports/{f}: {e}")

    # Backup scratch/
    scratch_dir = os.path.join(ROOT, "scratch")
    if os.path.exists(scratch_dir):
        try:
            shutil.copytree(scratch_dir, os.path.join(backup_dir, "scratch"), dirs_exist_ok=True)
        except Exception as e:
            print(f"  [warn] Could not back up scratch/: {e}")

    # Backup orchestrator/memory/reflections.md
    orch_reflections = os.path.join(ROOT, "orchestrator", "memory", "reflections.md")
    if os.path.exists(orch_reflections):
        try:
            shutil.copy2(orch_reflections, os.path.join(backup_dir, "orch_reflections.md"))
        except Exception as e:
            print(f"  [warn] Could not back up reflections.md: {e}")

    print(f"[+] Backup completed successfully in {backup_dir}")

    # 2. Clean data/ files
    print("[*] Cleaning databases, logs, and state files in data/...")
    
    # Clean databases (.db, .db-wal, .db-shm)
    for fname in os.listdir(DATA_DIR):
        fpath = os.path.join(DATA_DIR, fname)
        if fname in PRESERVE_FILES or fname in PRESERVE_DIRS:
            continue

        # DB files
        if any(fname.endswith(ext) for ext in (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")):
            try:
                os.remove(fpath)
                print(f"  [-] Removed DB: {fname}")
            except Exception as e:
                print(f"  [!] Failed to remove {fname}: {e}")

        # Log and jsonl files
        elif any(fname.endswith(ext) for ext in (".log", ".jsonl", ".out", ".err")):
            try:
                os.remove(fpath)
                print(f"  [-] Removed log/output: {fname}")
            except Exception as e:
                print(f"  [!] Failed to remove {fname}: {e}")

    # Reset state JSONs & text files
    json_resets = {
        "inflight.json": "{}",
        "self_heal_state.json": "{}",
        "skill_creator_state.json": "{}",
        "watches.json": "{}",
    }
    for jname, initial_val in json_resets.items():
        jpath = os.path.join(DATA_DIR, jname)
        try:
            with open(jpath, "w", encoding="utf-8") as f:
                f.write(initial_val)
            print(f"  [~] Reset {jname} to empty state")
        except Exception as e:
            print(f"  [!] Error writing {jname}: {e}")

    # Remove temporary JSON benchmark / state files
    temp_jsons = [
        "boot_times.json",
        "e2e_vram_lifecycle.json",
        "kv_cache_probe.json",
        "telemetry.jsonl",
        "corrections.jsonl",
        "chat_digest_last.md",
        "feedback.md",
        "paraphrase_report.txt",
    ]
    for tj in temp_jsons:
        tjpath = os.path.join(DATA_DIR, tj)
        if os.path.exists(tjpath):
            try:
                os.remove(tjpath)
                print(f"  [-] Removed runtime state: {tj}")
            except Exception as e:
                print(f"  [!] Error removing {tj}: {e}")

    # Reset reflections.md
    for rpath in (os.path.join(DATA_DIR, "reflections.md"), orch_reflections):
        try:
            with open(rpath, "w", encoding="utf-8") as f:
                f.write(INITIAL_REFLECTIONS)
            print(f"  [~] Reset reflections to clean template: {os.path.relpath(rpath, ROOT)}")
        except Exception as e:
            print(f"  [!] Error resetting {rpath}: {e}")

    # 3. Clean Directories in data/
    dir_cleanups = [
        "sessions",
        "logs",
        "chroma",
        "cache",
        "crops",
        "eval",
        "exports",
        "extracted",
        "finetune",
        "mailmerge",
        "media_output",
        "memes",
        "qr",
        "reach",
        "redacted",
        "s2",
        "sandbox",
        "screenshots",
        "tables",
        "undo",
        "uploads",
        "video_output",
        "vulnscan",
        "clone",
        "charts",
    ]
    for dname in dir_cleanups:
        dpath = os.path.join(DATA_DIR, dname)
        if os.path.exists(dpath):
            try:
                shutil.rmtree(dpath, ignore_errors=True)
                os.makedirs(dpath, exist_ok=True)
                print(f"  [~] Emptied directory: data/{dname}")
            except Exception as e:
                print(f"  [!] Error clearing {dname}: {e}")
        else:
            os.makedirs(dpath, exist_ok=True)

    # 4. Clean reports/*.pdf and scratch/*
    if os.path.exists(reports_dir):
        for f in os.listdir(reports_dir):
            if f.endswith(".pdf"):
                try:
                    os.remove(os.path.join(reports_dir, f))
                except Exception as e:
                    print(f"  [warn] Could not remove reports/{f}: {e}")
        print("  [~] Emptied reports/*.pdf")

    if os.path.exists(scratch_dir):
        for f in os.listdir(scratch_dir):
            fpath = os.path.join(scratch_dir, f)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
                elif os.path.isdir(fpath):
                    shutil.rmtree(fpath, ignore_errors=True)
            except Exception as e:
                print(f"  [warn] Could not remove scratch/{f}: {e}")
        print("  [~] Emptied scratch/")

    # 5. Initialize clean schemas
    print("\n[*] Initializing clean database schemas...")

    import ishti.facts as facts_mod
    f = facts_mod.Facts()
    print("  [+] Initialized data/ishti.db (tables: facts, route_log)")

    import ishti.memstore.memory as arch2_mem
    arch2_mem._conn = None
    c = arch2_mem.conn()
    print("  [+] Initialized data/arch2_memory.db (tables: memories, entities, observations)")

    import ishti.learning.store as learn_mod
    learn_mod.conn()
    print("  [+] Initialized data/learning.db (tables: lessons, feedback, policies)")

    import ishti.audit as audit_mod
    audit_mod._reset_cache()
    audit_mod._db()
    print("  [+] Initialized data/audit.db (tables: actions)")

    from orchestrator.memory.conversation import ConversationManager
    cm = ConversationManager()
    print("  [+] Initialized conversation manager (sessions dir ready)")

    import ishti.core.state as state_mod
    sm = state_mod.ConversationStateManager()
    print("  [+] Initialized data/convo_state.db (tables: convo_state)")

    print("\n[SUCCESS] ISHTI is now 100% clean and ready for a fresh test run!")
    print(f"Backup preserved at: {backup_dir}")


if __name__ == "__main__":
    clean()
