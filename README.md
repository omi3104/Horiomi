# YT Shorts Agent — automated daily "evergreen facts" Shorts

A hands-off pipeline that every day:

1. **Finds a fresh topic** — top posts from r/todayilearned / r/science /
   r/Damnthatsinteresting, lightly boosted by Google Trends, de-duplicated
   against everything it has already used.
2. **Writes a script** — Pollinations text API (keyless). Falls back to Gemini
   if you add a key, then to a Wikipedia-seeded template so it never stalls.
3. **Sources visuals** — one clip/image per beat: Openverse + Wikimedia Commons
   (keyless real photos), Pollinations (keyless AI images), plus Pexels/Pixabay
   and Gemini images *if* you add those keys.
4. **Records a voiceover** — `edge-tts` Microsoft neural voice (keyless).
5. **Builds captions** — `faster-whisper` word-level timing → animated subtitles.
6. **Edits the video** — `ffmpeg`: 1080×1920, Ken-Burns motion, burned captions,
   voice (+ optional music bed), light cinematic grade.
7. **Uploads to *your* YouTube channel as Private** — via YouTube Data API.
8. **(optional) Copies the file to a Google Drive folder** for phone review.
9. **Repeats daily** on GitHub Actions cron. Your PC can be off.

You review each day's Private upload and hit Publish when you're happy.

---

## Cost: $0

| Step | Service | Key needed? |
|---|---|---|
| Topics | Reddit JSON, Google Trends RSS | no |
| Script | Pollinations text (`text.pollinations.ai`) | no |
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

Optional keys (`GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`) only raise
quality; the pipeline is fully functional without them.

YouTube's free quota is 10,000 units/day; one upload costs ~1,600, so one
video/day sits well inside it.

---

## Architecture

```
src/
  config.py        knobs + credential loading (nothing topic-specific)
  trends.py        pick one fresh evergreen topic
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

Optional secrets: `GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`,
`DRIVE_FOLDER_ID` (the id in a Drive folder URL — the pipeline drops the day's
`.mp4` + `.json` there for review).

Optional **Variables** (same screen, "Variables" tab) to tune without code:
`VOICE`, `GEO`, `YT_CATEGORY_ID`, `PRIVACY`, `WHISPER_MODEL`.

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

## Adding a new topic source or media provider

- **Topic source:** add a function in `src/trends.py` that returns
  `[{topic, score, source, url}]` and include it in `pick_topic()`.
- **Media provider:** add a `_provider(query, stem) -> path | None` in
  `src/media.py` and slot it into the chain in `fetch_for_beats()`.

No other file needs to change.

---

## "Can this be an APK / an iPhone PWA?"

The **worker cannot run on a phone** — it needs ffmpeg encoding, a ~500 MB
Whisper model and sustained background execution. iOS suspends PWAs in the
background and cannot run ffmpeg/Python at all; an Android APK can technically
bundle ffmpeg but background daily automation is throttled and battery-heavy.
Keeping the worker in the cloud (this repo) is also *why* it runs when your
phone is off.

What **can** be an app is a thin **control panel**: view today's Private video,
see run status, tap "Run now", change the topic. That is a good fit for a
**PWA** (one build, installs on iPhone *and* Android via "Add to Home Screen",
no app store) hosted free on GitHub Pages, talking to the GitHub REST API with
a fine-grained token. Not built yet — ask if you want it as phase 2.

---

## Notes / caveats

- **Monetisation:** fully auto-generated faceless Shorts can hit YouTube's
  "reused / inauthentic content" rules for the Partner Program. Posting is
  unaffected; long-term revenue may be. The evergreen-facts angle + real
  photos + original scripts is the lower-risk approach, not a guarantee.
- **Accuracy:** the script writer is instructed to correct myths and never
  invent numbers, but spot-check before publishing — that is what Private mode
  is for.
- **Media licences:** Openverse/Wikimedia/Pexels/Pixabay/Pollinations are used
  under their free/commercial terms. If you add a music file, use a cleared
  track (see `assets/music/PUT_MUSIC_HERE.txt`).
- **Token in "Testing" mode:** Google refresh tokens for apps left in Testing
  used to expire after 7 days. For a single Test user this no longer applies in
  practice, but if uploads ever start failing with `invalid_grant`, re-run
  `node scripts/get-token.mjs` and update the secret.
```
