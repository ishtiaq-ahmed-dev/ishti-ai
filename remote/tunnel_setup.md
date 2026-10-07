# Remote access: ngrok first, Cloudflare optional

ISHTI binds to `127.0.0.1`; it does not open a LAN or public listening port. A tunnel is started only after the owner turns **Remote access** on. Proxied requests are never trusted as loopback: the phone must sign in with the owner access key or a five-minute, single-use link.

## Option A — ngrok

1. Install and authenticate ngrok from its official Windows instructions. Keep the authtoken in ngrok's own user configuration; never paste it into ISHTI or a report.
2. Start ISHTI normally.
3. On the ISHTI PC, open **Settings > Remote** and press **Start tunnel**.
4. Copy the displayed `https://…ngrok-free.app` URL.
5. Either open that URL on the phone and enter the access key, or press **Create one-time login link**, copy it, and open it on the phone within five minutes. The link works once.
6. Press **Stop** when remote access is no longer needed.

The equivalent local commands are:

```powershell
start.bat tunnel start 8888
start.bat tunnel status
start.bat tunnel stop
```

ISHTI launches `ngrok http 127.0.0.1:8888 --log=stdout`, discovers the HTTPS URL through ngrok's loopback agent API, and stops its owned child process during ISHTI shutdown. It never prints or reads the ngrok authtoken.

On ngrok's free plan, a browser interstitial can appear on the first visit to an endpoint. The owner should continue through it once. Non-browser API clients may send `ngrok-skip-browser-warning`, but ISHTI does not bypass the screen server-side. ngrok's older `--basic-auth` and `--oauth` flags are deprecated; use ngrok Traffic Policy if a second edge authentication layer is desired.

Official references: [HTTP agent command](https://ngrok.com/docs/gateway/agent/cli#ngrok-http), [local agent API](https://ngrok.com/docs/gateway/agent/api#base-url-and-authentication), [tunnel listing](https://ngrok.com/docs/gateway/agent/api#list-tunnels), and [browser warning](https://ngrok.com/abuse#browser-warning).

## Session safety

- The long-lived access key is accepted only in the login form or as an `Authorization: Bearer` credential for scripts. It is never placed in a URL or cookie.
- Browser cookies contain a random 256-bit session identifier; only its hash and device metadata are stored server-side.
- Sessions are `HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS, idle-expire after 30 days, and can be revoked individually or all at once from the local PC.
- Five failed keys lock that source IP for ten minutes. Key rotation and revoke-all are local-desktop-only actions.
- Keep the public URL private. T2 actions still require the normal explicit yes/no confirmation.

The session design follows the [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

## Option B — Cloudflare Tunnel

Cloudflare remains suitable when the owner already has a managed domain and wants Cloudflare Access in front of ISHTI.

1. Install and authenticate `cloudflared` using Cloudflare's official instructions.
2. Create a named tunnel whose ingress target is `http://127.0.0.1:8888`.
3. Put a Cloudflare Access policy in front of the hostname and allow only the owner's identity.
4. Open the hostname and use the same ISHTI login page. Do not put the access key in the URL.

Cloudflare headers such as `CF-Connecting-IP` and `X-Forwarded-*` deliberately remove implicit loopback trust, so the ISHTI session is still required behind Cloudflare Access.
