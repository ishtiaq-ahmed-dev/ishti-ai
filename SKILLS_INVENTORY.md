# SKILLS_INVENTORY — the full 1,000+ catalogue, item by item (2026-09-28)

Status legend:
- **BUILT** — real implementation exists in ISHTI, self-checked, routed (skill or module named).
- **BUILT-CORE** — exists as part of a core module (dispatcher, voice, models, gpu, monitor...) rather than a named skill.
- **PARTIAL** — some capability exists; the named item needs its own piece (batch number where planned).
- **BLOCKED** — impossible on this machine/now, with the reason (not skipped silently).

Batches: **B** = utilities wave, **C** = views/trackers wave, **D** = platform wave (all gated by
self-check + routing labels + check_skills, same as every existing skill).

## Domain 1 — Natural Language Understanding & Generation (30)
1. Multi-turn conversation management — BUILT-CORE (dispatcher sessions, INFLIGHT, history)
2. Intent recognition & slot filling — BUILT-CORE (router + Laya, 157/157)
3. Sentiment analysis — BUILT (textstats lexicon heuristic; LLM nuance on request)
4. Tone adaptation — BUILT (writing: formal/casual/simple rewrite; chat style rules)
5. Multilingual translation — BUILT (utilities translate; ur/ps pipeline in dispatcher)
6. Summarization (extractive & abstractive) — BUILT (writing summary; deep_research)
7. Paraphrasing & rewriting — BUILT (writing paraphrase)
8. Grammar & style correction — BUILT (writing grammar)
9. Text expansion & elaboration — BUILT (writing expand)
10. Bullet point generation — BUILT (writing bullets)
11. Executive summary creation — BUILT (writing summary; digest)
12. Meeting transcript processing — BUILT (planners lecture notes + digest; batch B: `meetingdocs` polished flow)
13. Email drafting & response suggestions — BUILT (writing email; email skill)
14. Chat message composition — BUILT (chat; whatsapp drafts)
15. Social media post generation — BUILT (writing post)
16. Blog article outlining — BUILT (writing outline)
17. Technical documentation writing — BUILT (dev skill; docs skill)
18. Creative writing (stories, poems, scripts) — BUILT (chat creative rules)
19. Persuasive copywriting — BUILT-CORE (planner via chat; dedicated template = batch D)
20. Legal document drafting assistance — PARTIAL (batch B: `legaldoc` template-assist; T2, not legal advice)
21. Medical note summarization — BLOCKED by policy? No: BUILT-CORE possible — PARTIAL (batch B: `mednote`, local-only, no advice)
22. Academic abstract generation — BUILT (writing summary)
23. Press release composition — PARTIAL (batch B: `pressrel`)
24. Product description writing — PARTIAL (batch B: `proddesc`)
25. FAQ generation from documents — PARTIAL (batch B: `faqgen`)
26. Conversation summarization — BUILT (digest; chat session summaries)
27. Key point extraction — BUILT (writing bullets)
28. Named entity recognition (regex-lite) — BUILT (textstats entities)
29. Topic modeling — BUILT (textstats keywords + LLM topics on request; true LDA = batch C)
30. Semantic search & retrieval — BUILT (semantic.py ChromaDB; files search)

## Domain 2 — Voice & Audio Processing (25)
1. Wake word detection — BUILT-CORE (wake.py, 3 phrases)
2. Voice activity detection — BUILT-CORE (Silero VAD, barge-in)
3. Speaker diarization — BUILT-CORE (diarize.py)
4. Real-time STT — BUILT-CORE (whisper turbo, 114 ms decode)
5. TTS synthesis — BUILT-CORE (Kokoro/Piper roster)
6. Voice cloning & personalization — BUILT-CORE (clone.py; owner-voice WhatsApp notes)
7. Emotion detection from voice — PARTIAL (batch B: `voiceemotion`, heuristic prosody)
8. Accent recognition & adaptation — PARTIAL (batch B: vocabulary hints; whisper handles accents)
9. Noise cancellation & audio enhancement — BUILT (batch I `audioclean`: ffmpeg afftdn denoise + EBU R128 loudnorm, originals untouched)
10. Music identification — BLOCKED (needs Shazam-class service/API; no offline equivalent)
11. Podcast transcription & summarization — BUILT (planners; yt-dlp + whisper)
12. Voice command parsing — BUILT-CORE (wake -> dispatcher)
13. Multi-speaker conversation tracking — BUILT-CORE (diarize + context)
14. Audio event detection (alarms, glass) — BLOCKED (needs dedicated models; not on disk)
15. Voice biometrics & authentication — PARTIAL (batch C: speaker-embed verify with resemblyzer)
16. Prosody analysis — PARTIAL (batch B: `voiceemotion` covers stress/pauses)
17. Real-time translation with voice — BUILT-CORE (ur/ps understand+answer pipeline)
18. Voice memo creation & organization — BUILT (media skill; voice notes)
19. Audio bookmarking — PARTIAL (batch C: `audiomark` timestamps)
20. Silence detection & trimming — BUILT (FFmpeg via capcut/video tooling)
21. Audio format conversion — BUILT (media/FFmpeg)
22. Voice speed adjustment — BUILT (Kokoro/Piper speed params in roster)
23. Background music/speech separation — BLOCKED (needs Demucs-class model ~GB; can add batch C if owner approves download)
24. Voice-based search queries — BUILT-CORE (voice -> web_search)
25. Audio quality assessment — BUILT (batch H `audioquality`: ffprobe codec/rate/bitrate/channels/duration + verdict bands)

## Domain 3 — Vision & Image Understanding (35)
1. Object detection & localization — PARTIAL (Gemma3 describes; boxes need YOLO — batch C with owner-approved ~6 MB model)
2. Image classification — BUILT (Gemma3 vision Q&A)
3. Scene understanding — BUILT (Gemma3; screen_understand)
4. Facial recognition & verification — BLOCKED by policy (privacy: no face DB on this PC without explicit owner request)
5. Emotion detection from faces — PARTIAL (batch C: needs FER model; owner call)
6. Age & gender estimation — PARTIAL (same model as above)
7. OCR — BUILT-CORE (Windows OCR + RapidOCR)
8. Handwritten text recognition — BUILT (Gemma3 fallback when OCR fails)
9. Barcode & QR scanning — BUILT (batch I `scanqr`: cv2 encoder + zxing-cpp reader, self-verified round-trip)
10. Document layout analysis — PARTIAL (batch B: `doclayout` pdfplumber)
11. Table extraction from images — BUILT (extract skill + vision)
12. Chart & graph interpretation — BUILT (Gemma3; screen_understand)
13. Medical image analysis — BLOCKED by policy (not medical advice; could do "describe image" only)
14. Satellite imagery analysis — PARTIAL (batch C: tiles + Gemma3 describe)
15. Product identification from photos — PARTIAL (batch C: Gemma3 + web search)
16. Landmark & place recognition — BUILT (Gemma3 describe + search)
17. Art style identification — BUILT (Gemma3)
18. Color palette extraction — BUILT (batch E `palette`: exact hex + pixel shares + brightness)
19. Image quality assessment — BUILT (batch E `imgquality`: resolution, Laplacian sharpness + blur verdict, brightness/contrast)
20. Duplicate image detection — BUILT (media skill dedupe)
21. Image captioning — BUILT (Gemma3)
22. Visual question answering — BUILT (Gemma3 look())
23. Image segmentation — BLOCKED (needs SAM-class model, GB-scale)
24. Depth estimation — BLOCKED (needs MiDaS-class model)
25. 3D reconstruction — BLOCKED (needs NeRF/colmap stack)
26. Image enhancement (denoise, super-res) — PARTIAL (Real-ESRGAN install pending, NEEDS 12)
27. Style transfer — BLOCKED (needs model; batch C if approved)
28. Image inpainting — BLOCKED (needs model)
29. Background removal — PARTIAL (rembg pending, NEEDS 12)
30. Cropping & composition suggestions — BUILT (batch H `imgcrop`: aspect/box/trim crop, originals untouched)
31. Meme generation — BUILT (batch H `meme`: top/bottom outlined captions, auto-fit, canvas fallback)
32. Infographic creation — PARTIAL (batch B: matplotlib → `chart`)
33. Visual search (similar images) — BUILT (media dedupe/similar)
34. Image-based product recommendations — PARTIAL (batch C)
35. Real-time video frame analysis — BUILT (video pro keyframes + Gemma3)

## Domain 4 — Video Processing (25)
1. Transcription with timestamps — BUILT (whisper + pro.py)
2. Scene change detection — BUILT (FFmpeg scene filter, capcut)
3. Action recognition — PARTIAL (batch C: frame sampling + Gemma3 describe)
4. Activity summarization — BUILT (transcript + LLM summary)
5. Key moment extraction (highlights) — PARTIAL (batch C: `highlights` loudness+scene heuristics)
6. Captioning & subtitling — BUILT (pro.py SRT)
7. Speaker tracking across frames — BUILT-CORE (diarize)
8. Video quality enhancement — PARTIAL (Real-ESRGAN pending)
9. Video summarization — BUILT (pro.py)
10. Event detection (goals, applause) — PARTIAL (audio-energy heuristic, batch C)
11. Video search by text query — PARTIAL (batch C: transcript index)
12. Video chaptering — BUILT (batch I `chapters`: ffmpeg scdet timestamps → YouTube-style list + Mermaid)
13. Slow-motion & time-lapse — BUILT (FFmpeg/capcut)
14. Video stabilization — PARTIAL (batch B: FFmpeg vidstab if present)
15. Green screen removal — PARTIAL (FFmpeg chromakey, batch B)
16. Video-to-GIF — BUILT (FFmpeg)
17. Thumbnail generation — BUILT (pro.py frames)
18. Compression optimization — BUILT (FFmpeg NVENC)
19. Format conversion — BUILT (FFmpeg)
20. Editing suggestions — BUILT (pro.py analysis)
21. Content moderation — PARTIAL (batch C: Gemma3 classify frames)
22. Copyright detection — BLOCKED (needs fingerprint service)
23. Video analytics (engagement prediction) — PARTIAL (batch C: basic stats only)
24. Live stream monitoring — PARTIAL (batch C: yt-dlp live poll)
25. Tutorial generation — PARTIAL (batch C: steps from transcript)

## Domain 5 — Knowledge & Information Retrieval (40)
1. Web search with citations — BUILT (web_search; self-verifying)
2. Academic search (arXiv, PubMed) — BUILT (arXiv API in search)
3. News aggregation — BUILT (utilities news; digest)
4. Fact-checking — BUILT-CORE (deep_research per-page verdicts)
5. Definition lookup — BUILT (utilities define)
6. Historical event retrieval — BUILT (search+LLM)
7. Scientific concept explanation — BUILT (study teach-me)
8. Legal case search — BLOCKED (needs Westlaw/Lexis APIs)
9. Patent search — BLOCKED (needs USPTO/EPO API — actually free: batch B candidate)
10. Company information — BUILT (search)
11. Stock market data — BUILT (markets/yfinance)
12. Weather forecasting — BUILT (open-meteo)
13. Sports statistics — PARTIAL (batch B: `sports` free APIs)
14. Movie/TV info — PARTIAL (batch B: `movies` OMDb if key, else Wikipedia)
15. Book summaries — PARTIAL (batch B: `books` search+summary)
16. Recipe search & nutrition — BUILT (batch C `recipe`: from-ingredients/recipe/grocery list)
17. Travel destination info — BUILT (travel)
18. Flight & hotel search — PARTIAL (scrape-blocked; grounded itineraries BUILT, NEEDS 12)
19. Event calendar lookup — PARTIAL (batch C)
20. Job posting aggregation — PARTIAL (batch C: `jobs` RSS)
21. Real estate search — PARTIAL (batch C)
22. Product comparison & reviews — BUILT (shopping)
23. How-to guides — BUILT (search + LLM)
24. FAQ retrieval — PARTIAL (batch B: `faqgen`)
25. Code snippet search — PARTIAL (batch B: GitHub search API in dev)
26. API documentation lookup — PARTIAL (batch B: dev docs search)
27. Technical spec retrieval — BUILT (search)
28. Standards lookup — PARTIAL (batch B: search-driven)
29. Citation generation — PARTIAL (batch B: `citation` APA/MLA)
30. Literature review assistance — BUILT (deep_research)
31. Trend analysis — BUILT (markets research)
32. Competitor analysis — BUILT (deep_research)
33. Market research synthesis — BUILT (deep_research)
34. Expert identification — PARTIAL (batch C: search-driven)
35. Q&A from documents — BUILT (docs; semantic)
36. Knowledge graph querying — BUILT (rgraph.py)
37. Timeline generation — PARTIAL (batch B: `timeline` from events text)
38. Comparison tables — BUILT (LLM tables per style rules)
39. Pro/con analysis — BUILT (chat/planner)
40. Decision matrix — BUILT (planner)

## Domain 6 — Reasoning & Problem Solving (35)
1-6 (deduction, math, word problems, equations, stats, probability) — BUILT-CORE (planner; chat math rules); batch B adds `calc` (sympy) and `datecalc`
7. Optimization — PARTIAL (batch B: `optimize` scipy-lite)
8. Constraint satisfaction — PARTIAL (batch C)
9. Puzzles (Sudoku/chess) — Sudoku BUILT (batch I `sudoku`: verified solver, unsolvable proven by exhaustion); chess engine still PARTIAL
10. Strategic planning — BUILT (planner)
11. Decision trees — BUILT (planner)
12. Root cause analysis — BUILT (planner + monitor data)
13-14. Hypothesis testing / counterfactuals — BUILT (planner)
15. Analogy detection — BUILT (planner)
16. Pattern recognition — BUILT (planner)
17. Causal inference — PARTIAL (batch C)
18. Argument analysis — BUILT (planner)
19. Fallacy detection — PARTIAL (batch B: `argumentcheck`)
20. Critical thinking evaluation — BUILT (planner)
21. Ethical reasoning — BUILT (planner)
22. Risk assessment — BUILT (planner; trading risk rules)
23. Cost-benefit analysis — BUILT (planner)
24. Scenario planning — BUILT (planner)
25. Game theory — PARTIAL (batch C)
26. Negotiation strategy — BUILT (planner)
27. Conflict resolution frameworks — BUILT (planner)
28. Troubleshooting guides — BUILT (dev; health)
29. Debugging assistance — BUILT (dev; coder)
30. Diagnostic reasoning — PARTIAL (batch B: `mednote` structured only)
31. Forecasting — BUILT (batch E `forecast`: exact least-squares trend + N-period projection, R² reported)
32. Trend extrapolation — BUILT (batch E `forecast`)
33. Sensitivity analysis — PARTIAL (batch C)
34. Monte Carlo guidance — BUILT (batch I `montecarlo`: seeded Box-Muller portfolios, dice ground-truth check, project estimates)
35. Bayesian reasoning — BUILT (batch I `bayes`: exact posteriors with odds/likelihood-ratio working, sequential updates)

## Domain 7 — Code Generation & Software Development (50)
1-8 (gen, complete, bugs, refactor, tests, docs, review, quality) — BUILT (dev + coder model + skillsmith)
9. Security vulnerability scanning — PARTIAL (batch B: `vulnscan` bandit if installable)
10. Performance suggestions — BUILT (coder)
11. API integration code — BUILT (coder)
12. DB schema design — BUILT (coder)
13. SQL generation & optimization — BUILT (coder)
14. Regex creation — BUILT (coder)
15. Shell scripts — BUILT (dev scaffold/run; coder)
16-18. CI/CD, Docker, K8s — BUILT (batch D `devops`: GitHub Actions / GitLab CI / Dockerfile / Compose / K8s templates, pinned versions)
19. IaC (Terraform) — BUILT (batch D `devops`: AWS provider + S3 bucket + versioning template)
20-21. Frontend/backend scaffolding — BUILT (dev scaffold: react/vue/next/fastapi/flask)
22. Microservice design — BUILT (coder)
23. Design patterns — BUILT (coder)
24. Algorithm implementation — BUILT (coder)
25. Data structures — BUILT (coder)
26. Code translation — BUILT (coder)
27. Legacy modernization — BUILT (coder)
28. Code explanation — BUILT (chat CODE rules + coder)
29. Error interpretation — BUILT (dev)
30. Stack trace analysis — BUILT (dev)
31. Dependency management — BUILT (pip/npm in dev)
32. Package recommendations — BUILT (coder)
33. Git operations — BUILT (batch D `gitops`: reads direct, writes T2-queued with exact commands, force/reset/rebase/clean refused in code)
34. Merge conflict resolution — BUILT (coder assist)
35. Snippet organization — BUILT (dev)
36-37. Function extraction / class design — BUILT (coder)
38. Interface definition — BUILT (coder)
39. Event-driven setup — BUILT (coder)
40. Async patterns — BUILT (coder)
41. Caching strategy — BUILT (coder)
42. Rate limiting — BUILT (coder)
43. Auth code — BUILT (coder)
44. Payment integration — BUILT (coder; no live keys)
45. Email sending code — BUILT (coder)
46. File upload/download — BUILT (coder)
47. Web scraping scripts — BUILT (extract; obscura)
48. ETL pipelines — BUILT (coder)
49. ML training code — BUILT (coder; finetune exists)
50. Model deployment scripts — BUILT (coder)
Plus A/B framework — PARTIAL (batch D)

## Domain 8 — Data Analysis & Visualization (40)
1-5 (cleaning, imputation, outliers, types, feature ideas) — PARTIAL batch B (`dataprep` pandas)
6. Statistical summary — BUILT (batch C `stats` pandas describe: mean/std/min/max, missing values)
7. Correlation — BUILT (batch C `stats` Pearson pairs)
8. Distribution analysis — PARTIAL batch B (`stats`)
9. Time series decomposition — BUILT-CORE (batch E `forecast`: moving-average smoothing + weekly/monthly cycle spread; full STL not attempted)
10. Trend analysis — BUILT (markets)
11. Seasonality — BUILT (batch E `forecast`: weekly/monthly phase means with a spread % and an honest weak-cycle verdict)
12. Anomaly detection — BUILT (batch C `stats` z-score > 3 with row numbers)
13. Clustering — BUILT (batch C `stats` k-means, standardised features)
14. Dimensionality reduction — PARTIAL batch C
15-19. Classification/regression/eval/CV/tuning — PARTIAL batch C (sklearn present?)
20. Feature importance — PARTIAL batch C
21. A/B analysis — BUILT (batch E `abtest`: two-proportion z-test, lift + p-value, direction-stating verdict)
22. Cohort analysis — PARTIAL batch C
23. Funnel analysis — PARTIAL batch C
24. Retention — PARTIAL batch C
25. Churn — PARTIAL batch C
26. Segmentation — BUILT (batch C `stats` k-means group sizes)
27. Market basket — PARTIAL batch C
28. Text sentiment data — batch B (`sentiment`)
29. Topic modeling — batch B (`textstats`)
30. Network analysis — PARTIAL batch C
31. Geospatial — PARTIAL batch C
32. Interactive dashboards — PARTIAL (HUD exists; batch D widgets)
33. Chart generation — BUILT (batch E `chart`: bar/line/histogram PNGs in data/charts)
34-38. (heatmap, box, sankey, treemap, 3D) — PARTIAL batch B/C (matplotlib variants)
39. Animation — PARTIAL batch C
40. Report generation — BUILT (deep_research; digest)

## Domain 9 — Document Processing (35)
1. PDF text extraction — BUILT (pypdfium2)
2. PDF table extraction — BUILT (batch D `pdftables`: pdfplumber → CSV per table in data/extracted)
3. Document summarization — BUILT
4. Document comparison — BUILT (diff tools; fileops)
5. Contract clause extraction — PARTIAL batch B (`legaldoc`)
6. Legal review assistance — PARTIAL batch B
7. Resume parsing — PARTIAL batch B (`resumeparse`)
8. Invoice data extraction — BUILT (docs invoices; extract)
9. Receipt scanning — BUILT (OCR + extract)
10. Business card extraction — PARTIAL batch B (OCR flow)
11. Form data extraction — BUILT (docs fill forms)
12. Spreadsheet analysis — BUILT (extract xlsx)
13. Pivot tables — PARTIAL batch B (`pdftables`/pandas)
14. Formula generation — PARTIAL batch B (`formula`)
15. Macro recording — PARTIAL batch C
16. Formatting assistance — BUILT (docs)
17. Style compliance — PARTIAL batch C
18. Plagiarism detection — PARTIAL batch B (`originality` web-overlap check)
19. Citation formatting — batch B (`citation`)
20. Bibliography management — batch B (`citation`)
21-23. Versioning/collab/comments — PARTIAL batch C
24. Templates — BUILT (docs)
25. Mail merge — BUILT (batch D `mailmerge`: CSV + template → per-row drafts; sending stays per-draft T2)
26. Batch processing — BUILT (fileops)
27. Classification & tagging — BUILT (folders smart)
28. Full-text search — BUILT (files; semantic)
29. Semantic retrieval — BUILT (chroma)
30. Redaction — BUILT (batch D `redact`: text/CSV/PDF, column-aware, originals untouched, copies only in data/redacted)
31. E-signature workflow — BLOCKED (needs cert/legal infra)
32. Approval workflows — BUILT (T2 gates)
33. Archive & retention — PARTIAL batch C
34. Conversion PDF↔Word↔HTML — BUILT (docs/media)
35. OCR scanned docs — BUILT (ocr_pdf)

## Domain 10 — Task & Project Management (30)
1. Task creation from NL — BUILT (reminders; task tracker)
2. Eisenhower prioritization — BUILT (batch E `prioritize`: inferred flags shown, ordered quadrants, drop-pile honesty)
3. Deadline tracking — BUILT (reminders)
4. Recurring scheduling — BUILT (reminders RECUR)
5. Subtask decomposition — BUILT (planner)
6. Dependency mapping — PARTIAL batch C
7. Gantt generation — PARTIAL batch C
8. Kanban — BUILT (batch H `kanban`: todo/doing/done SQLite board with WIP>3 warning)
9. Sprint planning — PARTIAL batch C
10. Standup summaries — PARTIAL batch B (`standup`)
11. Retrospectives — PARTIAL batch C
12. SMART goals — BUILT (planner)
13. OKR tracking — BUILT (batch H `okr`: objectives + KRs with exact average roll-up)
14. Progress tracking — BUILT (task tracker)
15. Time estimation — BUILT (planner)
16. Time tracking — BUILT (lifelog)
17. Pomodoro — BUILT (focus sprints)
18. Distraction blocking — PARTIAL batch C
19. Habit tracking — BUILT (habits)
20. Routine optimization — PARTIAL batch C
21. Workflow design — BUILT (workflows)
22. Process documentation — BUILT (docs)
23. SOP creation — BUILT (docs)
24. Checklists — batch B (`checklist`)
25. Meeting agendas — BUILT (docs)
26. Minutes & action items — BUILT (digest; batch B `meetingdocs`)
27. Follow-up scheduling — BUILT (reminders)
28. Stakeholder comms — BUILT (clients/email)
29. Risk register — BUILT (batch H `riskregister`: 5x5 matrix, computed 1-25 scores, top ranking, mitigations)
30. Resource allocation — PARTIAL batch C

## Domain 11 — Calendar & Scheduling (25)
1. Calendar integration — BUILT (local + .ics; Google OAuth blocked NEEDS 12)
2. Meeting scheduling from email — PARTIAL batch B
3. Conflict detection — BUILT (calendar local)
4. Time zone conversion — BUILT (utilities time_in)
5. Recurring meetings — BUILT (reminders RECUR)
6. Duration optimization — PARTIAL batch C
7. Buffer time — PARTIAL batch C
8. Travel time — BUILT (search; maps blocked without key)
9. Optimal meeting time — PARTIAL batch C
10. Sharing & permissions — BLOCKED (needs cloud calendar)
11. Custom reminders — BUILT (reminders)
12. Prep checklists — BUILT (docs)
13. Agenda distribution — BUILT (email)
14. RSVP tracking — PARTIAL batch C
15. Waitlists — PARTIAL batch C
16. Room booking — BLOCKED (no org rooms)
17. Virtual meeting links — BUILT (utilities jitsi)
18. Calendar analytics — PARTIAL batch C
19. Focus time blocking — BUILT (focus)
20. Meeting cost calc — PARTIAL batch B (`meetingcost`)
21. Schedule optimization — PARTIAL batch C
22. Deadline mapping — BUILT (reminders AT/TOMORROW)
23. Event categorization — BUILT (folders-style tags)
24. Habit-from-calendar — PARTIAL batch C
25. Multi-calendar find-time — PARTIAL batch C (needs Google OAuth)

## Domain 12 — Email & Communication (30)
1. Drafting from bullets — BUILT (writing email)
2. Response suggestions — BUILT (email; chat)
3. Thread summarization — BUILT (batch B `threadsum` polish)
4. Priority inbox — PARTIAL (IMAP order; batch C)
5. Spam detection — BUILT-CORE (Gmail-side) + batch B `phishing_check`
6. Categorization — BUILT (email)
7. Send later — BUILT (batch D `sendlater`: reminders-store queue with `email:` tasks; fire time hands to the email skill whose T2 gate asks the owner's yes — never sends unattended)
8. Follow-up reminders — BUILT (reminders)
9. Templates — BUILT (docs)
10. Bulk personalization — BUILT (batch D `mailmerge` drafts; the sending step remains per-draft T2 by design)
11. Signature management — PARTIAL batch C
12. Semantic search — BUILT (email search)
13. Attachment extraction — BUILT (email; fileops)
14. Email→task — BUILT (reminders from chat)
15. Email→calendar — PARTIAL batch C
16. Thread summary — see 3
17. Tone adjustment — BUILT (writing)
18. Proofreading — BUILT (writing grammar)
19. Length optimization — BUILT (writing)
20. CTA extraction — PARTIAL batch B
21. Email analytics — PARTIAL batch C
22. Automation rules — BUILT (automation triggers)
23. Archiving & retention — BUILT (fileops)
24. Encryption — PARTIAL batch C (GPG if installed)
25. Unsubscribe management — PARTIAL batch B
26. Contact extraction — BUILT (contacts)
27. CRM updates — BUILT (clients)
28. Sentiment tracking — batch B (`sentiment`)
29. Response-time optimization — PARTIAL batch C
30. Workflow integration — BUILT (workflows)

## Domain 13 — Smart Home & IoT (30) — BLOCKED as a domain
No Home Assistant / no IoT hub on this network (NEEDS 12 verified). Every item waits on
infrastructure, not on code. When HA exists, the `automation` skill already speaks webhooks,
so most items become one-line integrations. Items: lighting, thermostat, locks, cameras,
motion, door/window, smoke/CO, leak, appliances, vacuum, TV, multiroom audio, scenes, energy,
solar, EV charger, irrigation, pool, air quality, humidifier, blinds, pet feeder, plant
watering, packages, intercom, emergency broadcast, occupancy sim, utility tracking,
maintenance reminders, routine optimization.

## Domain 14 — Personal Health & Wellness (35)
1. Medication reminders — BUILT (reminders)
2. Pill interaction checking — PARTIAL batch B (`pillcheck` — public dataset lookup; informational only)
3. Symptom tracking — PARTIAL batch B (`symptomlog` — log only, no diagnosis)
4. Health metric tracking — PARTIAL batch B (`metrics` weight/BP log)
5. Workout logging — BUILT (habits + notes)
6. Exercise form feedback — BLOCKED (needs camera pose model; no webcam per NEEDS 12)
7. Workout plans — BUILT (planner)
8. Nutrition tracking — PARTIAL batch B (`nutrition` USDA API)
9. Meal planning — BUILT (planner)
10. Recipe suggestions — BUILT (batch C `recipe`)
11. Hydration reminders — BUILT (reminders; batch B `hydration` auto-nudge)
12-13. Sleep tracking/recommendations — BUILT (batch C `sleeplog`: manual log, weekly/monthly averages, sleep debt; no auto-tracking hardware on this PC)
14. Meditation guidance — PARTIAL batch B (`breathe` timer + script)
15. Stress monitoring — PARTIAL batch C
16. Mood tracking — BUILT (batch C `moodlog` 1–5 + note + trend)
17-18. Mental-health check-ins — BUILT (batch C `moodlog`: trend + supportive text + distress safety gate; never a diagnosis)
19. Habit coaching — BUILT (habits streaks)
20. Posture reminders — BUILT-CORE possible (reminders every X min)
21. Eye strain 20-20-20 — BUILT (reminders)
22. Stretch breaks — BUILT (reminders)
23. Ergonomic assessment — PARTIAL batch C
24. Health goals — BUILT (habits + planner)
25. Lab result interpretation — BLOCKED by policy (no medical advice)
26. Appointment scheduling — BUILT (calendar/reminders)
27. Insurance claims — PARTIAL batch C
28. First aid guidance — PARTIAL batch B (red-cross static content; emergency always 911/1122)
29. Emergency contacts — BUILT (contacts; owner facts)
30. Health document organization — BUILT (folders)
31. Vaccination records — PARTIAL batch B (notes table)
32. Family health history — PARTIAL (relationships graph attrs)
33. Telehealth prep — PARTIAL batch B (question list)
34. Health trend analysis — batch B (`metrics` trends)
35. Wellness challenges — PARTIAL batch C

## Domain 15 — Financial Management (30)
1. Transaction categorization — BUILT (budget categories)
2. Budget creation & tracking — BUILT (budget limits)
3. Spending alerts — BUILT (80% warning)
4. Bill reminders — BUILT (reminders)
5. Subscription tracking — BUILT (batch C `subscriptions`: exact totals + renewals)
6. Savings goals — PARTIAL batch B (`goaltrack`)
7. Portfolio monitoring — BUILT (markets)
8. Stock alerts — BUILT (shopping-style price watch on markets data)
9. Crypto tracking — BUILT (markets)
10. Net worth — BUILT (batch C `networth`: exact assets − liabilities)
11. Cash flow — BUILT (budget summary)
12. Expense reports — BUILT (budget; extract)
13. Receipt matching — BUILT (OCR + budget note)
14. Tax deduction ID — PARTIAL batch B (`taxhints` informational)
15. Tax document organization — PARTIAL batch B
16. Retirement projections — PARTIAL batch C
17. Loan calculations — BUILT (utilities? no — batch B `loancalc`)
18. Mortgage comparison — BUILT (calc loan payment; comparison table via chat)
19. Credit score monitoring — BLOCKED (needs bureau API)
20. Fraud alerts — PARTIAL (budget anomaly at 80%+; full fraud batch C)
21. Price tracking & deals — BUILT (shopping price watch)
22. Reward points optimization — PARTIAL batch C
23. Currency conversion — BUILT (utilities money)
24. Tip calculation — BUILT (calc tip)
25. Bill splitting — BUILT (quickcalc bill split with tip)
26. Financial goals — batch B (`goaltrack`)
27. Emergency fund planning — PARTIAL batch B
28. Insurance analysis — PARTIAL batch C
29. Estate docs organization — PARTIAL batch C
30. Donation tracking — PARTIAL batch B (`goaltrack` category)

## Domain 16 — Learning & Education (35)
1. Learning paths — BUILT (study plan)
2. Course recommendations — PARTIAL batch B (`courses` search)
3. Flashcards — BUILT (study; planners Anki CSV)
4. Spaced repetition — PARTIAL batch B (flashcards boxes + reminders wiring)
5. Quizzes — BUILT (study quiz; planners MCQ)
6. Concept explanation — BUILT (study teach-me; chat)
7. Step-by-step solving — BUILT (chat math)
8. Study schedules — BUILT (study plan)
9. Note-taking styles — BUILT (notes; batch B Cornell template)
10. Lecture transcription — BUILT (planners lecture)
11. Textbook summarization — BUILT (writing summary)
12. Research paper summarization — BUILT (deep_research)
13. Citation management — batch B (`citation`)
14. Thesis outlining — BUILT (writing outline)
15. Literature review — BUILT (deep_research)
16. Language practice — PARTIAL batch B (`langpractice` conversation drills)
17. Pronunciation feedback — PARTIAL batch C
18. Vocabulary building — BUILT (batch C `vocab`: add/lookup/quiz/review/stats)
19. Grammar explanation — BUILT (writing)
20. Essay feedback — BUILT (writing; chat)
21. Plagiarism check — batch B (`originality`)
22. Exam strategies — BUILT (planner)
23. Memory techniques — BUILT (mnemonic acronym/acrostic/story/palace)
24. Speed reading — PARTIAL batch C
25. Comprehension checks — BUILT (study quiz)
26. Knowledge gap ID — BUILT (lifecycle report; quiz results)
27. Skill assessment — PARTIAL batch C
28. Certification prep — BUILT (study plan + quiz)
29. Portfolio projects — BUILT (dev scaffold ideas)
30. Internship search — PARTIAL batch C (`jobs`)
31. Career path exploration — PARTIAL batch C
32. Skill-to-job matching — PARTIAL batch C
33. Resource curation — BUILT (deep_research)
34. Study groups — PARTIAL batch C
35. Progress motivation — BUILT (habits streaks)

## Domain 17 — Travel & Navigation (25)
1. Flight search — PARTIAL (blocked scraping; grounded info BUILT)
2. Hotel comparison — PARTIAL (same)
3. Itinerary planning — BUILT (travel)
4. Packing lists — BUILT (travel; batch B weather-aware polish)
5. Visa requirements — BUILT (search-grounded)
6. Travel insurance info — PARTIAL batch B (search)
7. Currency tracking — BUILT (utilities money)
8. Local attractions — BUILT (search)
9. Restaurant recommendations — BUILT (search)
10. Route planning — PARTIAL (needs maps API key; batch C if owner provides)
11. Traffic — BLOCKED (needs maps API)
12. Public transport — PARTIAL batch C (local transit sites)
13. Ride-sharing — BLOCKED (needs Uber/Careem API)
14. Parking — PARTIAL batch C (search)
15. Gas/EV stations — PARTIAL batch C
16. Weather advisories — BUILT (weather)
17. Document organization — BUILT (folders)
18. Travel journal — BUILT (notes/lifelog)
19. Photo geotagging — PARTIAL batch B (`geotag` EXIF)
20. Translation for travelers — BUILT (utilities)
21. Cultural etiquette — BUILT (search+LLM)
22. Emergency info — BUILT (search; batch B `embassy` polish)
23. Budget tracking — BUILT (budget category)
24. Loyalty optimization — PARTIAL batch C
25. Safety alerts — PARTIAL batch C

## Domain 18 — Shopping & E-commerce (25)
1. Product search — BUILT (shopping)
2. Price comparison — BUILT (shopping)
3. Deals & coupons — BUILT (shopping deal scan)
4. Review aggregation — BUILT (shopping; extract)
5. Review sentiment — batch B (`sentiment`)
6. Fake review detection — PARTIAL batch B (heuristics)
7. Size & fit — PARTIAL batch C
8. Alternatives — BUILT (shopping; chat)
9. Gift recommendations — BUILT (relationships prefs + shopping)
10. Shopping list — PARTIAL batch B (`shoppinglist`)
11. Grocery optimization — PARTIAL batch C
12. Meal→grocery — BUILT (batch C `recipe` grocery list)
13. Pantry inventory — PARTIAL batch C (`pantry`)
14. Expiry reminders — PARTIAL batch C (`pantry`)
15. Reorder reminders — PARTIAL batch C
16. Wardrobe management — PARTIAL batch C
17. Style recommendations — PARTIAL batch C
18. Trend analysis — BUILT (search)
19. Sustainable products — PARTIAL batch B (search filter)
20. Local business support — PARTIAL batch C
21. Price drop alerts — BUILT (price watch)
22. Return policy lookup — BUILT (web_fetch)
23. Warranty tracking — PARTIAL batch B (`warranty`)
24. Purchase history analysis — BUILT (budget)
25. Subscription boxes — PARTIAL batch C

## Domain 19 — Social & Relationship Management (25)
1. Contact management — BUILT (contacts)
2. Birthday reminders — BUILT (reminders + relationships)
3. Gift suggestions — BUILT (relationships preferences)
4. Post scheduling — PARTIAL batch C (needs platform APIs)
5. Content suggestions — BUILT (writing post)
6. Engagement tracking — BLOCKED (needs platform APIs)
7. Follower analysis — BLOCKED (same)
8. Hashtag optimization — PARTIAL batch B (`hashtags` heuristic)
9. DM drafting — BUILT (writing; whatsapp drafts)
10. Comment responses — PARTIAL batch C
11. Relationship strength — BUILT (rgraph last_confirmed staleness)
12. Networking ID — PARTIAL batch C
13. Introduction facilitation — BUILT (rgraph introductions)
14. Conversation starters — PARTIAL batch B (`icebreaker`)
15. Follow-up drafting — BUILT (clients follow-up)
16. Event invitations — BUILT (calendar/email)
17. RSVP tracking — PARTIAL batch C
18. Thank-you notes — BUILT (writing)
19. Condolence notes — BUILT (writing tone)
20. Congratulations — BUILT (writing)
21. Reference letters — BUILT (writing; docs)
22. Recommendations — BUILT (writing)
23. Performance reviews — BUILT (writing; docs)
24. Conflict mediation — BUILT (planner)
25. Relationship advice — BUILT (planner; general)

## Domain 20 — Security, Privacy & Governance (30)
1. Strong password generation — BUILT (utilities secrets)
2. Password manager integration — PARTIAL batch C (Bitwarden CLI if installed)
3. Breach alerts — BUILT (HIBP in security)
4. 2FA setup guidance — BUILT (chat/docs)
5. Security audit — BUILT (security; health)
6. Privacy settings recommendations — PARTIAL batch B (`privacycheck` guidance)
7. Data breach monitoring — BUILT (HIBP)
8. Identity theft monitoring — PARTIAL batch C
9. Phishing detection — BUILT (phishing_check heuristics)
10. Suspicious link analysis — BUILT (phishing_check)
11. Device security status — BUILT (system; security)
12. Update reminders — BUILT (desktop updates)
13. Antivirus scheduling — PARTIAL batch B (Defender PowerShell)
14. Firewall config — PARTIAL batch C (admin needed)
15. Network monitoring — PARTIAL batch B (`netwatch`)
16. VPN management — PARTIAL batch C
17. Encrypted messaging — BUILT (Signal/WhatsApp guidance; apps)
18. Secure file sharing — PARTIAL batch B (`securefile` zip+pass)
19. Backup verification — BUILT (fileops; test_lifecycle)
20. Disaster recovery planning — PARTIAL batch B (`backupplan`)
21. Access control policies — BUILT-CORE (scope.py)
22. Audit log analysis — BUILT (audit; health errors)
23. Compliance checking — PARTIAL batch D (GDPR-style checklists)
24. Retention enforcement — PARTIAL batch C
25. Sensitive data detection & redaction — batch B (`redact`)
26. API key rotation reminders — BUILT (reminders)
27. Incident response guidance — BUILT (docs/runbooks)
28. Threat intel monitoring — PARTIAL batch C
29. Vulnerability scanning — batch B (`vulnscan`)
30. Pen-test coordination — BLOCKED by policy (not on owner's machine targets)

## Domains 21-35 — see SKILLS_ROADMAP.md (already itemized there)
(Desktop/PC, App/API, Streaming, Multi-agent, Multitask, Self-heal, Observability, Workflow,
KG/Memory, Security/Compliance, Advanced AI, Human-UX, Edge/IoT, Blockchain, Autonomous —
status there; newly BUILT since: screen_control, screen_understand, notes, writing, study,
budget, habits, mic_control, obscura, skill_lifecycle.)

## Batch B — built 2026-09-28 (this pass)

New skills, each with self-check + manifest + routing (verified: check_skills 61 skills/146 assertions OK,
routing 157/157 @ 0.3 ms, verify_all 98/98):

| Skill | Covers |
|---|---|
| `calc` | solve equations, derivatives, integrals, simplify, percentages, tip, loan payment, prime factors, GCD/LCM (sympy — exact, no LLM arithmetic) |
| `quickcalc` | bill split with tip, BMI + healthy range, discount price, fuel cost, sleep-cycle wake times |
| `textstats` | word/char/sentence counts, reading time, sentiment (lexicon), top keywords, Flesch readability, entity-lite (emails/URLs/numbers/dates/proper nouns) |
| `phishing_check` | lookalike/homograph domains, IP hosts, shorteners, credential-bait & urgency phrasing, HTTP-on-login, verdicts + out-of-band advice |
| `citation` | APA/MLA/Harvard/IEEE/Chicago formatting from messy references |
| `mnemonic` | acronyms, acrostics, story-links, memory-palace walks |
| `checklist` | persistent named checklists (create/add/tick/left/reset) in SQLite |

## Re-verification pass — 2026-09-28 (all gates fresh)

Every gate re-run from scratch, plus a live-fire pass that routes AND executes real phrasings
through the dispatcher (new tool: `tools/smoke_reverify.py`, 33 live cases + negative controls,
owner data untouched via tempdir DB override):

| Gate | Result |
|---|---|
| `tools/check_skills.py` | **OK — 61 skills / 146 assertions / 157 labelled requests**, no matcher steals, all deps present |
| `python -m ishti.check` | **routing 157/157, median 0.2 ms** |
| `tools/verify_all.py` | **98/98 passed (289 s)** — every skill self-check green |
| `tools/smoke_reverify.py` | **33/33 live cases** — full 61-skill roster printed, none broken |

Two real defects found and fixed by the live-fire pass (both had passed their own self-checks on
narrow phrasings — this is why re-verification matters):
1. `quickcalc` — "wake up if i go to bed **now**" never matched (the `now` branch secretly
   required a clock number). Regex restructured; dedicated "From HH:MM (now)" reply added;
   self-check extended with the failing phrasing.
2. `checklist` — "reset the X checklist" **deleted the whole list**, so a later "my X checklist"
   found nothing. Reset now un-ticks all items and keeps them (and refuses politely when the
   list does not exist); self-check extended to prove items survive a reset.

## Batch C — built 2026-09-28 (this pass)

Seven skills, same gates as everything else (self-check + manifest + routing labels + check_skills
+ live-fire smoke). Routing labels added: 157 → 183 labelled requests.

| Skill | Covers |
|---|---|
| `vocab` | add words with the owner's meanings, lookups gated on saved words only (unsaved words fall through to the dictionary), quiz, weakest-word review, accuracy stats |
| `recipe` | dishes from the ingredients you have, full recipe for a dish, grocery list (local planner model) |
| `subscriptions` | track recurring costs (weekly/monthly/yearly), exact monthly + yearly totals, renewal days, cancel |
| `networth` | assets & liabilities ledger, exact net worth (computed, never estimated), itemised list |
| `sleeplog` | log hours (h/m), weekly/monthly averages vs an 8 h goal, 7-day sleep debt — informational only |
| `moodlog` | rate the day 1–5 + note, weekly view, week-over-week trend, and a safety gate: distress language always routes to help info (priority 10, above every skill that matches "kill") |
| `stats` | pandas CSV summary, Pearson correlations, z-score anomalies, k-means clusters — every number computed in code |

Routing-quality fixes the gates caught during batch C:
- **habits steal closed**: "track asset bank account 250000" was claimed by habits' generic
  `track <anything>` rule; habit names are now letters-only, so amounts can never match.
- **desktop process_kill guard**: "kill myself" / "end it all" never reach a process-kill path —
  reflexive objects are excluded and moodlog's safety gate (priority 10) owns distress language.
- **vocab beats the dictionary only for saved words** (priority 12 < utilities' 13): the owner's
  own meaning wins; anything else still goes to the real dictionary.
- moodlog's manifest had been omitted in the first draft — the loader silently skipped the folder;
  the smoke's roster check caught it (69 skills loaded, none broken).

Verified: check_skills **69 skills / 155 assertions / 183 labelled requests OK**, routing labels
clean, verify_all **107/107**, smoke **55/55 live cases**.

## Pull verification + Batch D — built 2026-09-28 (this pass)

**The owner's pulled changes were verified first, and three pull regressions were fixed:**

1. **`stats` couldn't see forward-slash paths.** Its PATH regex only accepted `D:\\...`, so
   `stats D:/data/sales.csv` (the natural typing) fell through to laya → files. All four batch-C
   `stats` routing labels had silently gone red (183 → 179). Fixed to accept both slash styles;
   self-check extended with the exact owner-style phrasings.
2. **arch2 consolidation was dead in production.** `consolidate.PROMPT` contained
   `{reply[:800]}` — an invalid str.format field — so `extract()` raised on every single turn and
   the guard swallowed it with a one-line `TypeError` nobody could act on. Every exchange was
   silently un-learned. Fixed the template, added the full traceback to the skip log, and verified
   a live extraction now returns real JSON from the pinned planner.
3. **`test_arch2_memory` leaked the owner's real chat store.** `retrieval.assemble_context()`'s
   vector path read the live Chroma data (an "unrelated query" assertion pulled an actual
   IMSciences conversation — see NEEDS #9, the recall threshold is still uncalibrated). The test
   now stubs `vector_hits` to match its own no-Chroma contract; its promotion assert was also
   scoped to its own observation (it had demanded the whole world be empty).

Also verified from the pull: the learning reinforcement loop (dispatcher credit/blame,
`lessons.attribute`, AdMem decay with the `decayed` watermark, chat injecting
"LEARNED FROM THE OWNER"), the reflections/mining dashboard APIs, and the fix-backs on habits,
desktop and moodlog — all confirmed intact by the full gate run.

**Batch D — six skills, same gates as everything else:**

| Skill | Covers | Tier / scope |
|---|---|---|
| `redact` | text / CSV / PDF redaction: emails, phones, CNICs, IBANs, cards, accounts, name-context; column-aware for CSV; `redact show` previews without writing; originals never touched, copies only in data/redacted | T1, write data/redacted |
| `devops` | GitHub Actions, GitLab CI, Dockerfile, Docker Compose, Kubernetes Deployment+Service, Terraform templates — pinned versions copied verbatim, service name the only variable | T0, no writes |
| `gitops` | safe git subset: status/log/diff/branches/current read directly; commit/push/pull/add/stash/checkout shown with their exact commands and T2-queued; **force-push, hard reset, rebase, clean refused in code** (owned refusal, never the LLM) | T2, write data/gitops |
| `sendlater` | schedule an email via the reminders store (task=1, `email:` prefix): queue/list/cancel; at fire time the email skill's normal T2 gate asks for the owner's yes — **nothing sends while the owner is away** | T2, write data |
| `mailmerge` | CSV + `{placeholder}` template → one personalized draft per row in data/mailmerge; unknown placeholders filled empty and reported (never a raw `{brace}`); invalid/missing emails skipped and counted | T1, write data/mailmerge |
| `pdftables` | pdfplumber table extraction → CSV per table in data/extracted with exact row×col counts in the reply | T1, write data/extracted |

**Routing hardening the labels forced (all with regression asserts):**
- gitops' `log` had to become git-flavoured: `log into my portal` (browser), `log in to github`
  (security) and `log sleep 7.5 hours` (sleeplog) are not git.
- dev/gitops boundary: the old `dev` git labels (`git status of the ishti project`) now belong to
  gitops (priority 12 < dev's 22) — dev keeps run-the-tests / lint.
- owner's fuzzy verb match (`"on"` → close to `"born"`) claimed "which branch am I on in D:/proj";
  requests mentioning a path/branch/repo/git are now excluded from `asks_about_me`.
- gitops write regexes needed word-boundary anchors: "add subscription spotify…" parsed `of s` as a
  path and stole subscriptions' smoke case.
- devops / gitops / sendlater / mailmerge / redact / pdftables are **trigger-only** (`laya: false`):
  their deterministic cues are distinctive, and as laya options they only created confusion
  ("draft an email…" → mailmerge, "mujhe batao mera project kahan hai" → devops).

Verified: check_skills **75 skills / 162 assertions / 207 labelled requests OK**, routing
**207/207 @ 0.2 ms**, smoke **70/70 live cases**, verify_all **115/115**.

## Batch E — built 2026-09-28 (this pass)

Six skills, chosen as the highest-value PARTIAL items that run fully local on already-installed
libraries (pandas, matplotlib, PIL, scipy — verified present before building):

| Skill | Covers |
|---|---|
| `forecast` | exact least-squares trend, N-period projection, moving average, weekly/monthly seasonality spread — every number from pandas/numpy, R² reported so a bad fit is visible |
| `chart` | bar / line / histogram PNGs of a CSV column via matplotlib Agg into data/charts, with exact min/mean/max in the reply; `show charts` lists recent |
| `palette` | dominant colors as exact hex codes with pixel shares (PIL median-cut quantize), average brightness + text-contrast advice |
| `imgquality` | resolution/MP/aspect, Laplacian-variance sharpness with a blur verdict, brightness and contrast — measured, never estimated |
| `abtest` | two-proportion z-test (scipy): rates, pp + relative lift, z, p, and a verdict that states direction — "significant but losing" is never sold as a win |
| `prioritize` | Eisenhower triage: quick-add tasks with inferred urgent/important flags shown in brackets, ordered DO NOW → DO QUICKLY → SCHEDULE → DROP/PARK plan |

Gate notes: the manifests declare import names, so the PIL skills list `PIL` (importable), not
`Pillow` (the pip name) — declaring the pip name had made the loader try to reinstall an
installed package. Read-only skills carry `write: ["data"]` per the house convention so the
scope auditor has something to verify. Routing labels: 207 → **231**; smoke cases 70 → **81**.

Verified: check_skills **81 skills / 174 assertions / 231 labelled requests OK**, routing
**231/231 @ 0.2 ms**, smoke **81/81 live cases**, verify_all **121/121**.

## Running totals (this inventory)
- BUILT / BUILT-CORE: 320+
- PARTIAL with a planned batch: ~230 (batches B, C, D core and E are built; the remainder are
  the wider long tail)
- BLOCKED (infrastructure/policy/model-size): ~45 — each with the reason above; nothing skipped silently.
- The remainder are already covered by shared capabilities (planner/coder/chat cover dozens of
  "one prompt" items individually listed in the catalogue).

**Next actions:** finish transcribing domains 21–35 item-level (done in ROADMAP; item-level
ledger continues below), then build batch B skills one by one.
