# Cloud 24/7 Setup (GitHub Actions)

Zero-cost. Runs every 30 minutes automatically. Requires GitHub account (free).

---

## Step 1 — Create GitHub Repository

1. Open https://github.com/new in browser
2. Repository name: `chilli-gavva-agent` (or any name)
3. **Set to Private** (your draft content and activity log is in `agent_data.db` — don't expose it publicly)
4. Do NOT check "Add a README file"
5. Click **Create repository**
6. GitHub shows a page with setup commands — copy the repo URL (looks like `https://github.com/YOUR-USERNAME/chilli-gavva-agent.git`)

---

## Step 2 — Push Code to GitHub

Open Terminal in the project folder (`/Users/vudathagroup/Documents/Chilli_Gavva_Agent`) and run:

```bash
git remote add origin https://github.com/YOUR-USERNAME/chilli-gavva-agent.git
git push -u origin main
```

Replace `YOUR-USERNAME` with your actual GitHub username.

It will ask for GitHub username + password. For password: use a **Personal Access Token** (not your account password):
- Go to https://github.com/settings/tokens
- Click "Generate new token (classic)"
- Note: `Chilli Gavva Agent push`
- Scope: check `repo` (full repo access)
- Expiration: 90 days or No expiration
- Click Generate — copy the token (shown once only)
- Paste the token as your password when git asks

---

## Step 3 — Add GROQ_API_KEY Secret

1. In your GitHub repo page → **Settings** tab
2. Left sidebar → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Name: `GROQ_API_KEY`
5. Value: (your Groq key from https://console.groq.com — free, no credit card)
6. Click **Add secret**

---

## Step 4 — Enable Workflow Write Permissions

1. Repo **Settings** tab
2. Left sidebar → **Actions** → **General**
3. Scroll to **Workflow permissions** section
4. Select: **Read and write permissions**
5. Click **Save**

---

## Step 5 — Test the Workflow

1. Go to your repo → **Actions** tab
2. Click **agent-cycle** in the left list
3. Click **Run workflow** button (top right) → **Run workflow**
4. Watch it run (takes ~1-2 min)
5. Green checkmark = success. It will auto-run every 30 min from now.

---

## What the workflow does every 30 minutes

- At :17 and :47 every hour (odd minutes — GitHub is least busy then)
- Runs all 132 tests first (if any fail → stops, doesn't run agent)
- Runs `cloud_runner.py` (1 planning pass + up to 3 tasks)
- Commits updated `agent_data.db` and `web/data/status.json` if changed
- Uses 0-2 free Groq API calls per run

## Free tier limits (GitHub Actions)

| | Public repo | Private repo |
|---|---|---|
| Minutes/month | Unlimited | 2,000 |
| At 2 runs/hour × 24h × 30 days = 1,440 runs/month × ~1 min each | ~1,440 min | Uses ~72% of free quota |

Private repo stays safely within the 2,000 min free limit.

---

## Emergency Stop (Cloud)

Commit a file named `KILL_SWITCH` to the repo main branch:
```bash
touch KILL_SWITCH && git add KILL_SWITCH && git commit -m "emergency stop" && git push
```

Remove it to resume:
```bash
git rm KILL_SWITCH && git commit -m "resume agent" && git push
```

---

## Dashboard in the Cloud

`web/` folder is static HTML. Options:
- **Cloudflare Pages** (free, 500 builds/month): connect your repo, build command: none, publish directory: `web`
- Approve/reject buttons won't work from cloud (they need local `serve.py`). Static view only.

---

## Notes

- `config.json` is gitignored — your local config never goes to GitHub
- `agent_data.db` IS committed (it's the state file). In private repo it's safe.
- If scheduled workflow stops after 60 days (GitHub policy): go to Actions tab and re-enable it
- To stop all cloud runs: delete the `.github/workflows/agent.yml` file from the repo
