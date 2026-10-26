# AI Radar // Frontier & Niche Intel Fetcher

A targeted intelligence aggregator designed to catch stealth model shifts, IDE toolchain removals (like Gemini 3.1 Pro / Claude drops on AGY/Cursor), whisper-net leaks (Argon, Astra, mystery LMSYS models), and bizarre autonomous anomalies before they hit mainstream tech news.

---

## ⚡ What it Does

Traditional news outlets are too slow, too generic, and bury technical shifts under marketing fluff. AI Radar pulls directly from the primary sources where developer alpha breaks:

- **Hacker News (Algolia Search API)**: Instant alerts from developer discussions and front-page technical posts.
- **Reddit RSS Feeds**: Real-time posts from `r/LocalLLaMA`, `r/singularity`, and `r/MachineLearning`.
- **Targeted Google News RSS**: Focused query channels for unannounced model codenames, lab backroom deals, and anomalies.
- **Engineering Trackers**: Specialized blogs (Simon Willison, Hugging Face) tracking prompt injections and release notes.

---

## 🎯 5 Core Intel Channels

1. **IDEs & Model Drops**: Silent deprecations, model removals (Antigravity, Cursor, Claude Code), pricing cuts, and context limit adjustments.
2. **Whisper Net & Leaks**: LMSYS Chatbot Arena mystery models, anonymous weights dumps, leaked benchmark cards, and lab codenames (Argon, Astra, GPT-6).
3. **Bizarre & Anomalies**: Wild autonomous incidents, satellite/drone AI drift, prompt injections, and invite-only secret summits.
4. **Power & Lab Deals**: Cross-lab handshakes (Google, OpenAI, Anthropic), datacenter nuclear energy alliances, and compute antitrust moves.
5. **Open Source Frontier**: Sovereign local models, quantizations (GGUF/EXL2), vLLM/Ollama breakthroughs beating proprietary APIs.

---

## 🚀 Running Locally

### 1. Requirements
Ensure Python 3.10+ is installed. Dependencies:
```bash
pip install -r requirements.txt
```

### 2. Fetch Latest Intel
Run the fetcher to query all channels and populate the local SQLite database (`radar.db`):
```bash
python fetcher.py
```

### 3. Launch Web Dashboard
Start the local FastAPI server:
```bash
python main.py
```
Open your browser to:
👉 **`http://127.0.0.1:8000`**

---

## 💡 Built-in Feedback & Monetization Survey

Integrated at the bottom of the dashboard is an interactive suggestions box where users can:
- Request specific tools, repos, or obscure incidents to track.
- Answer whether they would pay for **real-time push alerts** (Telegram/Discord bot) or **Pro lab intelligence** ($5/mo, $20/mo, or $100/mo API).
- Submissions are stored directly in `radar.db` and can be inspected via `/api/feedback`.

---

## ☁️ Deploying to Vercel

The project includes `vercel.json` and `api/index.py` configured for Vercel Serverless:
1. Push this folder to a GitHub repository.
2. Import the repository into [Vercel](https://vercel.com).
3. Vercel automatically detects the Python runtime and serves the app.
