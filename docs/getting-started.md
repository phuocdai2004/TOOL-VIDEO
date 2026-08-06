# 🚀 Quick Start

## Prerequisites

- Python 3.10+
- MongoDB 8.0+ (stores accounts, sessions, and roles)
- ffmpeg — required only for **manual setup** (Option A); the Docker (Option B) and npm (Option C) options bundle ffmpeg automatically, so no system ffmpeg install is needed there.

That's it. No GPU, no large RAM, a regular laptop is all you need.

## Option A: Manual Setup

**Step 1 — Clone & Launch**

```bash
git clone https://github.com/phuocdai2004/TOOL-VIDEO.git
cd agnes-video-generator
./start.sh
```

Make sure MongoDB is running locally first. The script automatically creates a virtual environment, installs dependencies, and opens `http://localhost:8765` in your browser. You can also start manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

**Step 2 — Create the Superadmin Account**

Open `http://localhost:8765` and register the first account. The first account automatically receives the `superadmin` role. Later accounts start as regular users and only see their own videos.

**Step 3 — Configure API Key**

Get a free API key from [Agnes AI](https://platform.agnes-ai.com), then choose one of two ways:

```bash
# Way 1: Environment variable
export AGNES_API_KEY="your-api-key"

# Way 2: Via API (same as entering it in the Web UI)
curl -X POST http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{"api_key": "your-api-key"}'
```

Only an admin or superadmin can change shared API keys and workspaces.

**Step 4 — Create Your First Video**

Open `http://localhost:8765`, choose a video mode (Simple / Creative / Manuscript / Anchor), enter your idea, and click "Start Generating".

### Password recovery email

Copy `.env.example` to `.env` in the project root and configure SMTP before using **Forgot password**. For Gmail, use an App Password rather than your normal Google password.

```env
PUBLIC_BASE_URL=http://localhost:8765
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-account@gmail.com
SMTP_PASSWORD=your-google-app-password
SMTP_FROM=your-account@gmail.com
SMTP_USE_TLS=true
```

Restart the service after changing `.env`. Reset links are single-use, expire after 30 minutes, and revoke existing login sessions after the password changes.

## Option B: Docker (No Python/FFmpeg Required)

Pre-built multi-arch images (`linux/amd64`, `linux/arm64`) are published to both **GitHub Container Registry (GHCR)** and **Docker Hub** on every release.

The recommended deployment is the included Compose stack because it starts both the app and its private MongoDB service:

```bash
git clone https://github.com/phuocdai2004/TOOL-VIDEO.git
cd agnes-video-generator
cp .env.example .env
docker compose up -d
```

Set `AGNES_API_KEY` and `GEMINI_API_KEY` in `.env` when needed, then open `http://localhost:8765` and create the first superadmin account.

**Data Persistence:** Videos, uploads, settings, and MongoDB data are stored under `./agnes_data/`, so they survive container recreation.

For a standalone app container, provide a reachable MongoDB instance explicitly:

```bash
docker run -d -p 8765:8765 \
  -e MONGODB_URI='mongodb://your-mongodb-host:27017/agnes_video' \
  -e AGNES_API_KEY=<your-key> \
  -v ~/agnes-data/working:/app/.working_dir \
  -v ~/agnes-data/config:/app/.agnes_config \
  ghcr.io/phuocdai2004/tool-video:latest
```

## Option C: npm (One Command)

If you have **Node.js 18+**, **Python 3.10+**, and a running MongoDB instance, the whole service ships as an npm package — no cloning or manual venv:

```bash
# Run directly without installing
npx tool-video

# Or install globally, then run
npm install -g tool-video
tool-video          # short alias: fsv
```

On first run the launcher automatically creates a local virtual environment, installs Python dependencies, wires up a bundled `ffmpeg` (via `imageio-ffmpeg`, so no system ffmpeg needed), starts the server on `http://localhost:8765`, and opens your browser. Pass your key through the environment or set it later in the Web UI:

```bash
AGNES_API_KEY=<your-key> npx tool-video
```

Options: `--port <n>`, `--host <h>` (use `0.0.0.0` for LAN access), `--no-open`.

### ffmpeg: bundled by default, or install your own

With the npm package you normally **don't need to install ffmpeg yourself** — the launcher (`bin/cli.js`) automatically installs `imageio-ffmpeg` (a static ffmpeg binary, now an explicit dependency in `requirements.txt`) into the local venv and prepends its directory to `PATH`, so every `ffmpeg` call inside the Python service resolves to the bundled binary. This works out of the box on macOS, Linux, and Windows.

**If you prefer to install ffmpeg on your system** (recommended for production / maximum stability — your system ffmpeg takes precedence over the bundled one because it appears earlier on `PATH`):

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt update && sudo apt install ffmpeg

# CentOS / RHEL (requires RPM Fusion)
sudo dnf install ffmpeg

# Windows (Chocolatey)
choco install ffmpeg

# Windows (Scoop)
scoop install ffmpeg
```

Or download a build from <https://ffmpeg.org/download.html> and add it to your `PATH`. Verify with:

```bash
ffmpeg -version
```

**Risks if you do NOT install a system ffmpeg (i.e. rely solely on the bundled `imageio-ffmpeg`):**

- **Platform / architecture support** — `imageio-ffmpeg` ships pre-built binaries only for common platforms (macOS x86_64/arm64, Linux x86_64/arm64, Windows x64). On niche or very old architectures a matching wheel may not exist, and the bundled binary would be missing.
- **Single source of truth** — all ffmpeg capability comes from that one static binary. If its install/extract fails (disk permissions, corruption), the failure only surfaces **when you generate a video**, not at server startup — the error is a low-level `FileNotFoundError: 'ffmpeg'`, which is harder to diagnose than a startup check.
- **Pinned version** — the bundled ffmpeg is locked to whatever version `imageio-ffmpeg` ships (e.g. ffmpeg 7.1); you can't easily upgrade it on your own.
- **Mitigation** — for production or stability-critical use, install a system ffmpeg as shown above; the bundled one then acts only as a fallback.

## Option D: AI Agent Assisted Setup

This project is designed for AI coding assistants. First, download the code and prepare your API key:

```bash
git clone https://github.com/phuocdai2004/TOOL-VIDEO.git
cd agnes-video-generator
```

Then tell your agent:

> "Read the AGENTS.md in this project, install dependencies, configure the API key `<your-key>`, and start the server."

The agent will read `AGENTS.md` (a comprehensive deployment guide) and handle: environment checks (Python 3.10+, ffmpeg), `pip install`, server launch, and API key configuration. After startup, you can also ask the agent to verify the deployment:

> "Run the deployment verification checks."

The agent will execute the 4-layer checklist from `AGENTS.md` (connectivity → static analysis → endpoint testing → subtitle feature) and report results.
