# Connecting your accounts (Client ID + Client Secret)

ISHTI can reach your accounts two ways. The **browser path** (Chrome Profile 9) is the default and needs no
setup. The **API path** is faster, needs no browser, and is what "connect …" turns on. One flow serves
everything - you paste a Client ID + Client Secret, ISHTI opens that service's own consent page once, and
keeps the token.

## Supported right now (say `what services can i connect`)

| Ask to connect | Goes to | What you need |
|---|---|---|
| `gmail` | Gmail API | Google Cloud → **Gmail API** → Desktop OAuth client |
| `drive` | Google Drive v3 | same client; Drive API is automatic via the same scopes |
| `sheets` | Sheets v4 | same client |
| `docs` | Docs v1 | same client |
| `calendar` | Calendar v3 | same client |
| `people` | People/Contacts v1 | same client |
| `youtube` | YouTube Data v3 | same client |
| `microsoft` | Microsoft Graph → **Outlook mail, OneDrive** | Entra app registration → Web platform, `http://localhost` |
| `github` | GitHub REST API | GitHub → Settings → Developer settings → OAuth Apps |
| `instagram` | Meta Graph API (IG messaging/comments) | Meta for Developers app + Instagram account linked to a Page |
| `facebook` | Meta Graph API (Pages posts/engagement) | same Meta app |
| `linkedin` | LinkedIn API | LinkedIn Developer Portal app |
| `telegram` | Bot API | not OAuth - give it the bot token, and it's stored in config |

`outlook`, `onedrive`, `contacts`, `google drive`, `gdrive`, `sheet`, `ig`, `mail` are understood as
aliases of the above, so plain English works.

## 1. One-time setup (per service, ~3 minutes)

**Google (gmail, drive, sheets, docs, calendar, people, youtube)**
1. <https://console.cloud.google.com/> → create/select a project.
2. **APIs & Services → Library** → enable the API you need (Gmail API, Drive API, …).
3. **OAuth consent screen** → add yourself as a **Test user**. *Testing* is fine; the connector tells you if
   a refresh token expires after 7 days (publishing the app removes that limit).
4. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app.**
5. Copy the **Client ID** and **Client secret**.

**Microsoft** → <https://portal.azure.com/> → App registrations → New registration → *Web* platform, redirect
`http://localhost` → Certificates & secrets → *New client secret*.

**GitHub** → Settings → Developer settings → OAuth Apps → *New OAuth App* → callback URL `http://127.0.0.1`.

**Meta (Instagram/Facebook)** → developers.facebook.com → your app → Facebook Login → *Web OAuth Redirect
URI* `http://127.0.0.1` → add the `pages_show_list`, `instagram_basic`, `instagram_manage_messages` permissions.

**LinkedIn** → developer portal → Products → *Sign In with LinkedIn* → redirect `http://127.0.0.1`.

## 2. Give them to ISHTI

```
connect drive client-id 1234567890.apps.googleusercontent.com client-secret GOCSPX-xxxxxxxxxxxx
```
(one Client ID can serve all the Google services — just `connect drive`, `connect sheets`, …)

Or put them in `ishti/config.json` → `oauth.<service>.client_id` / `client_secret`.

## 3. Authorise once

Say **`connect drive`** (etc.). Your browser opens that service's own consent page — click **Allow**.
ISHTI listens on `http://127.0.0.1:<random>/`, takes the code back and stores the token in
`data/oauth/<service>.json`. After that the access token refreshes itself silently.

- **`what is connected`** → every service with its state
- **`disconnect drive`** → revokes the grant and deletes the token

## Why the loopback redirect is correct here

Google's *Loopback IP Address flow Migration Guide* deprecates that flow **only** for the iOS, Android and
Chrome client types — "it will continue to be supported on **desktop apps**". ISHTI is a desktop app, so
`http://127.0.0.1:<port>/` is the documented redirect for a **Desktop app** client. PKCE (`code_challenge`,
S256) is used on top, and `access_type=offline&prompt=consent` guarantees the refresh token on first
connect. Installed apps "cannot keep secrets" — the client secret ships with the app, which is why Google
treats it as an identifier. It still stays on your PC.

## What changes in the assistant

| Ask | Not connected | Connected |
|---|---|---|
| "Do I have any unread emails?" | opens Gmail in Profile 9 and reads the page | direct API call, no browser |
| "Read the email from X" / "search my email for Y" | browser | direct API call |
| "Open my Gmail" | opens the web inbox (you asked for the web) | unchanged — still opens the web |
| API fails for any reason | — | falls straight back to the browser, and says so |

## Files

- `ishti/oauth.py` — the generic installed-app OAuth2 connector **and the provider registry**
  (`PROVIDERS`, `providers()`, `scopes_for()`, `api()`).
- `ishti/gmail_api.py` — Gmail REST over that token (unread / read / search / send / draft / mark read).
- `ishti/skills/connectors/` — the `connect …` / `what is connected` / `disconnect …` skill.
- `ishti/config.json` → `oauth.<service>` — your credentials.
- `data/oauth/<service>.json` — the token (this PC only).

Self-checks: `venv\Scripts\python -X utf8 ishti\oauth.py`, `...\ishti\gmail_api.py`,
`...\ishti\skills\connectors\skill.py`.