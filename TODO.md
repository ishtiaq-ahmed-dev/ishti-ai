# ISHTI - TODO (from the owner's requests, 2026-09-26)

Worked top to bottom. Each item is checked only after it has been verified by using ISHTI like a person would (the app, or a replay of the owner's own messages).

## A. Understand the request before acting (top priority)
- [x] Follow-ups use the conversation: "search now", "why didn't you search?", "do it using the names" are rewritten into one standalone request before routing (shown in Thinking as "understood as: …")
- [x] Product questions ("price and specs of rtx 5070ti") go to web search, not the PC-status skill
- [x] The "KNOWN FACTS" header no longer leaks into answers
- [x] A full video path + "edit … put the name on it" goes to the video skill (text overlay works; the FFmpeg font problem on Windows is fixed)
- [x] Replay of the owner's failing conversation gives real answers on every turn
- [ ] Research uses the *understood* request: 1-2 focused queries when it's a fallback, no tangent queries ("meaning of the phrase …")
- [ ] Laya upgrade: more labelled examples from real traffic (every correction becomes one); fine-tune once there are enough (NEEDS #1)

## B. Graph and Thinking UI
- [ ] Graph opens full-screen (expand button); pan and zoom instead of growing off-screen
- [ ] Nested structure: each skill is a group that holds its own tool calls (collapsible), so long runs stay readable
- [ ] A graph for every question (per message) - and one **conversation graph** for the whole chat: topics, skills used, what was done
- [ ] Prompt box grows with the text (auto-resize up to a limit)

## C. New "Overview" section
- [ ] The full pipeline (input → understanding → router/Laya → planner → skills → tools → verification → answer), live counts
- [ ] All skills with status, what they need and how often they are used
- [ ] The owner's data: profile, saved facts and projects, preferences, reminders

## D. Files: organize by content, not only by extension
- [ ] Detect audio-only .mp4/.webm with ffprobe → Audio, not Videos (the owner's Videos folder got audio files)
- [ ] Organize by name and content: read text from PDFs/docs (first page), group by topic (invoices, company mail, university …), preview → yes → move, with undo

## E. Shopping: best price from verified stores
- [ ] "Find me a <product>" → search official and verified stores (the manufacturer, and big retailers for PK and international), pull prices and links, and return a comparison table sorted by price, with the cheapest highlighted

## F. Use the owner's own Chrome profiles
- [ ] Profile 10 (`C:\Users\PC\AppData\Local\Google\Chrome\User Data\Profile 10`): searching and anything that needs a Google login
- [ ] Profile 9 (`…\User Data\Profile 9`): WhatsApp Web
- [ ] Nothing else is ever touched. Sending messages or anything consequential still waits for a yes.
- Approach: Playwright MCP `--extension` mode connects to the running Chrome through the "Playwright MCP Bridge" extension (a copied profile would lose logins, and Chrome blocks remote-debugging on its real user-data folder). **Owner action needed:** install that extension in Profile 9 and Profile 10.

## G. Carry-overs (NEEDS.md)
- [ ] Skill builder: create new skills when nothing fits (NEEDS #10)
- [ ] Video editing like an editor: analyse, auto-edit, open in CapCut (NEEDS #11)
