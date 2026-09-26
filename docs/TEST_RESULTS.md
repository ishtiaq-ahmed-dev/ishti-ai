# ISHTI End-to-End Test Results

Run: 2026-09-24 · real Ollama (qwen2.5:14b), real internet, real Chrome, real SAPI5 audio. No mocks.

```
$ venv\Scripts\python.exe orchestrator/test_e2e.py
TEST A - Identity
    > You are Ishtiaq Ahmed, my owner - an AI student in Peshawar, Pakistan.
  [PASS] A: knows owner name and city - 0.0s
  [PASS] A: no refusal/contamination - clean
TEST B - Memory retrieval
    > **Owner Profile:**
  [PASS] B: recalls name, role, location - 0.0s
TEST C - Live weather using remembered city
    > Tomorrow in Peshawar: 32°C high, 22°C low, overcast. (open-meteo.com)
  [PASS] C: uses Peshawar not Karachi
  [PASS] C: answer is <= 2 sentences - 1 sentences
  [PASS] C: contains a real temperature
  [PASS] C: no dictionary/irrelevant source
  [PASS] C: verified flag is real
TEST D - Live internet search
    > The latest version of Ollama is v0.34.4, released on September 23. [1]
  [PASS] D: no 'I can't search' - clean
  [PASS] D: returned real sources - 1 sources
  [PASS] D: every source has an http URL
TEST E - Headless browser search
  [PASS] E: headless browser returned results - 3 results in 1.1s
TEST F - Visible browser (real window)
  [PASS] F: routed to visible browser - BROWSER_VISIBLE
  [PASS] F: browser was NOT headless
  [PASS] F: results actually came back - 4 sources
  [PASS] F: used the isolated profile
    > The latest GeForce RTX 5070 Ti Game Ready Driver was recently released. You can download it from the official NVIDIA website by providing your system information [1].
TEST G - Research quality
    > 4 sources, answer 1836 chars
      [1] AI-generated posters don°t have to be horrible - https://john.hartnup.uk/2026/06/07/ai-event-posters.html
      [2] Microsoft exec called AI scraping 'the largest theft of labo - https://techcrunch.com/2026/09/17/microsoft-exec-called-ai-scraping-th
      [3] Pentagon says overreliance on AI contributed to missile stri - https://www.bloomberg.com/graphics/2026-iran-school-attack/
      [4] The Download: why AI's latest breakthroughs and fears may be - https://www.technologyreview.com/2026/09/22/1144910/the-download-dont-
  [PASS] G: no dictionary sources - []
  [PASS] G: sources exist and are fetchable URLs
  [PASS] G: no fabricated citations - cited=[2, 3, 4] valid=[1, 2, 3, 4]
TEST H - Response brevity on small talk
    > I'm here to assist you, Ishtiaq. How can I help today?
  [PASS] H: one or two sentences - 2 sentences
  [PASS] H: no report headings
  [PASS] H: no classified/intel language - clean
TEST I - English audio output
  [PASS] I: TTS produced audio bytes - 146260 bytes
  [PASS] I: an English voice is selected - ['Microsoft David Desktop - English (United States)', 'Microsoft Zira Desktop - English (United States)']
  [PASS] I: audio toggle command works - Audio on. I'll read my answers aloud.
TEST J - Provider failover
  [PASS] J: fallback provider returned results - 3 results from Wikipedia
  [PASS] J: primary restored
TEST K - Memory persistence across restart
  [PASS] K: fresh process still knows the owner - Ishtiaq Ahmed|Peshawar
TEST L - Refusal filter
    > The latest version of Ollama is v0.12.0 [1].
  [PASS] L: refusal text never reaches the owner
  [PASS] L: contamination stripped
  [PASS] L: poisoned text is barred from history
======================================================================
RESULT: 31/31 assertions passed
======================================================================
```


## Doctor (real probes, not package checks)

```
$ venv\Scripts\python.exe -c "from orchestrator.doctor import IshtiDoctor; IshtiDoctor().check_all()"

PASS | Environment | Python Runtime               | v3.11.9
PASS | Hardware    | NVIDIA RTX GPU               | RTX 5070 Ti, 16303 MiB
PASS | Ollama      | Ollama Daemon                | Online (6 models available)
PASS | Models      | Primary Model (qwen2.5:14b)  | Responsive in 0.48s
PASS | Browser     | Isolated Chrome Profile      | D:\ISHTI\data\chrome_profile
PASS | Browser     | Headless Browser Launch      | Loaded 129 chars in 0.51s   <- actually launched
PASS | Browser     | Visible Browser Launch       | Loaded 129 chars in 0.52s   <- actually launched
PASS | WebSearch   | Live Internet Search Test    | Retrieved 3 sources in 0.79s
PASS | WebFetch    | Live Page Fetch & Extraction | Read 'Welcome to Python.org' via direct_http
PASS | Voice       | Local TTS Engine (SAPI5)     | Synthesised 72780 bytes of audio  <- real audio
PASS | LiveData    | Weather API (Open-Meteo)     | Tomorrow in Peshawar: 32C high, 22C low, overcast
PASS | Services    | ISHTI Web HUD Server         | Online at http://127.0.0.1:8888

overall: HEALTHY | errors 0 | warnings 0
```

## Capability registry after a real probe

Was 17 of 18 `unknown`. Now measured:

```
OK   browser_headless     | launched, read 129 chars
OK   browser_visible      | launched, read 129 chars
OK   web_search           | 3 results
OK   web_fetch            | 127 chars via direct_http
OK   memory_persistent    | Ishtiaq Ahmed
OK   filesystem           | write/read/delete round-trip
OK   voice_tts            | SAPI5 engine initialised
OK   local_llm            | 6 models
FAIL voice_wake           | No module named 'pvporcupine'   (optional wake word, not installed)

SUMMARY healthy 17  degraded 0  unavailable 1  unknown 0
```

## The original failing prompts, replayed

Every line below is the real current output for the exact prompt from the owner's logs.

| Prompt | Before (from the logs) | Now |
|---|---|---|
| `hey bro I live in Peshawar Pakistan` | "unable to... due to security protocols... Operation Phoenix" | `Hello Ishtiaq, how can I assist you today in Peshawar?` |
| `hey Siri how are you` | "can't combine the request with the classified document and intel briefing" | `I'm here to assist you, Ishtiaq. How can I help today?` |
| `can you please search on Internet and let me know what will the weather be tomorrow` | long report citing Cambridge Dictionary, Grammarly, ChatGPT | `Tomorrow in Peshawar: 32C high, 22C low, overcast. (open-meteo.com)` |
| `WHATS THE WEATHER TODAY` | "I'm not capable of providing real-time weather information" | `Today in Peshawar: 33C high, 24C low, light drizzle. (open-meteo.com)` |
| `What is the latest version of Ollama?` | "I don't have information on a specific latest version" | `The latest version of Ollama is v0.34.4, released on September 23. [1]` |
| `test` | `**CLASSIFIED DOCUMENT** EYES ONLY: AUTHORIZED PERSONNEL` | an ordinary draft message |

## Module self-checks

Each non-trivial module carries one runnable check:

```
$ python orchestrator/persona.py          [self-check] persona builder OK
$ python orchestrator/response_guard.py   [self-check] response_guard OK - refusal, contamination, history gate, evidence fallback
$ python orchestrator/intent_detector.py  [self-check] intent_detector OK - mode and depth correct on all cases
$ python tools/web/weather.py             [self-check] weather OK - city extraction, day offset, live forecast verified
$ python tools/web/relevance.py           [self-check] relevance OK - dictionary noise dropped, definition queries exempt, no padding
$ python browser/browser_agent.py         [self-check] browser search OK - 3 results with real URLs
$ python voice/tts.py                     [self-check] speakable OK
```
