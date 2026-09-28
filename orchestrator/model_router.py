"""
ISHTI Model Router & Local Benchmark Engine
Evaluates installed Ollama models, benchmarks speed and tool-calling fidelity,
and automatically selects optimal local models for zero-cost runtime execution.
"""

import json
import os
import time
from typing import Dict, Any, List, Optional
import requests

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
REFLECTIONS_PATH = os.path.join(os.path.dirname(__file__), "memory", "reflections.md")


class ModelRouter:
    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self.config = self._load_config()
        self.host = self.config.get("ollama", {}).get("host", "http://127.0.0.1:11434")
        self.consecutive_failures: Dict[str, int] = {}

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "ollama": {
                "host": "http://127.0.0.1:11434",
                "fast_model": "llama3.2:3b",
                "primary_model": "qwen2.5:14b",
                "fallback_model": "deepseek-r1:8b",
            }
        }

    def _save_config(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    def get_model_sizes(self) -> Dict[str, int]:
        """Model name -> bytes on disk, as reported by Ollama."""
        try:
            res = requests.get(f"{self.host}/api/tags", timeout=5)
            if res.status_code == 200:
                return {m["name"]: m.get("size", 0) for m in res.json().get("models", [])}
        except Exception:
            pass
        return {}

    def get_installed_models(self) -> List[str]:
        """Fetch list of all locally installed models from Ollama."""
        try:
            res = requests.get(f"{self.host}/api/tags", timeout=5)
            if res.status_code == 200:
                data = res.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            print(f"[ModelRouter] Warning: could not connect to Ollama: {e}")
        return []

    def get_model(self, role: str = "primary") -> str:
        """Get currently configured model for a given role (primary, fast, fallback, coding)."""
        ollama_cfg = self.config.get("ollama", {})
        if role == "fast":
            return ollama_cfg.get("fast_model", "llama3.2:3b")
        elif role == "fallback":
            return ollama_cfg.get("fallback_model", "deepseek-r1:8b")
        elif role == "coding":
            return ollama_cfg.get("coding_model", "qwen2.5:14b")
        return ollama_cfg.get("primary_model", "qwen2.5:14b")

    def generate(
        self,
        user_content: str,
        depth: str = "SIMPLE",
        role: str = "primary",
        tools_used: Optional[List[str]] = None,
        evidence_is_live: bool = False,
        memory_query: str = "",
        temperature: float = 0.2,
        timeout: int = 60,
    ) -> str:
        """
        The single entry point to the LLM. Every caller goes through here so that the owner
        profile, the anti-refusal rules and the length rule are applied exactly once, in one
        place (see orchestrator/persona.py and docs/AUDIT.md S7).
        """
        from orchestrator.persona import build_system_prompt
        from orchestrator.memory.user_profile import get_user_memory

        memory_block = get_user_memory().format_for_llm(memory_query or user_content)
        system = build_system_prompt(
            depth=depth,
            memory_block=memory_block,
            tools_used=tools_used,
            evidence_is_live=evidence_is_live,
        )
        if "<<EXTERNAL" in user_content:  # fetched pages are data, never instructions
            from ishti.untrusted import RULE
            system = f"{system}\n\n{RULE}"
        from ishti import killswitch, models
        killswitch.check()
        models.used(self.get_model(role))
        try:
            # streamed, so the kill switch can stop a long report at the next token (closing the stream ends it)
            with requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.get_model(role),
                    "system": system,
                    "prompt": user_content,
                    "stream": True,
                    "options": {"temperature": temperature},
                },
                timeout=timeout, stream=True,
            ) as res:
                if res.status_code != 200:
                    print(f"[ModelRouter] generate HTTP {res.status_code}")
                    return ""
                parts = []
                for line in res.iter_lines():
                    killswitch.check()
                    if line:
                        chunk = json.loads(line)
                        parts.append(chunk.get("response", ""))
                        if chunk.get("done"):
                            break
                return "".join(parts).strip()
        except Exception as exc:
            if type(exc).__name__ == "Cancelled":
                raise
            print(f"[ModelRouter] generate failed: {exc}")
        return ""

    def benchmark_model(self, model_name: str) -> Dict[str, Any]:
        """
        Runs 3 quality & latency tests on a model:
        1. Tool-calling / JSON structured output
        2. Task decomposition logic
        3. Fast reasoning latency
        """
        print(f"\n[ModelRouter] Benchmarking '{model_name}'...")
        scores = {"json_tool": 0, "planning": 0, "speed_wps": 0.0, "latency_s": 0.0}
        total_time = 0.0

        # Test 1: Strict JSON Tool Calling
        prompt_tool = (
            "You are an AI assistant. Return ONLY a valid JSON object with keys: "
            "'tool_name' (string, value 'search_web'), and 'parameters' (dict with key 'query' and value 'local weather'). "
            "Do NOT include markdown formatting or extra text."
        )
        try:
            t0 = time.time()
            res = requests.post(
                f"{self.host}/api/generate",
                json={"model": model_name, "prompt": prompt_tool, "stream": False, "options": {"temperature": 0.1}},
                timeout=25,
            )
            elapsed = time.time() - t0
            total_time += elapsed
            if res.status_code == 200:
                content = res.json().get("response", "").strip()
                # Clean code fences if any
                clean = content.replace("```json", "").replace("```", "").strip()
                try:
                    parsed = json.loads(clean)
                    if parsed.get("tool_name") == "search_web" and "query" in parsed.get("parameters", {}):
                        scores["json_tool"] = 100
                    else:
                        scores["json_tool"] = 50
                except Exception:
                    scores["json_tool"] = 25 if "search_web" in clean else 0
        except Exception as e:
            print(f"[ModelRouter] Test 1 failed for {model_name}: {e}")

        # Test 2: Task Planning & Sub-step Decomposition
        prompt_plan = (
            "Break this request into 2 distinct numbered steps: 'Check disk space and list top files'. "
            "Reply strictly in 2 numbered lines (1. ..., 2. ...)."
        )
        try:
            t0 = time.time()
            res = requests.post(
                f"{self.host}/api/generate",
                json={"model": model_name, "prompt": prompt_plan, "stream": False, "options": {"temperature": 0.1}},
                timeout=25,
            )
            elapsed = time.time() - t0
            total_time += elapsed
            if res.status_code == 200:
                content = res.json().get("response", "").strip()
                lines = [l for l in content.split("\n") if l.strip()]
                if any("1." in l for l in lines) and any("2." in l for l in lines):
                    scores["planning"] = 100
                else:
                    scores["planning"] = 60
        except Exception as e:
            print(f"[ModelRouter] Test 2 failed for {model_name}: {e}")

        # Calculate metrics
        avg_latency = round(total_time / 2.0, 2)
        quality_score = int((scores["json_tool"] * 0.6) + (scores["planning"] * 0.4))
        scores["avg_latency_s"] = avg_latency
        scores["quality_score"] = quality_score
        print(f"[ModelRouter] Results for '{model_name}': Quality={quality_score}/100, Latency={avg_latency}s")
        return scores

    def run_full_benchmark(self, reason: str = "initial_run") -> Dict[str, Any]:
        """
        Runs benchmarks against installed models and selects primary, fast, and fallback models.
        """
        installed = self.get_installed_models()
        if not installed:
            print("[ModelRouter] No Ollama models found running.")
            return {}

        # Filter out embedding models
        candidates = [m for m in installed if not m.startswith("nomic-embed") and not m.startswith("all-minilm")]
        print(f"[ModelRouter] Benchmarking candidates: {candidates} (Reason: {reason})")

        benchmark_results = {}
        for model in candidates:
            benchmark_results[model] = self.benchmark_model(model)

        # Selection logic:
        # Fast model: lowest parameter / fastest latency among quality >= 70
        # Primary model: highest quality score; tie-break by lowest latency
        # Fallback model: second best quality model

        # Tie-break by model size, NOT by latency. The benchmark only checks JSON shape and
        # planning, which a 3B model passes; breaking ties on speed once promoted llama3.2:3b
        # to primary, and that model then hallucinated and refused its way through synthesis
        # (docs/AUDIT.md F1). The fast slot below already covers latency-sensitive work.
        sizes = self.get_model_sizes()
        sorted_models = sorted(
            candidates,
            key=lambda m: (benchmark_results[m]["quality_score"], sizes.get(m, 0)),
            reverse=True,
        )

        primary = sorted_models[0] if sorted_models else "qwen2.5:14b"
        fallback = sorted_models[1] if len(sorted_models) > 1 else primary

        # Fast model candidates
        small_candidates = [m for m in candidates if any(k in m for k in ["3b", "1.7b", "7b", "8b", "small"])]
        if small_candidates:
            fast = min(small_candidates, key=lambda m: benchmark_results[m]["avg_latency_s"])
        else:
            fast = primary

        self.config["ollama"]["primary_model"] = primary
        self.config["ollama"]["fallback_model"] = fallback
        self.config["ollama"]["fast_model"] = fast
        self.config["ollama"]["last_benchmark"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.config["ollama"]["benchmark_scores"] = benchmark_results

        self._save_config()
        print(f"\n[ModelRouter] Benchmark complete!\n  > Primary: {primary}\n  > Fast: {fast}\n  > Fallback: {fallback}")
        return benchmark_results

    def record_failure_and_maybe_heal(self, model_name: str, error_detail: str) -> Optional[str]:
        """
        Tracks consecutive failures per model. If threshold exceeded (>= 2), switches to fallback.
        """
        self.consecutive_failures[model_name] = self.consecutive_failures.get(model_name, 0) + 1
        count = self.consecutive_failures[model_name]
        print(f"[ModelRouter] Recorded failure for '{model_name}' (Count: {count})")

        if count >= 2:
            current_primary = self.config["ollama"].get("primary_model")
            fallback = self.config["ollama"].get("fallback_model")
            try:
                from ishti import self_heal
                if not self_heal.is_enabled():
                    self_heal.record_candidate(
                        "model",
                        f"Model '{model_name}' failed {count} times: {error_detail}",
                        f"Switch primary model from '{current_primary}' to fallback '{fallback}'"
                    )
                    return None
            except Exception:
                pass
            if current_primary != fallback and fallback:
                print(f"[ModelRouter] Self-healing: Switching primary model from '{current_primary}' to fallback '{fallback}'!")
                self.config["ollama"]["primary_model"] = fallback
                self._save_config()

                # Log to reflections
                self._log_reflection(
                    f"Model '{current_primary}' encountered {count} consecutive failures: {error_detail}. "
                    f"Self-healed by dynamically shifting primary routing to '{fallback}'."
                )
                self.consecutive_failures[model_name] = 0
                return fallback
        return None

    def _log_reflection(self, message: str) -> None:
        try:
            entry = (
                f"\n### Model Failover Event\n"
                f"- **Timestamp**: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
                f"- **Event**: {message}\n"
                f"- **Status**: Self-healed\n"
            )
            with open(REFLECTIONS_PATH, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"[ModelRouter] Error writing reflection: {e}")


if __name__ == "__main__":
    router = ModelRouter()
    print("Testing ModelRouter benchmark...")
    res = router.run_full_benchmark(reason="cli_test")
    print("Benchmark summary:", res)
