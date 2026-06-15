"""
ManyChat → freebie webhook.

ManyChat's master flow calls this on every Comment Trigger fire:

    GET /dm?keyword=DMS&user_id=12345&secret=<shared>

We look up the keyword in the freebie manifest and return:

    {
      "ok": true,
      "keyword": "DMS",
      "title": "Instagram Saved Replies Intake Kit for US Attorneys",
      "dm_text": "Hey! Here's the Instagram Saved Replies Intake Kit ...",
      "view_url": "https://drive.google.com/file/d/.../view?usp=sharing",
      "tag": "got_dms"
    }

ManyChat then sends the DM (Send Message → uses {{response.dm_text}} +
{{response.view_url}}) and applies the tag (Apply Tag → branches on
{{response.tag}}).

No per-keyword setup — adding a new freebie means editing the manifest,
not touching the ManyChat UI. Adding a new keyword to the master flow's
Comment Trigger keyword list is still a 30-second UI task per batch.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import html as html_lib

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

# ---------------------------------------------------------------------------
# Config (via env vars — set on Render / Fly / wherever)
# ---------------------------------------------------------------------------
MANIFEST_URL = os.environ.get(
    "MANIFEST_URL",
    # Default: read from the public GitHub raw URL of the freebie manifest.
    # Override per deployment.
    "https://raw.githubusercontent.com/danilo-burnett/burnett-marketing/main/clients/SMM-Authority/Lead-Magnets/manifest.json",
)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
MANIFEST_CACHE_TTL = int(os.environ.get("MANIFEST_CACHE_TTL", "60"))  # seconds

# Optional fallback bundled with the deployment if MANIFEST_URL is unreachable
LOCAL_MANIFEST_PATH = Path(__file__).parent / "manifest_fallback.json"


# ---------------------------------------------------------------------------
# DM body template — single source of truth for the message we send
# ---------------------------------------------------------------------------
def render_dm_body(title: str, view_url: str) -> str:
    """The exact text the user sees in their DM. Edit here, not in ManyChat."""
    return (
        f"Hey! Here's the {title} I promised:\n\n"
        f"👉 {view_url}\n\n"
        f"It's a free PDF — open it any time, no email required. Save it to "
        f"your phone so you can reference it when you're setting up the workflow.\n\n"
        f"One thing: this only works if you actually open it and try the "
        f"workflow on one of your reels this week. The lawyers who close the "
        f"gap between \"I'll watch this later\" and \"I tried it Monday "
        f"morning\" are the ones who see results.\n\n"
        f"Let me know if you hit anything weird while setting it up — reply "
        f"with a question and I'll point you to the right step.\n\n— Danilo"
    )


# ---------------------------------------------------------------------------
# Manifest cache (TTL-based, in-process — fine for single-instance webhook)
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0, "source": None}


def _load_manifest() -> tuple[dict, str]:
    """Return (manifest_dict, source) where source is 'remote' or 'local'."""
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["fetched_at"]) < MANIFEST_CACHE_TTL:
        return _cache["data"], _cache["source"]

    # Try remote first
    try:
        resp = httpx.get(MANIFEST_URL, timeout=4.0)
        resp.raise_for_status()
        data = resp.json()
        _cache.update(data=data, fetched_at=now, source="remote")
        return data, "remote"
    except Exception as exc:
        print(f"[manifest] remote fetch failed: {exc!r}", file=sys.stderr)

    # Fall back to bundled
    if LOCAL_MANIFEST_PATH.exists():
        data = json.loads(LOCAL_MANIFEST_PATH.read_text())
        _cache.update(data=data, fetched_at=now, source="local")
        return data, "local"

    raise HTTPException(status_code=503, detail="Manifest unreachable and no local fallback")


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Burnett ManyChat Webhook",
    version="1.0.0",
    docs_url="/_docs",  # under a private path
)


@app.get("/health")
def health() -> dict:
    """Render's health-check endpoint pings this."""
    try:
        manifest, source = _load_manifest()
        return {
            "ok": True,
            "manifest_source": source,
            "freebie_count": len(manifest),
            "keywords": sorted(manifest.keys()),
        }
    except HTTPException as exc:
        return {"ok": False, "error": exc.detail}


def _extract_keyword(text: str, known_keywords: set[str]) -> str | None:
    """Pick the first known keyword that appears in the comment text.

    Tokenizes on non-alphanumeric chars so we match "DMS please" / "Dms!"
    / "manychat how" alike. Case-insensitive. Returns the matched
    keyword in UPPERCASE, or None if no match.
    """
    if not text:
        return None
    import re
    # Split on anything that isn't a letter/digit/underscore
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*", text)
    for tok in tokens:
        up = tok.upper()
        if up in known_keywords:
            return up
    return None


@app.get("/dm")
def dm(
    keyword: str = Query("", description="Direct keyword (legacy path) — OR pass `text` to extract from a comment"),
    text: str = Query("", description="Raw comment text — webhook extracts any known keyword from it"),
    user_id: str = Query("", description="ManyChat subscriber id (informational)"),
    secret: str = Query("", description="Shared secret to gate the endpoint"),
) -> JSONResponse:
    """ManyChat's master flow calls this on every Comment Trigger fire.

    Accepts EITHER:
      - `keyword=DMS` — exact keyword (used when ManyChat's trigger already
        filtered by Specific Keywords).
      - `text=DMS%20please` — raw comment text; we extract the first known
        keyword from it. Use this when the ManyChat trigger is "Any comment"
        and the 8-keyword limit in Specific Keywords forced us to do
        extraction here instead.
    """

    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        # Don't leak whether the keyword exists when auth fails
        raise HTTPException(status_code=403, detail="Forbidden")

    manifest, source = _load_manifest()

    # Direct keyword takes precedence; otherwise try to extract from `text`
    kw: str | None = None
    if keyword:
        kw = keyword.strip().upper()
    elif text:
        kw = _extract_keyword(text, set(manifest.keys()))

    if not kw:
        # No keyword could be resolved — master flow's conditional should END
        # FLOW here (no DM, no tag). This is the common case when the
        # "Any comment" trigger fires on an unrelated comment.
        return JSONResponse({
            "ok": False,
            "keyword": "",
            "reason": "no_keyword_in_text" if text else "missing_input",
            "manifest_source": source,
        })

    entry = manifest.get(kw)

    if not entry:
        return JSONResponse({
            "ok": False,
            "keyword": kw,
            "reason": "unknown_keyword",
            "manifest_source": source,
        })

    title = entry.get("title") or "the playbook"
    view_url = entry.get("view_url") or ""
    tag = f"got_{kw.lower()}"

    return JSONResponse({
        "ok": True,
        "keyword": kw,
        "title": title,
        "view_url": view_url,
        "tag": tag,
        "dm_text": render_dm_body(title, view_url),
        "manifest_source": source,
        "user_id": user_id,
    })


@app.get("/")
def root() -> dict:
    return {
        "service": "burnett-manychat-webhook",
        "endpoints": [
            "/dm?keyword=X&user_id=Y&secret=Z",
            "/health",
            "/test",
        ],
    }


# ---------------------------------------------------------------------------
# /test — preview-mode UI for end-to-end validation WITHOUT sending DMs.
# Useful for: verifying titles/links pre-deploy, debugging keyword routing,
# letting a non-engineer eyeball the rendered DM body before going live.
# ---------------------------------------------------------------------------
TEST_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Burnett ManyChat Webhook — Preview</title>
  <style>
    :root {
      --bg: #0E0E0E; --fg: #ECECEC; --muted: #8A8A8A; --accent: #FF6B1A;
      --card: #1A1A1A; --ok: #2BB673; --err: #E25555; --border: #2A2A2A;
      --code: #131313;
    }
    * { box-sizing: border-box; }
    html, body { background: var(--bg); color: var(--fg); margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Helvetica, Arial, sans-serif; }
    .wrap { max-width: 760px; margin: 0 auto; padding: 40px 24px; }
    h1 { font-size: 22px; font-weight: 700; margin: 0 0 6px; }
    .sub { color: var(--muted); font-size: 14px; margin-bottom: 28px; }
    .row { display: flex; gap: 10px; margin-bottom: 28px; }
    input[type=text] { flex: 1; padding: 12px 14px; background: var(--card);
      border: 1px solid var(--border); border-radius: 10px; color: var(--fg);
      font-size: 15px; font-family: inherit; }
    input[type=text]:focus { outline: none; border-color: var(--accent); }
    button { padding: 12px 22px; background: var(--accent); color: #000;
      border: none; border-radius: 10px; font-weight: 700; font-size: 15px;
      cursor: pointer; }
    button:hover { filter: brightness(1.1); }
    .card { background: var(--card); border: 1px solid var(--border);
      border-radius: 14px; padding: 20px 22px; margin-bottom: 16px; }
    .status { display: flex; align-items: center; gap: 8px; font-size: 14px;
      color: var(--muted); margin-bottom: 6px; }
    .badge { display: inline-block; padding: 2px 10px; border-radius: 6px;
      font-size: 12px; font-weight: 700; letter-spacing: 0.5px; }
    .badge.ok { background: rgba(43,182,115,0.15); color: var(--ok); }
    .badge.err { background: rgba(226,85,85,0.15); color: var(--err); }
    .label { color: var(--muted); font-size: 12px; text-transform: uppercase;
      letter-spacing: 1px; margin: 14px 0 4px; }
    .val { color: var(--fg); font-size: 14px; word-break: break-all; }
    .val a { color: var(--accent); text-decoration: none; }
    .dm-preview { background: #fff; color: #111; border-radius: 16px;
      padding: 18px 20px; font-size: 14px; line-height: 1.55;
      white-space: pre-wrap; }
    pre { background: var(--code); border: 1px solid var(--border);
      border-radius: 10px; padding: 14px 16px; overflow-x: auto;
      font-size: 12px; line-height: 1.5; margin: 0; }
    .empty { color: var(--muted); font-size: 14px; text-align: center; padding: 40px 0; }
    .keywords { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 16px; }
    .kw { background: var(--card); border: 1px solid var(--border);
      border-radius: 6px; padding: 4px 9px; font-size: 12px;
      color: var(--muted); cursor: pointer; }
    .kw:hover { color: var(--accent); border-color: var(--accent); }
    .meta { font-size: 11px; color: var(--muted); margin-top: 16px;
      padding-top: 12px; border-top: 1px solid var(--border); }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>ManyChat Webhook — Preview</h1>
    <p class="sub">
      Simulates what ManyChat will receive when someone comments a keyword.
      <strong>No DM is sent.</strong> Use this to verify titles, links, and DM
      copy before pushing the master flow live.
    </p>

    <form class="row" method="get" action="/test">
      <input type="text" name="keyword" placeholder="Type a keyword (e.g. DMS)"
             value="__KEYWORD__" autofocus />
      <button type="submit">Preview</button>
    </form>

    __RESULT__

    <div class="meta">
      Available keywords (click to test): <div class="keywords">__KW_CHIPS__</div>
    </div>
  </div>
  <script>
    document.querySelectorAll('.kw').forEach(el => {
      el.addEventListener('click', () => {
        document.querySelector('input[name=keyword]').value = el.textContent.trim();
        document.querySelector('form').submit();
      });
    });
  </script>
</body>
</html>
"""


@app.get("/test", response_class=HTMLResponse)
def test_page(keyword: str = Query("", description="Keyword to preview")) -> str:
    """Browser-based end-to-end check. Does NOT send DMs."""
    try:
        manifest, source = _load_manifest()
    except HTTPException as exc:
        return HTMLResponse(
            f"<pre>Manifest unreachable: {html_lib.escape(exc.detail)}</pre>",
            status_code=503,
        )

    kw_chips = "".join(
        f'<span class="kw">{html_lib.escape(k)}</span>' for k in sorted(manifest.keys())
    )

    result = ""
    if keyword:
        kw = keyword.strip().upper()
        entry = manifest.get(kw)
        if entry:
            title = entry.get("title") or "(missing title)"
            view_url = entry.get("view_url") or ""
            tag = f"got_{kw.lower()}"
            dm_text = render_dm_body(title, view_url)
            preview_payload = {
                "ok": True,
                "keyword": kw,
                "title": title,
                "view_url": view_url,
                "tag": tag,
                "dm_text": dm_text,
                "manifest_source": source,
            }
            result = f"""
            <div class="card">
              <div class="status"><span class="badge ok">OK</span>
                ManyChat would receive this payload</div>
              <div class="label">Title</div>
              <div class="val">{html_lib.escape(title)}</div>
              <div class="label">Drive link</div>
              <div class="val"><a href="{html_lib.escape(view_url)}" target="_blank">{html_lib.escape(view_url)}</a></div>
              <div class="label">ManyChat tag</div>
              <div class="val">{html_lib.escape(tag)}</div>
            </div>
            <div class="card">
              <div class="status">Rendered DM (preview only — not sent)</div>
              <div class="dm-preview">{html_lib.escape(dm_text)}</div>
            </div>
            <div class="card">
              <div class="status">Raw JSON the master flow's External Request will receive</div>
              <pre>{html_lib.escape(json.dumps(preview_payload, indent=2, ensure_ascii=False))}</pre>
            </div>
            """
        else:
            result = f"""
            <div class="card">
              <div class="status"><span class="badge err">UNKNOWN</span>
                Keyword "{html_lib.escape(kw)}" not in manifest</div>
              <div class="val">ManyChat's master flow would fall through to the
                "we'll get back to you" fallback branch.</div>
            </div>
            """
    else:
        result = '<div class="empty">Type a keyword above or click a chip below to preview.</div>'

    return (
        TEST_PAGE
        .replace("__KEYWORD__", html_lib.escape(keyword))
        .replace("__RESULT__", result)
        .replace("__KW_CHIPS__", kw_chips)
    )
