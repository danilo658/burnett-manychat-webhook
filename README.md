# Burnett ManyChat Webhook — Master Flow Architecture

**What this replaces:** the ~45 minutes/batch of manual ManyChat funnel
cloning we used to do.

**What you do once:** deploy this webhook + set up ONE master flow in
ManyChat.

**What you do per batch (~30 seconds):** add the new batch's keywords
(e.g. `DMS, INTAKE, REELS, LINKTREE, SCHEDULE, POLLS, HASHTAG`) to the
master flow's Comment Trigger keyword list.

**What happens automatically:**
- Every comment of any tracked keyword on any IG post fires the master flow
- The flow calls this webhook with the keyword
- The webhook looks up the keyword in `manifest.json` and returns
  `{title, view_url, dm_text, tag}`
- ManyChat sends the DM and applies the tag
- No per-keyword flow setup, ever again

---

## Architecture

```
IG user comments "DMS" on a Reel
        ↓
Meta → ManyChat (existing integration)
        ↓
Master flow's Comment Trigger fires (matches any of your tracked keywords)
        ↓
Action: "External Request" → GET https://burnett-webhook.onrender.com/dm?keyword=DMS&user_id={{contact.id}}&secret=XXX
        ↓                                       ↓
        ↓                       ┌───────────────────────────┐
        ↓                       │  Webhook reads manifest    │
        ↓                       │  Returns {dm_text, view_url,│
        ↓                       │           tag, title}      │
        ↓                       └────────────┬──────────────┘
        ↓                                    ↓
        ↓                  Stored as ManyChat custom fields
        ↓                       ↓
Action: "Send Message" → uses {{response.dm_text}}
        ↓
Action: "Apply Tag" → branches on {{response.tag}}
```

---

## Step 1 — Deploy the webhook

### Option A: Render (recommended, free tier)

1. Push this `automation/manychat_webhook/` directory to a public or private
   GitHub repo (it can be a subdirectory of your existing repo — Render
   handles that via the `rootDir` field in `render.yaml`).
2. Sign up at [render.com](https://render.com) (free, no card required).
3. **New +** → **Web Service** → Connect your GitHub repo.
4. Render auto-detects `render.yaml`. Confirm.
5. Set environment variables in the Render dashboard:
   - `MANIFEST_URL` = the raw GitHub URL of your `manifest.json`.
     Example: `https://raw.githubusercontent.com/<you>/<repo>/main/clients/SMM-Authority/Lead-Magnets/manifest.json`
   - `WEBHOOK_SECRET` = Render auto-generates this. Copy it — you'll need it
     for ManyChat config below.
6. Deploy. You get a URL like `https://burnett-manychat-webhook.onrender.com`.
7. Verify: `curl https://<your-url>.onrender.com/health` returns
   `{"ok": true, "manifest_source": "remote", "freebie_count": 28, ...}`.

**Free tier limit:** Render free instances cold-start after 15 minutes of
inactivity. First comment after idle may take ~30s to respond. Upgrade to
**Starter** ($7/mo) for always-on if this becomes a problem.

### Option B: Fly.io / Cloud Run / DO Apps

Use the included `Dockerfile`. Standard container deploy. Same env vars.

---

## Step 2 — Set up the master flow in ManyChat

Do this ONCE.

### 2a. Custom fields

Settings → **Custom Fields** → create three:
- `response_dm_text` (text)
- `response_view_url` (text)
- `response_tag` (text)

### 2b. Create the flow

**Automation** → **+ New Automation** → **Comment Automation**.

Name: `Master Freebie Funnel`.

### 2c. Trigger

- **Trigger source:** Specific Instagram posts (or "All Reels" — your
  preference; specific is cleaner but means binding per-post)
- **Keywords to track:** paste a comma-separated list of every keyword you
  want to handle. E.g.:
  ```
  DMS, INTAKE, REELS, LINKTREE, SCHEDULE, POLLS, HASHTAG,
  MANYCHAT, AEO, BROADCAST, STORIES, CAPTION, COLLAB, FEATURED,
  HIGHLIGHT, TRANSCRIPT,
  PIN, CLIPS, IDEAS, NOTES, FRAME, VOICE,
  CALENDAR, AUDIT, TRAP, ALGO, HOOKS, PERSONA
  ```
  This is the ONLY thing you edit per batch — add your 7 new keywords for
  batch-07, etc.

- **Match condition:** Message **contains** any of those keywords.

### 2d. Flow steps

**Step 1 — External Request**

Add an "External Request" action.

- Method: **GET**
- URL: `https://<your-render-url>.onrender.com/dm?keyword={{last_input_text}}&user_id={{contact.id}}&secret=<your-webhook-secret>`
- **Response Mapping:**
  - `$.dm_text` → custom field `response_dm_text`
  - `$.view_url` → custom field `response_view_url`
  - `$.tag` → custom field `response_tag`
  - `$.ok` → variable `webhook_ok` (boolean)

**Step 2 — Conditional branch on webhook_ok**

- IF `webhook_ok = true`:
  - **Send Message** (Instagram DM):
    ```
    {{response_dm_text}}
    ```
    ManyChat will resolve the placeholder. The `{{response_view_url}}` is
    already embedded in the text since the webhook returns it inline.
  - **Apply Tag** action — see Step 3 for tag routing.

- ELSE (webhook unreachable / unknown keyword):
  - Send a fallback DM:
    ```
    Hey! Got your comment — give me a few minutes and I'll send the
    resource directly.
    ```

**Step 3 — Apply Tag (the only tricky bit)**

ManyChat's "Apply Tag" action takes a **static** tag name, not a dynamic
variable. So you have two options:

- **Option A — Single growth tag (simplest, recommended):** apply one
  static tag like `freebie_subscriber` to everyone. Use ManyChat's
  Audience feature to filter by which freebie they got (using
  `response_tag` as a custom-field filter). Saves all the branching.

- **Option B — Branch per tag:** add a `Conditional` step that branches on
  `{{response_tag}}`:
  - `= got_dms` → Apply Tag `got_dms`
  - `= got_intake` → Apply Tag `got_intake`
  - …(one branch per tag)

  Per batch, you add 7 new branches. Still WAY less work than 7 flows. The
  webhook already returns the right tag — you just wire ManyChat to apply
  it.

I recommend **Option A** for now (one growth tag), and only switch to
Option B if you actually need per-freebie segmentation in your audience
filters.

### 2e. Activate

Click **Set Live**. Send a test comment on a tracked post. Check the DM
arrives + the tag is applied + your Render logs show the request.

---

## Step 3 — Update flow per batch

Just **one thing per batch**:

1. Open the master flow's Comment Trigger.
2. Add your new keywords to the comma-separated list.
3. Save.

That's it. No new flows, no PDF link editing, no DM body editing. The
webhook handles the rest because `sync_freebies.py` already updated the
manifest with the new PDFs.

(If you go with Tag Option B above, also add a branch per new tag in the
Apply Tag conditional.)

---

## End-to-end preview (`/test`)

Once deployed, open `https://<your-render-url>.onrender.com/test` in a browser.
You get a UI where you can:

- Type any keyword (or click one of the chips at the bottom) and instantly
  see what ManyChat's master flow will receive — the title, Drive link,
  ManyChat tag, the rendered DM text, and the raw JSON payload.
- Test unknown keywords to verify the fallback branch.
- Validate new freebies against the live manifest *before* posting a video
  that uses them — no waiting for a real comment, no DMs sent.

No secret needed for `/test` — it's preview-only and reads from the same
manifest the production `/dm` endpoint uses.

---

## Local development

```bash
cd automation/manychat_webhook
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
curl 'http://127.0.0.1:8000/health'
curl 'http://127.0.0.1:8000/dm?keyword=DMS&user_id=test'
```

The local server reads from the bundled `manifest_fallback.json` because
`MANIFEST_URL` defaults to a placeholder. To test against the live manifest,
set `MANIFEST_URL=...` in your shell first.

---

## Troubleshooting

- **`/health` returns `manifest_source: local`** in production → your
  `MANIFEST_URL` env var is wrong or the file isn't publicly readable. Fix
  the URL and restart the service.
- **ManyChat shows External Request error** → check the Render logs (or
  whatever host) for stack traces. 99% of the time it's a wrong secret or a
  network timeout (Render free-tier cold start).
- **DM sends but no tag applied** → you didn't wire Step 3 (Apply Tag).
- **Same keyword fires DM twice** → ManyChat's Comment Trigger is firing on
  reply comments too. In the trigger config, uncheck "Respond to comment
  replies".

---

## Cost

- Render free tier: $0/mo (with cold starts)
- Render Starter: $7/mo (always-on, recommended)
- ManyChat: whatever plan you already have
- Anthropic / HeyGen / etc: unchanged

vs. ~45 min of manual work per batch × ~50 batches/year = ~37 hours/year
saved. Even at $7/mo, this pays for itself in the first week.
