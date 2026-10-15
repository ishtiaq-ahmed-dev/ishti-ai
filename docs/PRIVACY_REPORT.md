# Privacy and egress report

What ISHTI does with your data, checked against the code rather than asserted about it. Every line below belongs to the revision named here and to no other: re-run `venv\Scripts\python tools\privacy_report.py --markdown docs\PRIVACY_REPORT.md` after any change and this page is regenerated - a privacy report that describes a build you are not running is worth nothing.

| | |
|---|---|
| Version | 0.1.0 |
| Revision | `e4fc379` (dirty) |
| Source hash | `675ca6091bba878cddae44517848df235bea9bb30583fc5ca0db1ad0ea9db0dc` |
| Generated | 2026-10-16T02:18:36 |
| Python | 3.11.9 on Windows-10-10.0.26300-SP0 |
| Result | 4 of 4 checks passed |

## The checks

| Check | Result | What was established |
|---|---|---|
| `egress_guard_blocks` | PASS | blocked a connect to a public IP and logged one row of host/port/who only |
| `telemetry_deny` | PASS | all 26 telemetry hosts refused while scope.net "*" was active, and example.com allowed in the same context |
| `loopback_bind` | PASS | 13 bind sites, none using a non-loopback literal; orchestrator/plan_view.py refuses a non-loopback host before a socket opens |
| `no_desktop_capture` | PASS | none of 14 desktop-capture APIs appear in the product source; screenshot requests are refused remotely |

## What each check actually did

### `egress_guard_blocks` - PASS

- A fresh process installed the guard (`ishti/privacy.py:install()`) and called `socket.create_connection(("93.184.216.34", 80))`: **blocked**.
- The refusal reads: _ISHTI blocked an outbound connection to 93.184.216.34:80 (no skill or owner action is running); nothing was sent. See Privacy in the dashboard or data/outbound.log._
- Connecting to a telemetry host by name returned **blocked**.
- One ledger row was written, with fields `['allowed', 'host', 'port', 'reason', 'ts', 'who']` - the host, the port, who asked and why. There is no field for a payload, a URL path or a response, which is the point.
- The row: `{"ts": "2026-10-16T02:18:34", "host": "i.posthog.com", "port": 443, "who": "library:?", "allowed": false, "reason": "known telemetry/analytics endpoint"}`

### `telemetry_deny` - PASS

- Context: the skill `browser`, whose manifest declares `scope.net: ["*"]`, plus an owner grant - the most permission ISHTI can hand out.
- Control: `example.com` in that same context was **allowed**. Without this the check would pass on a build that blocked everything.
- All 26 hosts on `TELEMETRY_DENY` were refused: ['known telemetry/analytics endpoint'].
- With the Internet switch OFF, an otherwise allowed host was refused.

### `loopback_bind` - PASS

- 13 bind sites in ISHTI's own source; non-loopback literals: none. Sites: `ishti/lifecycle.py:1089`, `ishti/lifecycle.py:1050`, `ishti/oauth.py:438`, `ishti/oauth.py:441`, `ishti/skills/workflows/skill.py:105`, `desktop/mock_server.py:251`, `orchestrator/plan_view.py:235`, `tests/test_audit_fixes.py:160`, `tests/test_download_cancel.py:44`, `tests/test_keepalive.py:71`, `tests/test_launcher_window.py:123`, `tests/test_launcher_window.py:129`, `tests/backend/test_remote_tunnel.py:44`.
- `orchestrator/plan_view.py:serve()` refused ['', '0.0.0.0', '192.168.1.10', '::', 'example.com'] at runtime, and its default bind was `http://127.0.0.1:49546/`.
- 1 site(s) take the host as a parameter rather than a literal (`orchestrator/plan_view.py:235`). The runtime half above exercises `orchestrator/plan_view.py:serve()`; a variable host is listed rather than passed, because a scan of literals cannot see what it is set to at call time.

### `no_desktop_capture` - PASS

- Searched 14 capture APIs (ImageGrab, pyautogui.screenshot, pyscreeze, import mss, mss.mss, dxcam, BitBlt, GetDC, GetWindowDC, PrintWindow, desktop_capture, grab_screen, capture_screen, screenshot_scraping) across `ishti/`, `desktop/`, `orchestrator/` and `interface/`: **no hits**.
- Screenshot requests are a refused remote-action category (`ishti/channels/gate.py`): True.
- Outside the product, in developer tooling, 0 hit(s): none. Those are test and QA scripts that are not on a customer's machine.

## Every host the code can reach

### Web skills - allowed by design, only while the Internet switch is on

These 12 skills declare `scope.net: ["*"]`: they may call whatever host the task needs, because fetching a page the owner asked for is their job. The guard allows them only while the Internet switch is on, and it refuses the telemetry list above even then.

`browser`, `deep_research`, `dev`, `digest`, `find_leads`, `ledger`, `markets`, `shopping`, `utilities`, `web_fetch`, `web_search`, `webtools`

### Skills with a named host list

| Skill | Hosts |
|---|---|
| `braintrain` | `127.0.0.1`, `huggingface.co` |
| `connectors` | `google.com`, `googleapis.com`, `github.com`, `facebook.com`, `linkedin.com`, `microsoftonline.com`, `graph.microsoft.com`, `127.0.0.1` |
| `email` | `googleapis.com` |
| `gitops` | `localhost` |
| `internet_reach` | `r.jina.ai`, `youtube.com`, `youtu.be`, `github.com`, `api.github.com`, `v2ex.com`, `xueqiu.com`, `feeds.bbci.co.uk` |
| `media` | `github.com`, `githubusercontent.com`, `hf.co`, `huggingface.co` |
| `planners` | `youtube.com`, `youtu.be`, `googlevideo.com` |
| `recipe` | `localhost` |
| `security` | `api.pwnedpasswords.com` |
| `skillsmith` | `github.com`, `api.github.com`, `raw.githubusercontent.com`, `codeload.github.com` |
| `speedtest` | `speed.cloudflare.com` |
| `stats` | `localhost` |
| `weather` | `open-meteo.com` |

### Model providers (only when you choose a cloud model)

| Provider | Endpoint |
|---|---|
| anthropic | `https://api.anthropic.com/v1/` |
| gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| openai | `https://api.openai.com/v1` |
| openrouter | `https://openrouter.ai/api/v1` |

With `provider: local` - the default - the model runs through Ollama on this machine and none of these endpoints is used. A cloud model is reachable only after the owner selects one and turns the Internet switch on; `ishti/privacy.py:inference()` then narrows the grant to that one endpoint and nothing else.

### Refused everywhere (26 hosts)

Telemetry, analytics and crash-reporting endpoints. These are refused even inside a skill whose scope is `*`, even with an owner grant, and even with the Internet switch on - which is what the `telemetry_deny` check above demonstrates rather than assumes.

`i.posthog.com`, `app.posthog.com`, `ingest.sentry.io`, `api.segment.io`, `cdn.segment.com`, `api.mixpanel.com`, `api-js.mixpanel.com`, `api.amplitude.com`, `api2.amplitude.com`, `browser-intake-datadoghq.com`, `http-intake.logs.datadoghq.com`, `scarf.sh`, `telemetry.crewai.com`, `sgai-oss-tracing.onrender.com`, `api.smith.langchain.com`, `eu.api.smith.langchain.com`, `google-analytics.com`, `analytics.google.com`, `googletagmanager.com`, `stats.g.doubleclick.net`, `app-measurement.com`, `bugsnag.com`, `clarity.ms`, `vortex.data.microsoft.com`, `events.data.microsoft.com`, `telemetry.ollama.com`

### Third parties, and what stops each (12)

| Who | Would go to | What it would carry | Status |
|---|---|---|---|
| Ollama tray app update check | ollama.com/api/update | OS, CPU arch, Ollama version, timestamp, random nonce | stopped at every start: ISHTI closes Ollama's tray app and removes its Startup shortcut (silence_ollama_tray); ISHTI runs its own Ollama |
| Ollama cloud models / web search | ollama.com | prompts sent to cloud models, only if you pick one | disabled: OLLAMA_NO_CLOUD=1 and ~/.ollama/server.json disable_ollama_cloud |
| Ollama model downloads | registry.ollama.ai | model name | owner-initiated (Settings > Models) |
| WebView2 (dashboard window) | Microsoft endpoints (sync, updates, telemetry) | browser diagnostics | blocked: loopback-only resolver rule plus --disable-* flags |
| scrapegraphai (find_leads) | sgai-oss-tracing.onrender.com | the extraction prompt, page text and the model's answer (when enabled) | blocked by name at the socket guard |
| chromadb / posthog | posthog | anonymous usage events | off: anonymized_telemetry=False and ANONYMIZED_TELEMETRY=False |
| huggingface_hub | huggingface.co | model name, the saved HF token if any, telemetry ping | offline when the cache exists; telemetry and implicit token off; downloads only via Settings |
| crewai / langsmith / opentelemetry (installed, not imported by ISHTI) | telemetry.crewai.com, api.smith.langchain.com | n/a unless imported | env switches set; hosts on the deny list |
| npx @playwright/mcp (browser skill) | registry.npmjs.org | package name | npm_config_prefer_offline set; the MCP child may not inherit it |
| Telegram remote control | api.telegram.org | your messages and replies, through Telegram's servers | opt-in: allowed only while telegram_enabled is on |
| Web skills (browser, web_search, web_fetch, markets, ...) | whatever you ask them to open | the query or URL you gave | allowed by design, per manifest scope.net; blocked when the Internet switch is OFF |
| SearXNG (Docker) | startpage, mojeek, yahoo, bing, qwant, wikipedia | your search terms | owner-initiated through web_search; the container is outside Python's reach |

A live check of the machine this report was generated on found: **Ollama tray app is running** - ACTIVE - quit it (tray icon > Quit) and remove Startup\Ollama.lnk to stop The rows above are the static inventory; this line is an observation, and it changes between runs.

### Environment hardening (25 variables set at import)

Set by `ishti/privacy.py:harden()`, before any library that reads them is imported - this is what turns off the telemetry of the libraries ISHTI depends on.

| Variable | Value |
|---|---|
| `ANONYMIZED_TELEMETRY` | `False` |
| `CREWAI_DISABLE_TELEMETRY` | `true` |
| `CREWAI_DISABLE_TRACKING` | `true` |
| `DOTNET_CLI_TELEMETRY_OPTOUT` | `1` |
| `DO_NOT_TRACK` | `1` |
| `GRADIO_ANALYTICS_ENABLED` | `False` |
| `HF_HUB_DISABLE_IMPLICIT_TOKEN` | `1` |
| `HF_HUB_DISABLE_TELEMETRY` | `1` |
| `LANGCHAIN_TRACING` | `false` |
| `LANGCHAIN_TRACING_V2` | `false` |
| `LANGSMITH_TRACING` | `false` |
| `NEXT_TELEMETRY_DISABLED` | `1` |
| `NO_ALBUMENTATIONS_UPDATE` | `1` |
| `OLLAMA_NO_CLOUD` | `1` |
| `OTEL_SDK_DISABLED` | `true` |
| `PIP_DISABLE_PIP_VERSION_CHECK` | `1` |
| `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD` | `1` |
| `SCARF_NO_ANALYTICS` | `true` |
| `SCRAPEGRAPHAI_TELEMETRY_ENABLED` | `false` |
| `WANDB_DISABLED` | `true` |
| `WANDB_MODE` | `disabled` |
| `npm_config_audit` | `false` |
| `npm_config_fund` | `false` |
| `npm_config_prefer_offline` | `true` |
| `npm_config_update_notifier` | `false` |

### The dashboard window

ISHTI's own UI runs in a WebView2 with these flags (`ishti/privacy.py:WEBVIEW2_ARGS`):

```text
--disable-features=ElasticOverscroll,OptimizationHints,Translate,AutofillServerCommunication,CertificateTransparencyComponentUpdater,MediaRouter,msSmartScreenProtection --disable-background-networking --disable-sync --disable-component-update --no-pings --disable-domain-reliability --disable-breakpad --disable-client-side-phishing-detection --disable-default-apps --no-first-run --safebrowsing-disable-auto-update --host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE 127.0.0.1 , EXCLUDE localhost"
```

and with a resolver rule that maps every host except `127.0.0.1` and `localhost` to `~NOTFOUND`. The browser engine that renders the interface therefore cannot resolve a name on the network at all - it is a rendering surface, not a browser.

## Live gate - not run

This report did not start the backend, so nothing here shows the running server's own sockets. `--live` runs `tools/gate_no_cloud.py`, which does exactly that: it samples every connection of the backend's process tree during boot and under load, and fails on a single external one.

## What this report does not prove

A privacy report that overstates is worse than none, so here is the boundary of the claims above.

- the operating system, the Windows firewall, and traffic from any process that is not ISHTI.
- compiled code inside third-party wheels that ISHTI imports but does not read.
- a live backend and its sockets, unless --live ran (tools/gate_no_cloud.py).
- what an operator with administrator rights does to the machine after install.
- the skills whose job is the web: they reach the internet by design, and their declared hosts are listed above.

It is also a report about ISHTI's own source. It establishes what the code in this revision does and refuses; it is not an audit by a third party, and nobody outside this project has reproduced it. What it gives you is a starting point an auditor can check, and a revision-stamped answer that stays true only as long as the source hash above does.
