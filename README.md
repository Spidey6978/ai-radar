# AI Radar // Frontier Engineering Intelligence

A targeted intelligence aggregator designed to catch verified model releases, primary research disclosures, IDE toolchain removals (like Gemini 3.1 Pro / Claude drops on AGY/Cursor), whisper-net leaks, and systemic AI security advisories before they hit mainstream tech news.

---

## ⚡ Architecture: Aggregator First, Radar Eventually

Traditional tech news is slow, superficial, and buries breaking technical alpha under marketing fluff. AI Radar organizes signals into explicit evidence tiers with automated canonical deduplication and freshness decay.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        SOURCE TIERS & PROVENANCE                       │
├───────────────────┬──────────────────────┬─────────────────────────────┤
│ Layer A: Primary  │ Layer B: Discovery   │ Layer C: Systemic Security  │
│ • GitHub Releases │ • Hacker News (API)  │ • CISA KEV Exploited Vulns  │
│   (vLLM, Ollama,  │ • Reddit Discussions │ • NIST CVE Records          │
│    Transformers)  │   (LocalLLaMA, ML)   │                             │
│ • arXiv Preprints │ • Simon Willison     │                             │
│   (cs.AI, cs.CL)  │ • Hugging Face Blog  │                             │
└───────────────────┴──────────────────────┴─────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        INGESTION & DATA MODEL                          │
├────────────────────────────────────────────────────────────────────────┤
│ 1. Immutable Observations (content_hash, canonical_url, native_id)     │
│ 2. Feed Items (cross-source merged, discussion URLs retained)          │
│ 3. Tiered Half-Life Freshness Decay (72h for releases, 20h for social) │
│ 4. SQLite FTS5 Full-Text Search Virtual Index                          │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🎯 5 Topical Domains

1. **IDEs & Model Drops**: Silent deprecations, toolchain removals (Antigravity, Cursor, Claude Code), pricing cuts, and context limits.
2. **Open Source Frontier**: Sovereign local models, quantizations (GGUF/EXL2), and vLLM/Ollama release notes.
3. **Whisper Net & Leaks**: Mystery models, anonymous weights dumps, leaked benchmark cards, and lab codenames (Argon, Astra, GPT-6).
4. **Bizarre & Anomalies**: Wild autonomous incidents, agent drift, prompt injections, and invite-only summits.
5. **Power & Lab Deals**: Cross-lab handshakes (Google, OpenAI, Anthropic), datacenter power alliances, and compute antitrust moves.

---

## 🚀 Running Locally

### 1. Requirements
Ensure Python 3.10+ is installed. Install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Standalone Ingestion Collector
The collector runs independently from the web server with automatic retry backoff, jitter, and source health tracking:

* **One-shot run:**
  ```bash
  python collector.py --once
  ```

* **Continuous background daemon (e.g. every 15 minutes):**
  ```bash
  python collector.py --interval 900
  ```

### 3. Launch Web Dashboard
Start the local FastAPI server:
```bash
python main.py
```
Open your browser to:
👉 **`http://127.0.0.1:8000`**

---

## 🔍 Features & Ergonomics

* **Evidence Tiers**: Filter signals instantly by `🟢 Verified Artifacts`, `🟡 Research Papers`, `🔵 Security Advisories`, `⚪ Community Leads`, or `📰 Secondary Press`.
* **Full-Text Search (SQLite FTS5)**: Fast local search across titles, summaries, and source names with prefix matching.
* **Source Health Drawer**: Click the health pill in the header (`🟢 15/16 Sources Active`) to inspect status codes, last check times, and rate-limit throttle notices.
* **Item Actions**:
  * `🔖 Save`: Bookmark high-signal items for quick retrieval.
  * `📋 Copy`: 1-click formatted Markdown TL;DR with primary link for sharing to Discord/Slack.
  * `✕ Dismiss`: Soft-dismiss noise from the active feed.
* **Compact Row View**: Toggle between high-density compact row mode and detailed card view.

---

## 🔒 Security & Deployment Notes

* **Feedback Endpoint**: Customer suggestions are stored in `radar.db`. Accessing `GET /api/feedback` requires the `ADMIN_KEY` environment variable (via `X-Admin-Key` header or `?admin_key=...` query param).
* **Storage Considerations**: The local database uses SQLite with Write-Ahead Logging (`WAL` mode) for concurrent reads/writes. Note that serverless platforms like Vercel treat `/tmp` as ephemeral scratch space. For durable cloud deployments, run as a container service (Railway, Fly.io, VPS) or connect to an external persistent driver (e.g., Turso/libSQL).
