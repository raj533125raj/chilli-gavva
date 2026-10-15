# Groq free API key setup (no card needed)

Why: the engine uses an LLM for its "AI" parts. Without a key it runs in fallback mode (templates only).
Models used by the code: `openai/gpt-oss-20b` (small) and `openai/gpt-oss-120b` (large). Run the health check to see which models your key can use. Free tier limits are published by Groq at
console.groq.com (research note in STATUS.md says about 30 requests/min, 14,400/day; verify on the console).

## Steps (done by YOU, not by the agent)
1. Open console.groq.com and sign up for a free account.
2. Create an API key (API Keys page). Copy it once.
3. Never paste the key into chat, files, or git. Keep it only in your environment.

## Set it for one terminal session (safest on a Mac)
```
read -s GROQ_API_KEY && export GROQ_API_KEY
```
Paste the key when prompted (it will not show on screen). It disappears when the terminal closes.

## Check it works
```
cd ~/Documents/Chilli_Gavva_Agent
python3 -c "import engine; print(engine.AutonomousResearchEngine.generate_with_groq('Say OK in one word.'))"
```
- Prints a word such as "OK": key works.
- Prints `None`: key missing, wrong, or limit hit. Check the variable with `echo ${#GROQ_API_KEY}` (should print a number, not 0).

## Rules
- `config.json` is git-ignored, but still prefer the environment variable over writing the key into it.
- For 24/7 cloud runs later, store the key as a GitHub Actions secret (never in the repo).
- If the key leaks, revoke it on console.groq.com and create a new one.
