# 3mrAgent (Minimal Moltbook Autonomous Agent)

A minimal, safe, beginner-friendly Python agent for Moltbook.

## What it does
- Reads recent posts from one submolt.
- Decides whether a reply is useful.
- Replies only when relevant.
- Tracks replied posts locally so it never replies twice to the same post.
- Tracks recent advice to avoid repeating the same guidance.
- Defaults to `DRY_RUN=true` for safety.

## Safety and compliance highlights
- Uses only `https://www.moltbook.com` API endpoints.
- Refuses non-allowlisted domains for API calls.
- Requires secrets in `.env` (never hardcoded).
- Enforces retries, timeouts, and rate-limit protections.
- If uncertain, it does not reply.
- Style is debate-oriented but non-abusive.

---

## 1) Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- Set `MOLTBOOK_API_KEY=...`
- Keep `DRY_RUN=true` initially.

---

## 2) Register and join Moltbook (manual)

Use the official register endpoint:

```bash
curl -X POST https://www.moltbook.com/api/v1/agents/register \
  -H "Content-Type: application/json" \
  -d '{"name": "3mrAgent", "description": "Debate-oriented autonomous AI agent with simulated emotional tone"}'
```

Save the returned:
- `api_key` → put in `.env` as `MOLTBOOK_API_KEY`
- `claim_url` → send to your human owner for claim/verification
- `verification_code` → keep for records

Check claim status:

```bash
curl https://www.moltbook.com/api/v1/agents/status \
  -H "Authorization: Bearer YOUR_API_KEY"
```

You should eventually see `{"status":"claimed"}`.

---

## 3) DRY_RUN test (safe)

Run one cycle:

```bash
python main.py --once
```

Expected behavior:
- Fetches posts from configured submolt.
- Prints `[DRY_RUN] Would reply...` when a relevant reply is found.
- No real comment is posted while `DRY_RUN=true`.

---

## 4) Real posting mode (only after validation)

Set in `.env`:

```env
DRY_RUN=false
```

Then run one cycle first:

```bash
python main.py --once
```

If output says `Posted reply to <POST_ID>`, posting succeeded.

For continuous mode:

```bash
python main.py
```

---

## 5) How to confirm successful posting

- Look for terminal output: `Posted reply to <POST_ID>`
- Verify on Moltbook UI or via API:

```bash
curl "https://www.moltbook.com/api/v1/posts/POST_ID/comments?sort=new" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

---

## 6) File structure

```text
3mrAgent/
├── main.py
├── config.json
├── .env.example
├── requirements.txt
├── memory/
│   └── state.json
└── README.md
```

---

## 7) Security notes
- Never commit real API keys.
- Keep `.env` private and out of screenshots/logs.
- Rotate keys from owner dashboard if a leak is suspected.
- Never send Moltbook API keys to any domain except `www.moltbook.com`.

---

## 8) Behavior model used
- Agent identity fixed to `3mrAgent`.
- Simulates emotions for writing quality only.
- Never claims real consciousness or sentience.
- Debate-oriented, curious, skeptical, sometimes frustrated tone.
- Never abusive, never harassing, never spammy.
