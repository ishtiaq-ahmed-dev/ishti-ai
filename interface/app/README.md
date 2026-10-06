# ISHTI dashboard (React + TypeScript + Vite)

The desktop/web UI for ISHTI: chat, working-status view, connectors (WhatsApp/Telegram), profiles,
learning and privacy panels. Talks to the FastAPI backend (`interface/web_ui/server.py`, started by
`start.bat` / `python -m ishti`).

## Commands

```bash
npm install            # once
npm run dev            # dev server with HMR (proxies to the backend)
npm run build          # production build into dist/ (served by the backend at /)
npm run preview        # serve the built dist locally
```

The backend serves `dist/` at `http://127.0.0.1:8888/`. After a rebuild, restart the backend
(it caches `index.html` for 24 h).

## Layout

- `src/lib/chat-store.ts` — app-level store: chats, messages, runs, drafts, unread badges, event
  polling with backoff and gap resync. Chat is **store-driven** per CONTRACT v2: one event stream,
  idempotent sends (`client_msg_id`), optimistic UI, deterministic replay on start.
- `src/sections/Chat.tsx` — chat: working-status card (steps, elapsed, Stop, streamed draft),
  feedback panel, sidebar (search, select, rename, delete+undo, share, drafts), Personal/Business
  groups. Each chat pins its profile at creation; namespaces never mix.
- `src/components/` — RunStatusCard, FeedbackPanel, ProfileSwitcher, ConnectorsPage, AgentsPanel,
  LearningPanel.
- `frontend/mock/server.py` — stdlib mock backend implementing CONTRACT v2 for UI work without the
  real engine (`python frontend/mock/server.py 8901`; checks: `python frontend/mock/test_mock.py`).

## Notes

- Against the real backend, the chat needs the CONTRACT v2 endpoints (`/api/chats`, `/api/runs`,
  `/api/events`) — currently on branch `agent/backend`. Until that merge, the mock covers UI work.
- Type check: `npx tsc --noEmit`. Keep it clean before committing.
- No secrets are ever rendered; page text and web content are treated as untrusted data.
