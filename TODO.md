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
