# Remote Access Setup: Cloudflare Tunnel + Cloudflare Access (Zero Trust)
*Secure, zero-cost remote reachability for ISHTI from your smartphone, tablet, or external laptop without port forwarding or exposing plain ports.*

> **ISHTI requires a token for every tunnelled request** (cloudflared connects from 127.0.0.1, so
> ISHTI treats any request carrying `Cf-Connecting-Ip` / `X-Forwarded-For` as remote). The token is
> in `D:\ISHTI\data\api_token` (created on first start). On your phone, open
> `https://<your-tunnel-host>/?token=<token>` once; a secure cookie handles the rest. API clients send
> `Authorization: Bearer <token>`. Keep Cloudflare Access in front as well.

---

## Architecture Overview
```
[Your Phone / Remote Device]
             │
             ▼
   [Cloudflare Edge] (Cloudflare Zero Trust Access / Email OTP or GitHub Login)
             │
             ▼ (Encrypted Outbound Tunnel)
    [cloudflared daemon] on Windows PC
             │
             ▼ (Localhost only)
   [ISHTI Web UI: http://127.0.0.1:8888]
```

---

## 1. Prerequisites (Free Tier)
1. A free [Cloudflare Account](https://dash.cloudflare.com/).
2. A domain added to Cloudflare (e.g. `yourdomain.com`).
3. Cloudflare Zero Trust (free for up to 50 users).

---

## 2. Step-by-Step Installation

### Step 1: Install `cloudflared` on Windows
Run PowerShell as Administrator or standard user:
```powershell
winget install --id Cloudflare.cloudflared
```
Verify installation:
```powershell
cloudflared --version
```

### Step 2: Authenticate cloudflared with Cloudflare
```powershell
cloudflared tunnel login
```
*A browser window will open. Select your domain to generate the certificate file.*

### Step 3: Create the ISHTI Tunnel
```powershell
cloudflared tunnel create ishti-agent
```
*Take note of the generated Tunnel ID (UUID) and the JSON credentials file path in `C:\Users\<YourUser>\.cloudflared\<UUID>.json`.*

### Step 4: Configure the Tunnel
Create a configuration file at `D:\ISHTI\remote\config.yml`:
```yaml
tunnel: <YOUR-TUNNEL-UUID>
credentials-file: C:\Users\<YourUser>\.cloudflared\<YOUR-TUNNEL-UUID>.json

ingress:
  - hostname: ishti.yourdomain.com
    service: http://127.0.0.1:8888
  - service: http_status:404
```

### Step 5: Route DNS Traffic to the Tunnel
```powershell
cloudflared tunnel route dns ishti-agent ishti.yourdomain.com
```

### Step 6: Enforce Cloudflare Access (Authentication Barrier)
> [!IMPORTANT]
> Never expose ISHTI without authentication! The confirm-before-consequential action gate prevents accidents, but authentication prevents unauthorized prompt execution.

1. Navigate to **Cloudflare Zero Trust Dashboard** > **Access** > **Applications**.
2. Click **Add an Application** > Select **Self-hosted**.
3. Set Application Name: `ISHTI Neural Core`.
4. Set Domain: `ishti.yourdomain.com`.
5. Under **Policies**:
   - Policy Name: `Authorized Owner Only`.
   - Action: `Allow`.
   - Rule: `Include` > `Emails` > Enter your exact personal email address.
6. Under **Authentication**:
   - Enable **One-Time PIN (OTP)** or your OAuth provider (Google/GitHub).
7. Save Application.

---

## 3. Starting the Tunnel on Demand

To launch the tunnel alongside ISHTI:
```powershell
cloudflared tunnel --config D:\ISHTI\remote\config.yml run
```
Or start as a Windows Service (optional):
```powershell
cloudflared service install
```

When you visit `https://ishti.yourdomain.com` from your phone:
1. Cloudflare prompts for your email.
2. A 6-digit PIN is sent to your inbox.
3. Upon entering the PIN, you have full, secure, encrypted access to the holographic ISHTI interface!
