# AI Storyboarding Lab

**Turn one sentence into a cinematic storyboard.** Give it a story idea, and it writes the narrative beats and generates a matching panel image for each one.

No signup, no API key required — the default path runs entirely on free services.

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/flask-3.0.3-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-2f9aa2.svg)](LICENSE)

---

## Why it exists

Storyboarding is normally the slow part of pre-production: you have the idea, but turning it into a shot-by-shot sequence takes hours of sketching. This app collapses that into one request. It writes the panels, keeps character and setting continuity across them, and renders each frame.

It is a single Flask service with a vanilla frontend — no build step, no node_modules, no framework churn. Clone it, `pip install`, run it.

## Features

- **One prompt in, a full storyboard out** — title, scene description, dialogue, and a cinematic image prompt per panel.
- **Continuity-aware prompting** — the model is instructed to hold character appearance, wardrobe, and setting constant across panels.
- **Cinematic image prompts** — each prompt is built from a fixed structure: subject, action, environment, camera framing, lighting, mood, art style.
- **Two-tier image generation** — Gemini when you have a key, automatic fallback to Pollinations.ai when you don't (or when Gemini is rate-limited).
- **Retry with exponential backoff** — transient provider failures are retried with jittered delays, and a permanent quota failure trips the fallback instead of burning through retries.
- **Image proxy** — a server-side `/image-proxy` endpoint fetches remote images with browser-like headers, so panel images load reliably in the browser.
- **Graceful degradation** — malformed model JSON is retried with stricter instructions, and missing fields fall back to sane defaults rather than crashing the request.
- **Configurable panel count** — 1 to 6 panels, clamped and validated server-side.

## How it works

```
Browser
  │  POST /generate  { "prompt": "..." }
  ▼
Flask (app.py)
  │
  ▼
StoryboardService (services.py)
  ├─ 1. Build a strict-JSON storyboard instruction
  ├─ 2. Vexa AI  ──────────────►  title + panels[] (scene, dialogue, image_prompt)
  ├─ 3. Normalize + validate the panel payload
  └─ 4. Per panel, generate an image:
         ├─ Gemini 2.5 Flash Image  (if GEMINI_API_KEY is set)
         └─ Pollinations.ai         (fallback, or default when no key)
  ▼
JSON response  ──►  Browser renders the panel grid
                    (images streamed through /image-proxy)
```

Text generation and image generation are deliberately separate calls. That keeps the narrative coherent (one model sees the whole arc) while letting the image model work panel by panel.

## Quick start

**Requirements:** Python 3.10 or newer.

```bash
git clone https://github.com/abhrajyoti-01/ai-storyboarding.git
cd ai-storyboarding

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
```

Run it:

```bash
flask --app app run
```

Open <http://localhost:5000>, type a story idea, and hit **Generate storyboard**. Expect roughly 20–60 seconds depending on the image provider.

> The app works with an empty `.env`. Adding `GEMINI_API_KEY` switches image generation to Gemini first, with Pollinations as the safety net.

## Configuration

All settings are read from environment variables (or `.env`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | _(empty)_ | Enables Gemini image generation. `GAMINI_API_KEY` is accepted as a legacy alias. |
| `GEMINI_IMAGE_MODEL` | `gemini-2.5-flash-image` | Gemini model. Any `imagen*` model is routed to the `:predict` endpoint automatically. |
| `STORYBOARD_PANEL_COUNT` | `4` | Panels per storyboard, clamped to 1–6. |
| `FLASK_DEBUG` | `false` | Flask debug mode. Also controls whether raw exception text is returned to the client. |
| `IMAGE_PROXY_RETRIES` | `3` | Attempts `/image-proxy` makes before returning an error. |

## API

### `POST /generate`

```json
{ "prompt": "A young engineer discovers a hidden map in an old train station" }
```

Response `200`:

```json
{
  "title": "The Hidden Map",
  "panel_count": 2,
  "panels": [
    {
      "panel_number": 1,
      "scene_description": "Dust motes drift through a shaft of light as a young engineer kneels beside a rusted locker, a folded map sliding free from a crack in the metal.",
      "dialogue": "This hasn't been opened in thirty years.",
      "image_prompt": "Young engineer in a worn canvas jacket, kneeling beside a rusted locker, abandoned train station interior, medium close-up shot, dramatic side lighting, tense atmosphere, cinematic concept art, cool blue tones",
      "image_url": "https://image.pollinations.ai/prompt/...",
      "image_data_url": "data:image/png;base64,...",
      "image_provider": "gemini",
      "image_status": "ready"
    }
  ]
}
```

| Field | Notes |
| --- | --- |
| `image_provider` | `gemini` or `pollinations` — which provider produced this panel. |
| `image_data_url` | Base64 data URL. Populated when Gemini generated the image. |
| `image_url` | Remote URL. Populated when Pollinations generated the image. |
| `image_status` | `ready` on success. |

**Errors**

| Status | Meaning |
| --- | --- |
| `400` | Empty prompt or panel count out of range. |
| `502` | An upstream AI service failed after retries. |
| `500` | Unexpected server error. Raw detail only when `FLASK_DEBUG=true`. |

### `GET /image-proxy?url=<encoded>`

Server-side fetch for Pollinations images. Only `https://image.pollinations.ai/` URLs are accepted — this is an allowlist, not an open proxy. Returns the image bytes with a 24-hour cache header, or `429` / `502` when the provider is unavailable.

### `GET /`

Renders the single-page UI.

## Usage examples

**curl**

```bash
curl -X POST http://localhost:5000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "A detective follows clues through a foggy Victorian London street"}'
```

**Python**

```python
import requests

response = requests.post(
    "http://localhost:5000/generate",
    json={"prompt": "An astronaut lands on an alien planet and finds a garden of glowing flowers"},
    timeout=180,
)

if response.ok:
    board = response.json()
    print(f"{board['title']} — {board['panel_count']} panels")
    for panel in board["panels"]:
        print(f"\nPanel {panel['panel_number']}")
        print(f"  Scene:    {panel['scene_description']}")
        print(f"  Dialogue: {panel['dialogue']}")
else:
    print(f"Error {response.status_code}: {response.json().get('error')}")
```

## Project structure

```
ai-storyboarding/
├── app.py                  # Flask routes, config, error handling
├── services.py             # StoryboardService: prompting, providers, retries
├── requirements.txt
├── .env.example
├── templates/
│   └── index.html          # Single-page UI
└── static/
    ├── css/styles.css
    └── js/script.js        # Form handling, batched image loading, progress UI
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `AI returned invalid JSON after retries` | The text provider returned unparseable output. Retry — the service already re-prompts with stricter instructions twice before giving up. |
| `Gemini image generation quota exceeded (free tier)` | Free-tier quota is exhausted. The app switches to Pollinations for the rest of the request. Add billing to the key, or unset `GEMINI_API_KEY`. |
| Panels render but images stay blank | Pollinations is rate-limiting. `/image-proxy` retries and backs off; wait a moment and regenerate. |
| `Image provider is busy, please retry.` | Upstream returned `429` after all retries. |
| Requests take over a minute | Expected with sequential image generation. Lower `STORYBOARD_PANEL_COUNT` for faster runs. |

## Notes and limitations

- Image generation is sequential by design — it is slower but far more reliable than firing panels concurrently at a free provider.
- Text generation depends on a free public endpoint. Treat it as best-effort; it is not an SLA-backed service.
- Images are generated at 1280×720. There is no export to PDF or ZIP yet — the UI is view-only.
- `FLASK_DEBUG` should stay `false` anywhere other than your own machine; it controls whether raw exception text reaches the client.
- The dev server is not a production server. For deployment, put it behind a WSGI server such as `waitress` or `gunicorn`.

## Roadmap

- [ ] Export a storyboard as PDF or a zipped image set
- [ ] Per-request panel count from the UI instead of env only
- [ ] Save and reload past storyboards
- [ ] Character reference sheet pinned across panels for stronger continuity

## Contributing

Issues and pull requests are welcome. Keep changes focused, match the existing style, and describe the user-visible effect in the PR. For anything larger than a bug fix, open an issue first so we can agree on the approach.

## License

MIT — see [LICENSE](LICENSE).
