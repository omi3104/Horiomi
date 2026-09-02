# YT Shorts Agent — automated daily HISTORY Shorts

Niche: **ancient & medieval history and historical geopolitics** — Greeks,
Romans, Persians, Byzantines, the Islamic world, Normans, Mongols, Ottomans,
the British Empire, the World Wars and Cold War *as history*. Surprising,
lesser-known angles on famous subjects. **Not** current partisan politics.

A hands-off pipeline that every day:

1. **Finds a high-demand topic** — Wikipedia's most-viewed articles (yesterday),
   filtered to history via their categories; Wikipedia "On this day"; real
   YouTube search-suggest phrases for era seed terms; and a curated bank of
   dramatic hooks. Ranked by actual audience demand, de-duplicated against
   everything already used. The era mix is whatever people are searching that
   week.
2. **Writes a script** — Groq if you add a key (fast, reliable free tier), then
   Gemini, then the keyless Pollinations text API, then a Wikipedia-seeded
   template so it never stalls. Dramatic "storyteller" tone, real dates and
   names, corrects the popular myth as the payoff. Narration sized to land the
   finished short at **50–80 seconds**. Title + description + tags are seeded
   with the YouTube-suggest phrases for SEO.
3. **Sources visuals** — one clip/image per beat. Real **stock video** first
   (Pexels / Pixabay / Coverr with a free key, plus keyless Wikimedia Commons
   video), then real photos (Openverse, Wikimedia), then AI images
   (Gemini if keyed, else keyless Pollinations). Add one stock-video key and
   most beats become motion footage instead of stills.
4. **Records a voiceover** — `edge-tts` Microsoft neural voice (keyless). The
   speaking rate is auto-tuned so the runtime stays inside the 50–80s window.
5. **Builds captions** — `faster-whisper` word-level timing → animated subtitles.
6. **Edits the video** — `ffmpeg`: 1080×1920, Ken-Burns motion, burned captions,
   voice (+ optional music bed), light cinematic grade. The final file is
   clamped to the 50–80s window (tail held / trimmed as needed).
7. **Uploads to *your* YouTube channel as Private** — via YouTube Data API.
8. **(optional) Copies the file to a Google Drive folder** for phone review.
9. **Repeats daily** on GitHub Actions cron. Your PC can be off.

You review each day's Private upload and hit Publish when you're happy.

---

## Cost: $0

| Step | Service | Key needed? |
|---|---|---|
| Topics | Wikipedia pageviews + "On this day" + YouTube search-suggest | no |
| Script | Groq / Gemini if keyed, else Pollinations text, else template | free key = much better |
| Stock video | Wikimedia Commons (keyless); Pexels / Pixabay / Coverr | free key = much better |
| Real photos | Openverse API, Wikimedia Commons API | no |
| AI images | Pollinations image (`image.pollinations.ai`) | no |
| Voiceover | `edge-tts` | no |
| Captions | `faster-whisper` (runs on the free CI runner) | no |
| Editing | `ffmpeg` | no |
| Daily run | GitHub Actions (free minutes, public or private repo) | no |
| **Upload to your channel** | **YouTube Data API v3** | **yes — one-time OAuth, free** |

The **only** credential the pipeline needs is OAuth for *your own* YouTube
channel. Google requires the channel owner to authorise the app once — nobody
can bypass that. After the one-time step below it runs untouched.

Optional keys (`GROQ_API_KEY`, `GEMINI_API_KEY`, `PEXELS_API_KEY`,
`PIXABAY_API_KEY`, `COVERR_API_KEY`) only raise quality; the pipeline is fully
functional without them. In practice you want a **script key** (`GROQ_API_KEY`)
and **at least one stock-video key** — see
[Stock media API keys](#stock-media-api-keys-recommended) — or every beat is a
still image.

YouTube's free quota is 10,000 units/day; one upload costs ~1,600, so one
video/day sits well inside it.

---

## Architecture

```
src/
  config.py        knobs + credential loading (nothing topic-specific)
  trends.py        pick one high-demand history topic (Wikipedia views, YouTube suggest)
  script_gen.py    topic -> {title, hook, beats[], narration, description, tags}
  media.py         one clip/image per beat, layered free sources
  tts.py           narration -> voice.mp3   (edge-tts)
  captions.py      voice.mp3 -> captions.ass (faster-whisper word timings)
  video.py         segments + captions + audio -> out/short_YYYYMMDD.mp4  (ffmpeg)
  youtube_upload.py  upload as Private (YouTube Data API v3)
  drive_upload.py    optional review copy to Google Drive
  state.py         data/used_topics.json  (committed back each run -> no repeats)
  pipeline.py      orchestrates all of the above
scripts/
  get-token.mjs    one-time Google OAuth -> refresh token   (pure Node, no deps)
.github/workflows/daily.yml   cron + manual trigger
```

Layers are independent — swapping the script writer or a media source touches
only that file.

---

## One-time setup (~10–15 min, all free)

### 1. Put the code in a private GitHub repo

Create an **empty** private repo on github.com (no README), then from this
folder:

```bash
git init
git add .
git commit -m "initial: yt shorts agent"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### 2. Create the Google OAuth client (free)

1. https://console.cloud.google.com/ → create a project (any name).
2. **APIs & Services → Library** → enable **YouTube Data API v3** and
   **Google Drive API**.
3. **APIs & Services → OAuth consent screen** → User type **External** →
   fill the required name/email → **Add users** → add your own Google address
   as a **Test user** (keeps it in "Testing" mode, which is fine forever for
   personal use — refresh tokens stay valid).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   application type **Desktop app** → create → copy the **Client ID** and
   **Client secret**.

### 3. Mint your refresh token

You already have Node. From this folder:

```bash
node scripts/get-token.mjs
```

It opens Google's consent screen, you approve, and it prints three values.

### 4. Add repo secrets

GitHub repo → **Settings → Secrets and variables → Actions → New repository
secret**. Add:

| Secret | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | from step 2 |
| `GOOGLE_CLIENT_SECRET` | from step 2 |
| `GOOGLE_REFRESH_TOKEN` | from step 3 |

Optional secrets: `GROQ_API_KEY`, `GEMINI_API_KEY`, `PEXELS_API_KEY`,
`PIXABAY_API_KEY`, `COVERR_API_KEY`, `DRIVE_FOLDER_ID` (the id in a Drive folder
URL — the pipeline drops the day's `.mp4` + `.json` there for review). See
[Stock media API keys](#stock-media-api-keys-recommended) for how to get the
three stock keys.

> **Drive uploads are off by default.** Google refuses to grant
> `youtube.upload` and `drive.file` in the same unverified-app consent
> request ("scopes that cannot be requested together"), so `get-token.mjs`
> only requests `youtube.upload`. If you still want the Drive review copy,
> mint a *second* token with only `drive.file` scope, store it as a separate
> secret, and point `drive_upload.py` at those credentials instead — the
> daily video is already reviewable via YouTube Studio's Private tab and the
> workflow's build artifact, so this is a nice-to-have, not required.

Optional **Variables** (same screen, "Variables" tab) to tune without code:
`VOICE`, `GEO`, `YT_CATEGORY_ID`, `PRIVACY`, `WHISPER_MODEL`, `PREFER_VIDEO`
(`true`/`false`), `TARGET_SECONDS_MIN` / `TARGET_SECONDS_MAX` (default 50 / 80),
`TARGET_SECONDS`, `TTS_RATE`.

### 5. Test it, then leave it alone

Repo → **Actions** → enable workflows → **daily-short** → **Run workflow** →
tick **dry_run** → Run. Download the artifact and check the video. Then run it
once without dry_run to confirm the upload lands in YouTube Studio as Private.
After that the daily 14:00 UTC cron handles everything.

---

## Running locally (optional)

Needs Python 3.11+ and ffmpeg on PATH. On Windows:

```bash
winget install Python.Python.3.11
winget install Gyan.FFmpeg
```

Then:

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # fill in the GOOGLE_* values
set DRY_RUN=1                 # build without uploading
python -m src.pipeline
```

Output lands in `out/`.

---

## Stock media API keys (recommended)

Without a stock key every beat is a still image (keyless Wikimedia Commons
video is the only motion source and it rarely matches). Add **one or more** of
these — all free, ~2 minutes each — as repo **secrets** (or `.env` lines
locally). The pipeline tries them in the order below and uses the first clip it
can download; stills are only used when no video source has a match.

### Pexels — best single key

1. Sign in at <https://www.pexels.com/> (free).
2. Go to <https://www.pexels.com/api/> → **Get Started** / **Your API Key**.
3. Fill the short form (name the project "YT Shorts Agent", pick "personal").
4. Copy the key → repo secret **`PEXELS_API_KEY`**.
   Free tier: 200 requests/hour, 20 000/month — far more than one video/day.

### Pixabay — extra coverage

1. Create a free account at <https://pixabay.com/>.
2. Open <https://pixabay.com/api/docs/> while logged in — your key is shown
   inline in the "Search Images" example (the `key=` value).
3. Copy it → repo secret **`PIXABAY_API_KEY`**. Free tier: 100 req/min.

### Coverr — cinematic B-roll

1. Sign up at <https://coverr.co/> (free).
2. Open the **Dashboard → Developers / API keys** (also reachable at
   <https://api.coverr.co>) and create a key.
3. Copy it → repo secret **`COVERR_API_KEY`**.

### Groq (recommended) — the script writer

Fast, dependable free tier; tried first for the script.

1. Sign in at <https://console.groq.com/> (free, Google/GitHub login).
2. <https://console.groq.com/keys> → **Create API Key** → copy (`gsk_…`).
3. Repo secret **`GROQ_API_KEY`**.

One ~1k-token call per day sits far inside the free limits. Uses
`llama-3.3-70b-versatile` and self-discovers a replacement if that model is ever
retired.

### Gemini (optional) — script fallback + AI images

<https://aistudio.google.com/apikey> → **Create API key** → repo secret
**`GEMINI_API_KEY`**. Used if Groq is unset or fails, and adds an AI-image
fallback above Pollinations.

> Turn motion footage off entirely with the `PREFER_VIDEO=false` variable —
> then the pipeline only ever uses stills.

---

## Adding a new topic source or media provider

- **Topic source:** add a function in `src/trends.py` that returns
  `[{topic, score, source, url}]` and add it to the `pool` in `pick_topic()`.
  Higher `score` = more audience demand; it is what the ranker weights.
- **Media provider:** add a `_provider(query, stem) -> path | None` in
  `src/media.py` and slot it into `_VIDEO_PROVIDERS` or `_IMAGE_PROVIDERS`.

No other file needs to change.

---

## Reviewing from your iPhone (the PWA)

`docs/` is a tiny installable web app — one HTML file, no build step — served
free from **GitHub Pages**. It is a *control panel*, not the worker: the video
is still built in the cloud so it runs with your phone off.

**Turn Pages on once:** repo → **Settings → Pages → Build and deployment →
Source → "GitHub Actions"**. (The workflow token cannot enable Pages by itself
— a GitHub restriction.) Pages also needs the repo **public**, or GitHub
Pro/Team on a private repo.

Then the `deploy-pwa` workflow publishes `docs/` on every push under `docs/` and
on manual dispatch (**Actions → deploy-pwa → Run workflow**). The site URL shows
in the run summary and at **Settings → Pages** — usually
`https://<you>.github.io/<repo>/`.

**Install on iPhone:** open that URL in **Safari** → Share → **Add to Home
Screen**. It opens full-screen like a native app.

**First-run setup (in the app):** tap ⚙︎ and enter:

- **Repository** — `owner/repo`
- **GitHub token** — a *fine-grained* PAT
  (<https://github.com/settings/personal-access-tokens/new>) scoped to **only
  this repo**, with **Actions: Read and write** and **Contents: Read-only**.
  It is stored only in your browser's `localStorage` and is sent only to
  `api.github.com`.

**What you get:**

- **Latest workflow run** status (green / red / running) with a link to the log.
- **Today's short — review**: thumbnail, title, topic + source link, a
  length pill (e.g. `63s / 50–80s`, red if out of range), the full generated
  description, tag chips, and the beat-by-beat script. Buttons deep-link to
  **Watch in the YouTube app** (the Private upload plays there while you're
  signed in) and **Publish in Studio**.
- **Run the pipeline**: *Build only* (dry run) or *Build + upload* (Private),
  which dispatch the GitHub Action.
- **Recent runs** list.

The review panel reads `data/last_run.json`, which the daily workflow commits
after every build (including dry runs).

> The **worker itself cannot run on a phone** — it needs ffmpeg, a ~500 MB
> Whisper model and sustained background execution that iOS does not allow.

---

## Notes / caveats

- **Monetisation:** fully auto-generated faceless Shorts can hit YouTube's
  "reused / inauthentic content" rules for the Partner Program. Posting is
  unaffected; long-term revenue may be. Original history scripts + real stock
  footage + narration is the lower-risk approach, not a guarantee. The niche
  filter deliberately blocks current partisan politics, which YouTube limits
  hardest.
- **Accuracy:** the script writer is instructed to correct myths and never
  invent numbers, but spot-check before publishing — that is what Private mode
  is for.
- **Media licences:** Openverse / Wikimedia / Pexels / Pixabay / Coverr /
  Pollinations are used under their free/commercial terms (attribution not
  required by any of them for this use, but check if you redistribute). If you
  add a music file, use a cleared track (see `assets/music/PUT_MUSIC_HERE.txt`).
- **Length:** every short is forced to 50–80s. YouTube raised the Shorts
  maximum to 3 minutes in 2024, so these still count as Shorts. Tune with the
  `TARGET_SECONDS_MIN` / `TARGET_SECONDS_MAX` variables.
- **Token in "Testing" mode:** Google refresh tokens for apps left in Testing
  used to expire after 7 days. For a single Test user this no longer applies in
  practice, but if uploads ever start failing with `invalid_grant`, re-run
  `node scripts/get-token.mjs` and update the secret.

---

## License

MIT — see [`LICENSE`](LICENSE).
