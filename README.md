# ReelMaker — Local AI Highlight Reel Generator

Turns folders of photos and videos into a searchable media knowledge base, then
generates highlight reels from a text prompt. 100% local — your media and API
keys never leave your machine. Windows-first.

- Vision analysis, semantic scene search, LLM edit planning, FFmpeg rendering
- Face-aware photo framing (YuNet head detection keeps full heads in frame), EXIF-correct
  orientation, uniform still pacing, frame-exact reel durations
- Design doc: [`docs/implementation-plan.md`](docs/implementation-plan.md) · current state:
  [`docs/status.md`](docs/status.md) · stack inventory: [`docs/tech_stack.md`](docs/tech_stack.md) ·
  original spec: [`spec.txt`](spec.txt)

Architecture review and suggested fixes: [`docs/architecture_review.md`](docs/architecture_review.md).
Contributors: read and maintain [`docs/lessons_learned.md`](docs/lessons_learned.md)
alongside the stack inventory; see [`AGENTS.md`](AGENTS.md).

## Stack

| Layer | Tech |
|---|---|
| UI | Vue 3 + TypeScript + Vite + Pinia + TanStack Query |
| Backend | Python + FastAPI + SQLite (per-project knowledge base) |
| AI | Unsloth Desktop (any OpenAI-compatible server), Qwen3-VL for vision, SigLIP2 embeddings |
| Media | FFmpeg/ffprobe (scenes, frames, rendering), exiftool (GPS/camera), PySceneDetect, OpenCV (YuNet faces, frame metrics) |

## Prerequisites (one-time)

1. **uv** (Python manager): `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. **Node.js** 20+: `winget install OpenJS.NodeJS.LTS`
3. **FFmpeg**: `winget install Gyan.FFmpeg`
4. **exiftool**: `winget install exiftool`   *(optional but recommended — GPS/camera metadata)*
5. **Unsloth Desktop**: <https://unsloth.ai/download>
6. NVIDIA GPU recommended (NVENC used automatically; falls back to CPU x264 without one)

## First-time setup

### 1. Install dependencies

```powershell
cd backend
uv sync --group ml          # Python deps incl. CPU torch + SigLIP2
cd ..\frontend
npm install
```

### 2. Configure the AI connection (backend\.env)

1. Open **Unsloth Desktop** → load a **vision** model
   (e.g. `Qwen3-VL-8B-Instruct-abliterated-v2.0-GGUF:Q4_K_S`).
2. In Unsloth: avatar → **Settings → API** → create a key (`sk-unsloth-…`). Remember the API key is often set to expire after n days when it's created in Unsloth Desktop.
3. Copy `backend\.env.example` to `backend\.env` and fill it in:

```
REELMAKER_UNSLOTH_BASE_URL=http://127.0.0.1:8888   # port Unsloth prints on startup
REELMAKER_UNSLOTH_API_KEY=sk-unsloth-...           # paste yours here
REELMAKER_PLANNER_MODEL_ID=                        # blank = auto-detect
```

`.env` is gitignored — never commit real keys. Prefer short-expiry keys.

> Tip: `/v1/models` lists every *downloaded* model even if nothing is loaded.
> If planning/vision fails with "downloaded but not loaded", open Unsloth and load it.

### 3. Start everything

```powershell
powershell scripts\start-dev.ps1
```

Then open **<http://localhost:5173>** (backend API runs at `127.0.0.1:8000`).

## Daily use

| Action | How |
|---|---|
| Start | `powershell scripts\start-dev.ps1` → browser to `localhost:5173` |
| Stop | `powershell scripts\stop-dev.ps1` |
| Logs | `%TEMP%\reelmaker-dev\*.log` |

### Making a reel

1. **Projects** → name + absolute path to a media folder → Create (auto-scans)
2. **Analyze** — the single button always rescans the folder first (picks up new files, drops
   deleted ones), then runs scenes, frames, quality metrics, tags, AI descriptions
   (~5–8s per scene on an RTX 4070 Super; resumable/idempotent, safe to re-run)
3. **Generate reel** — describe it ("energetic summit montage"), set target seconds.
   Still images are paced uniformly (target time ÷ image count); portrait photos are
   auto-framed to keep whole heads in view
4. The **Reels grid** lists every plan: Render → slim progress bar → **⬇ download icon**.
   Reels stay bold (unread) until downloaded. Expand a row (▸) for the instant plan preview
   player + clip list
5. **Scene library** (collapsed accordion near the bottom) — semantic search + tag chips +
   thumbnails, when you want to inspect what was indexed

Re-planning is cheap (~10–30s); expensive analysis runs once and is cached forever.

## Where your data lives

Everything runtime-related is outside the repo:

- Projects DBs, thumbnails, exports: `%LOCALAPPDATA%\ReelMaker\`
- HF model cache (SigLIP2): `%LOCALAPPDATA%\ReelMaker\hf`
- Face-detection model (YuNet ONNX, ~230KB, auto-downloaded on first render with photos):
  `%LOCALAPPDATA%\ReelMaker\models`

## Troubleshooting

| Symptom | Fix |
|---|---|
| Port 5173 busy error at startup | Another app took Vite's fixed port — stop it, re-run start script |
| "model … downloaded but not loaded" | Load the model into a slot in Unsloth Desktop |
| Planning returns JSON errors | Ensure a model is loaded; retry — output is auto-salvaged/truncated-repaired |
| No GPS/camera metadata | Install exiftool, then re-run Analyze |
| Analyze says "0 files" | Hit Analyze again — it always rescans the folder first |
| **Python changes not taking effect** | The backend has no auto-reload — `scripts\stop-dev.ps1` then `start-dev.ps1`. Check the port-8000 process start time before debugging "broken" code |
| Photo crops look wrong / faces clipped | Check `%LOCALAPPDATA%\ReelMaker\models` contains the YuNet ONNX (delete it to force re-download). Offline machines silently fall back to a simple upper-biased crop |

## Development

```powershell
cd backend
uv run pytest            # fast suite (add -m slow for full-model E2E)
uv run ruff check .      # lint
..\frontend\npm run build  # type-checked production build
```

Backend restart needed after editing Python code (no auto-reload):
`scripts\stop-dev.ps1` then `scripts\start-dev.ps1`. Frontend hot-reloads.
