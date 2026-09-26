# ISHTI - TODO (from the owner's requests, 2026-09-26)

Worked top to bottom. Each item is checked only after it has been verified by using ISHTI like a person would (the app, or a replay of the owner's own messages).

## A. Understand the request before acting (top priority)
- [x] Follow-ups use the conversation: "search now", "why didn't you search?", "do it using the names" are rewritten into one standalone request before routing (shown in Thinking as "understood as: …")
- [x] Product questions ("price and specs of rtx 5070ti") go to web search, not the PC-status skill
- [x] The "KNOWN FACTS" header no longer leaks into answers
- [x] A full video path + "edit … put the name on it" goes to the video skill (text overlay works; the FFmpeg font problem on Windows is fixed)
- [x] Replay of the owner's failing conversation gives real answers on every turn
- [x] Research uses the *understood* request: 1-2 focused queries when it's a fallback, no tangent queries ("meaning of the phrase …")
- [ ] Laya upgrade: more labelled examples from real traffic (every correction becomes one); fine-tune once there are enough (NEEDS #1)

## B. Graph and Thinking UI
- [x] Graph opens full-screen (expand button); pan and zoom instead of growing off-screen
- [x] Nested structure: each skill is a group that holds its own tool calls (collapsible), so long runs stay readable
- [x] A graph for every question (per message) - and one **conversation graph** for the whole chat: topics, skills used, what was done
- [x] Prompt box grows with the text (auto-resize up to a limit)

## C. New "Overview" section
- [x] The full pipeline (input → understanding → router/Laya → planner → skills → tools → verification → answer), live counts
- [x] All skills with status, what they need and how often they are used
- [x] The owner's data: profile, saved facts and projects, preferences, reminders

## D. Files: organize by content, not only by extension
- [x] Folder questions count subfolders too and skip Office lock files (after organizing, "how many pdfs" said 1)
- [x] Detect audio-only .mp4/.webm with ffprobe → Audio, not Videos (the owner's Videos folder got audio files)
- [x] Organize by name and content: read text from PDFs/docs (first page), group by topic (invoices, company mail, university …), preview → yes → move, with undo

## H. Chat history (owner request)
- [x] Recent chats in the sidebar (clean list, newest first); click to reopen a chat with its messages (and each answer's thinking + graph, saved with it); new chat button; delete on hover

## E. Shopping: best price from verified stores
- [x] "Find me a <product>" → search official and verified stores (the manufacturer, and big retailers for PK and international), pull prices and links, and return a comparison table sorted by price, with the cheapest highlighted
- Done as the `shopping` skill: prices read from the store pages (schema.org data, else the page text), only the brand's exact domain counts as official, retailers from an allow-list, price-guide sites shown separately, S25 never priced from an S25 Ultra page. Daraz renders prices with JavaScript, so it is usually missing (ceiling: read it through the browser).

## F. Use the owner's own Chrome profiles
- [x] Profile 10 (`C:\Users\PC\AppData\Local\Google\Chrome\User Data\Profile 10`): searching and anything that needs a Google login
- [x] Profile 9 (`…\User Data\Profile 9`): WhatsApp Web
- [x] Nothing else is ever touched. Sending messages or anything consequential still waits for a yes.
- Approach: Playwright MCP `--extension` mode connects to the running Chrome through the "Playwright MCP Bridge" extension (a copied profile would lose logins, and Chrome blocks remote-debugging on its real user-data folder). **Owner action needed:** install the Playwright Extension in Profile 9 and Profile 10, then paste each profile's token in Settings → Chrome profiles. Until then ISHTI uses its own isolated browser, and WhatsApp says it isn't connected.
- [x] Safety fix found while testing: a click sent as a bare element ref (`target: e30`) skipped the consequential-action gate; the gate now reads what that element is on the live page

## G. Carry-overs (NEEDS.md)
- [ ] Skill builder: create new skills when nothing fits (NEEDS #10)
- [ ] Video editing like an editor: analyse, auto-edit, open in CapCut (NEEDS #11)

## I. Owner's real chats, 26 Sep (fix first)
- [x] "i study in imsciences" was web-searched → personal statements ("I study/work at…", "I'm a…", "my birthday is…") are saved as owner facts
- [x] "where i stid ?" (typo) was web-searched for "STid" → questions about the owner answer from saved facts (typo tolerant); if not saved, say so. Never the web
- [x] "check my whatsap…" missed the WhatsApp skill (typo) and got generic web advice → typo-tolerant trigger; unread chats are read straight from the page (5 s instead of 85 s)
- [x] The saved Chrome tokens didn't work: the extension shows `PLAYWRIGHT_MCP_EXTENSION_TOKEN=…` and that whole line was pasted → the prefix is stripped on save and load
- [x] "why your response is late…", "you do not perform parallel tasks… fix yourself" went to browser → web → deep research → questions about ISHTI itself are answered honestly from its own skills and limits, never escalated to the web
- [x] Queued messages: the owner can keep typing and sending while ISHTI works; messages run in order
- [x] "check the weather today and also … videos in download edit that" did only the weather → "and also" requests run every part
- [x] "videos in download edit that" / "the latest video in the download" → list the recent videos and ask which one; "latest"/"first" picks it; a bare path asks what edit to make
- [x] Web answers ended with "KNOWN FACTS override this…" → the shared style prompt no longer mentions KNOWN FACTS (only the chat skill, which actually has them)

## J. Multitasking and learning the owner (owner request, 26 Sep)
- [x] Several different tasks in one message: Laya routes each clause; two or more skills = a plan; independent steps run in parallel (3 tasks in 2 s), chained ones ({prev}) in order
- [x] Several messages at once: the chat sends up to 3 together (more queue above the input); the server runs independent requests of one chat side by side, each with its own live trace; a follow-up, yes/no or correction waits for the answer it is about; browser work takes turns (one page)
- [x] Personal and professional facts are saved when stated ("I study at…"); lasting preferences and working style are learned in the background from every message (grounded: a value must share a word with the message), backfilled from past chats, and applied to answers through the style prompt
- [ ] Laya fine-tune on the route log + corrections once there are enough labels (NEEDS #1) - still the path to "more accurate"

## K. Owner requests + chat failures, 26 Sep (afternoon)
Fixes from the chats:
- [ ] "the new era" as the answer to "Which video?" went to web search → names match without filler words; "top/first video" picks it
- [ ] "i am in th semester" wasn't saved; "add in memory …" crashed the apps skill (TypeError) → "I am in…" is a fact; "add/save it in memory" uses the conversation; apps crash fixed at the root
- [ ] "you didn't even open CapCut…" re-listed videos → complaints about the last answer are follow-ups (redo it the right way)
- [ ] Bitcoin answer mixed $96k and $84k from different pages → live prices come from market APIs, not page text
New:
- [ ] "About you" section: see and edit everything ISHTI knows about the owner, plus custom instructions sent with every message, and a preview of exactly what is sent
- [ ] Reply in the owner's language (Urdu, Roman Urdu, English)
- [ ] Markets: stocks, crypto, gold, currency with a real analyzer (trend, RSI, moving averages, volatility, fundamentals) and a Markets section; research open-source stock terminals (OpenBB etc.)
- [ ] Edited videos/images play and download inside the chat
- [ ] "send this video to qasim ims" → WhatsApp: find the contact (fuzzy for spelling/voice errors), exact match sends, otherwise ask which one; attach the file
- [ ] Video editing like an editor: professional ad-style edit (intro title card, captions, music bed, transitions, grading, loudness), open in CapCut (install it with winget after a yes if missing), and verify the result frame by frame with a local vision model
- [ ] More skills: prayer times, currency, news, translate, dictionary, document summary, unit conversion
- [ ] Install skills from GitHub repos (show the code, install only after a yes, run isolated)
