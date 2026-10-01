"""
ISHTI Structured Error & Diagnostic Logging Engine
Records all subsystem errors, exceptions, and warnings with full tracebacks,
context, self-healing status, and concrete user fix recommendations.
"""

import os
import sys
import time
import json
import traceback
import uuid
from typing import Dict, Any, List, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS_DIR = os.path.join(ROOT_DIR, "data", "logs")
ERROR_JSON_PATH = os.path.join(LOGS_DIR, "error_records.json")
ERROR_MD_PATH = os.path.join(LOGS_DIR, "error_records.md")

os.makedirs(LOGS_DIR, exist_ok=True)


class ErrorLogger:
    @staticmethod
    def _generate_suggested_fix(subsystem: str, exc_type: str, message: str) -> str:
        """Generates concrete, actionable guidance on how the user or agent can fix the error."""
        msg_lower = message.lower()

        if "11434" in msg_lower or "connection refused" in msg_lower or "ollama" in msg_lower:
            return "Ollama service appears offline. Open a terminal and run 'ollama serve' or restart the Ollama Windows taskbar application."
        
        if "charmap" in msg_lower or "unicodeencodeerror" in msg_lower:
            return "Windows console cp1252 encoding limit. Reconfigure stream with `sys.stdout.reconfigure(encoding='utf-8')`."
        
        if "timeout" in msg_lower:
            return "Operation exceeded timeout threshold. Verify network connectivity, proxy settings, or increase timeout in config.json."
        
        if "chrome" in msg_lower or "playwright" in msg_lower:
            return "Verify that Google Chrome is installed at 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' and profile dir has write permissions."
        
        if "202" in msg_lower or "403" in msg_lower or "429" in msg_lower or "bot" in msg_lower:
            return "Search provider returned anti-bot or rate-limit challenge. MultiProviderSearch automatically fails over to alternate providers."

        if "module" in msg_lower or "importerror" in msg_lower:
            return "Missing python dependency. Run `D:\\ISHTI\\venv\\Scripts\\pip.exe install <package_name>` inside the project virtual environment."

        return f"Inspect stack trace in {subsystem} subsystem. Check relevant configuration in orchestrator/config.json."

    @classmethod
    def log_error(
        cls,
        subsystem: str,
        error: Any,
        user_prompt: str = "",
        context: str = "",
        severity: str = "ERROR",
        suggested_fix: Optional[str] = None,
        traceback_str: Optional[str] = None,
        resolved: bool = False,
    ) -> Dict[str, Any]:
        """
        Records a detailed error record to both JSON and Markdown log files.
        """
        error_id = f"ERR-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        exc_type = type(error).__name__ if isinstance(error, Exception) else "SystemError"
        error_msg = str(error)

        if not traceback_str:
            if isinstance(error, Exception) and error.__traceback__:
                traceback_str = "".join(traceback.format_tb(error.__traceback__))
            else:
                traceback_str = traceback.format_exc()
                if "NoneType: None" in traceback_str:
                    traceback_str = "No active traceback available."

        if not suggested_fix:
            suggested_fix = cls._generate_suggested_fix(subsystem, exc_type, error_msg)

        record = {
            "error_id": error_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "subsystem": subsystem.upper(),
            "severity": severity.upper(),
            "exception_type": exc_type,
            "error_message": error_msg,
            "user_prompt": user_prompt,
            "context": context,
            "suggested_fix": suggested_fix,
            "traceback": traceback_str,
            "resolved": resolved,
        }

        # 1. Update JSON store
        records = cls.get_all_errors()
        records.insert(0, record)  # Newest first
        try:
            with open(ERROR_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(records[:200], f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ErrorLogger] Failed to write JSON log: {e}")

        # 2. Append to Markdown store
        cls._append_markdown_log(record)

        print(f"[ERROR_LOGGED] [{record['subsystem']}] {record['error_id']} - {record['error_message'][:120]}")
        return record

    @classmethod
    def _append_markdown_log(cls, record: Dict[str, Any]) -> None:
        md_entry = (
            f"\n### [{record['severity']}] {record['error_id']} — {record['subsystem']}\n"
            f"- **Timestamp**: `{record['timestamp']}`\n"
            f"- **Exception**: `{record['exception_type']}`: {record['error_message']}\n"
            f"- **User Prompt**: `{record['user_prompt'] or 'N/A'}`\n"
            f"- **Context**: {record['context'] or 'N/A'}\n"
            f"- **Suggested Fix**: {record['suggested_fix']}\n"
            f"- **Status**: `{'RESOLVED' if record['resolved'] else 'REQUIRES_ATTENTION'}`\n\n"
            f"```text\n{record['traceback'].strip()}\n```\n"
            f"---\n"
        )
        try:
            header = "# ISHTI Subsystem Error Audit & Remediation Log\n\n" if not os.path.exists(ERROR_MD_PATH) else ""
            with open(ERROR_MD_PATH, "a", encoding="utf-8") as f:
                if header:
                    f.write(header)
                f.write(md_entry)
        except Exception as e:
            print(f"[ErrorLogger] Failed to append Markdown log: {e}")

    @classmethod
    def get_all_errors(cls) -> List[Dict[str, Any]]:
        if os.path.exists(ERROR_JSON_PATH):
            try:
                with open(ERROR_JSON_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    @classmethod
    def mark_resolved(cls, error_id: str) -> bool:
        records = cls.get_all_errors()
        updated = False
        for r in records:
            if r.get("error_id") == error_id:
                r["resolved"] = True
                updated = True
        if updated:
            with open(ERROR_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
        return updated

    @classmethod
    def clear_all(cls) -> bool:
        try:
            with open(ERROR_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump([], f)
            with open(ERROR_MD_PATH, "w", encoding="utf-8") as f:
                f.write("# ISHTI Subsystem Error Audit & Remediation Log\n\n*Log cleared by user.*\n")
            return True
        except Exception:
            return False
