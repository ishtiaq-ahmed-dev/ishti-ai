"""Create + verify the local Flowise chatflow for ISHTI (idempotent).

Builds a minimal 3-node canvas (ChatLocalAI -> LLM Chain <- Prompt Template) around the
genuine anchor IDs read from Flowise 2.2.8's own LLM Chain template, POSTs it to the local
Flowise (loopback-only container), prints the flow id, then runs one prediction to prove
the wire end-to-end. Ollama serves the model (host.docker.internal from the container).

    venv\\Scripts\\python -X utf8 tools\\flowise_make_flow.py
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:3010"
FLOW_NAME = "ISHTI Local Research"
OLLAMA_MODEL = "qwen2.5:3b"  # small: provable even while the GPU is busy; swap freely in the UI


def api(path: str, payload: dict | None = None, timeout: int = 20) -> object:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def canvas() -> dict:
    llm = {
        "width": 300, "height": 442, "id": "chatLocalAI_0",
        "position": {"x": 175.2, "y": 101.1}, "type": "customNode",
        "data": {
            "id": "chatLocalAI_0", "label": "ChatLocalAI", "name": "chatLocalAI",
            "version": 2, "type": "ChatLocalAI", "category": "Chat Models",
            "baseClasses": ["ChatOpenAI", "BaseChatModel", "BaseLanguageModel", "Runnable"],
            "description": "Use open source LLMs in Ollama, LM studio, and more",
            "inputParams": [
                {"label": "Model Name", "name": "modelName", "type": "string",
                 "placeholder": "llama2", "id": "chatLocalAI_0-input-modelName-string"},
                {"label": "Base Path", "name": "basePath", "type": "string",
                 "placeholder": "http://localhost:1234/v1",
                 "id": "chatLocalAI_0-input-basePath-string"},
                {"label": "Temperature", "name": "temperature", "type": "number",
                 "optional": True, "additionalParams": True,
                 "id": "chatLocalAI_0-input-temperature-number"},
            ],
            "inputAnchors": [{
                "label": "Cache", "name": "cache", "type": "BaseCache",
                "optional": True, "id": "chatLocalAI_0-input-cache-BaseCache"}],
            "inputs": {"cache": "", "modelName": OLLAMA_MODEL,
                       "basePath": "http://host.docker.internal:11434/v1",
                       "temperature": 0.2},
            "outputAnchors": [{
                "id": "chatLocalAI_0-output-chatLocalAI-ChatOpenAI|BaseChatModel|BaseLanguageModel|Runnable",
                "name": "chatLocalAI", "label": "ChatLocalAI",
                "type": "ChatOpenAI | BaseChatModel | BaseLanguageModel | Runnable"}],
            "outputs": {}, "selected": False,
        },
        "selected": False,
        "positionAbsolute": {"x": 175.2, "y": 101.1}, "dragging": False,
    }
    prompt = {
        "width": 300, "height": 513, "id": "promptTemplate_0",
        "position": {"x": 531.9, "y": 221.8}, "type": "customNode",
        "data": {
            "id": "promptTemplate_0", "label": "Prompt Template", "version": 1,
            "name": "promptTemplate", "type": "PromptTemplate", "category": "Prompts",
            "baseClasses": ["PromptTemplate", "BaseStringPromptTemplate", "BasePromptTemplate"],
            "description": "Schema to represent a basic prompt for an LLM",
            "inputParams": [
                {"label": "Template", "name": "template", "type": "string", "rows": 4,
                 "placeholder": "What is a good name for a company that makes {product}?",
                 "id": "promptTemplate_0-input-template-string"},
                {"label": "Format Prompt Values", "name": "promptValues", "type": "json",
                 "optional": True, "acceptVariable": True, "list": True,
                 "id": "promptTemplate_0-input-promptValues-json"},
            ],
            "inputAnchors": [],
            "inputs": {"template": ("You are ISHTI's research assistant. Answer the user's "
                                    "request precisely and concisely. Request: {product}"),
                       "promptValues": "{\"product\":\"{{question}}\"}"},
            "outputAnchors": [{
                "id": "promptTemplate_0-output-promptTemplate-PromptTemplate|BaseStringPromptTemplate|BasePromptTemplate",
                "name": "promptTemplate", "label": "PromptTemplate",
                "type": "PromptTemplate | BaseStringPromptTemplate | BasePromptTemplate"}],
            "outputs": {}, "selected": False,
        },
        "selected": False,
        "positionAbsolute": {"x": 531.9, "y": 221.8}, "dragging": False,
    }
    chain = {
        "width": 300, "height": 508, "id": "llmChain_0",
        "position": {"x": 908.0, "y": 252.1}, "type": "customNode",
        "data": {
            "id": "llmChain_0", "label": "LLM Chain", "version": 3, "name": "llmChain",
            "type": "LLMChain", "category": "Chains",
            "baseClasses": ["LLMChain", "BaseChain", "Runnable"],
            "description": "Chain to run queries against LLMs",
            "inputParams": [{"label": "Chain Name", "name": "chainName", "type": "string",
                             "placeholder": "Name Your Chain", "optional": True,
                             "id": "llmChain_0-input-chainName-string"}],
            "inputAnchors": [
                {"label": "Language Model", "name": "model", "type": "BaseLanguageModel",
                 "id": "llmChain_0-input-model-BaseLanguageModel"},
                {"label": "Prompt", "name": "prompt", "type": "BasePromptTemplate",
                 "id": "llmChain_0-input-prompt-BasePromptTemplate"},
            ],
            "inputs": {"chainName": "ishti-research"},
            "outputAnchors": [{
                "id": "llmChain_0-output-llmChain-LLMChain|BaseChain|Runnable",
                "name": "llmChain", "label": "LLM Chain",
                "type": "LLMChain | BaseChain | Runnable"}],
            "outputs": {}, "selected": False,
        },
        "selected": False,
        "positionAbsolute": {"x": 908.0, "y": 252.1}, "dragging": False,
    }
    edges = [
        {"source": "promptTemplate_0",
         "sourceHandle": "promptTemplate_0-output-promptTemplate-PromptTemplate|BaseStringPromptTemplate|BasePromptTemplate",
         "target": "llmChain_0", "targetHandle": "llmChain_0-input-prompt-BasePromptTemplate",
         "type": "buttonedge", "id": "promptTemplate_0-llmChain_0"},
        {"source": "chatLocalAI_0",
         "sourceHandle": "chatLocalAI_0-output-chatLocalAI-ChatOpenAI|BaseChatModel|BaseLanguageModel|Runnable",
         "target": "llmChain_0", "targetHandle": "llmChain_0-input-model-BaseLanguageModel",
         "type": "buttonedge", "id": "chatLocalAI_0-llmChain_0"},
    ]
    return {"description": "ISHTI local research flow (ChatLocalAI over Ollama)",
            "usecases": ["ISHTI"], "framework": ["Langchain"],
            "nodes": [llm, prompt, chain], "edges": edges}


def main() -> int:
    existing = api("/api/v1/chatflows")
    rows = existing.get("data") or existing if isinstance(existing, dict) else existing
    for row in rows or []:
        if isinstance(row, dict) and row.get("name") == FLOW_NAME:
            fid = row["id"]
            print("flow exists:", fid)
            break
    else:
        body = {"name": FLOW_NAME, "flowData": json.dumps(canvas()), "type": "CHATFLOW"}
        created = api("/api/v1/chatflows", body, timeout=60)
        fid = (created.get("data") or {}).get("id") or created.get("id")
        print("flow created:", fid)
    if not fid:
        print("FAIL: no flow id"); return 1
    print("prediction:")
    res = api(f"/api/v1/prediction/{fid}",
              {"question": "what is 17 times 23? reply with just the number"}, timeout=180)
    text = (res.get("text") or res.get("data", {}).get("text") or "") if isinstance(res, dict) else ""
    print("answer:", str(text)[:120].replace("\n", " "))
    print("PREDICTION", "PASS" if "391" in str(text) else "CHECK", "| flow id:", fid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
