# Tailscale Setup — Access Chilli Gavva From Anywhere

Zero-cost. Works from office, cafe, phone, travel — no port forwarding, no static IP needed.

## What Tailscale Does

Creates a private encrypted network between all your devices.
Your Mac (agent server) + Windows PC + Phone → all see each other as if on same local WiFi.
You SSH into Mac from anywhere, check approval queue, approve/reject tasks.

## Step 1: Install Tailscale on Mac (agent machine)

```bash
# Download from https://tailscale.com/download/mac
# Or via Homebrew:
brew install --cask tailscale

# Start and login (opens browser)
tailscale up
```

Note your Mac's Tailscale IP:
```bash
tailscale ip -4
# Example output: 100.64.x.x  ← remember this
```

## Step 2: Install Tailscale on Windows PC

Download installer: https://tailscale.com/download/windows
Run, login with same account.

Verify from Windows:
```
ping 100.64.x.x   ← Mac's Tailscale IP
```

## Step 3: Install Tailscale on Phone

- Android: Play Store → Tailscale
- iPhone: App Store → Tailscale
Login with same account.

## Step 4: Enable SSH on Mac (if not already)

System Settings → General → Sharing → Remote Login → ON

Test from phone (Termius app, free):
```
ssh yourusername@100.64.x.x
```

## Step 5: Access Chilli Gavva Dashboard Remotely

From anywhere (phone/Windows/office laptop):

**Option A — SSH + check approval queue:**
```bash
ssh yourusername@100.64.x.x
cd ~/Documents/Chilli_Gavva_Agent
python3 start.py status
```

**Option B — Web dashboard via Tailscale:**
```bash
# On Mac, serve dashboard:
python3 serve.py
# (already runs on 127.0.0.1:8787 normally)

# From phone browser, go to:
http://100.64.x.x:8787
```

But serve.py serves on 127.0.0.1 (localhost only). To allow Tailscale access:
```bash
CHILLI_BIND=0.0.0.0 python3 serve.py
```
Then from phone: `http://100.64.x.x:8787`

## Approve Tasks From Phone

```bash
# SSH into Mac from phone, then:
cd ~/Documents/Chilli_Gavva_Agent
python3 start.py approve <task_id>
python3 start.py reject <task_id>
```

Or open the web dashboard on phone browser.

## Security Notes

- Tailscale traffic is encrypted end-to-end (WireGuard).
- Tailscale IP (100.x.x.x) is only reachable from your own devices — not the public internet.
- Free plan: up to 3 users, 100 devices. More than enough.
- No firewall rules needed; no router config needed.

## Troubleshooting

| Problem | Fix |
|---|---|
| Can't ping Mac from phone | Check Tailscale is running on Mac: `tailscale status` |
| Dashboard not loading | Add `CHILLI_BIND=0.0.0.0` when starting serve.py |
| SSH rejected | Enable Remote Login in Mac System Settings → Sharing |
| Slow connection | Normal for first connect; subsequent connects are fast |

## One-Line Check (run this any time)

```bash
tailscale status
```
Should show all your devices with green dots.

---
*Free tier. No credit card. No expiry. 3 devices max on free plan — Mac + Windows + Phone = exactly 3.*
