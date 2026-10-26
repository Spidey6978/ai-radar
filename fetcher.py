import asyncio
import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import feedparser
import httpx
import yaml
import database

logger = logging.getLogger("ai_radar_fetcher")

BASE_DIR = Path(__file__).parent
SOURCES_YAML_PATH = BASE_DIR / "sources.yaml"

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "ref_src", "feature", "ved", "usg"
}

# Per-host rate pacing tracking
_last_host_hit: Dict[str, float] = {}
_host_locks: Dict[str, asyncio.Lock] = {}

def get_host_lock(host: str) -> asyncio.Lock:
    if host not in _host_locks:
        _host_locks[host] = asyncio.Lock()
    return _host_locks[host]

async def pace_host(host: str, min_delay: float = 0.5):
    """Enforces per-host spacing to avoid 429 throttling."""
    lock = get_host_lock(host)
    async with lock:
        now = asyncio.get_event_loop().time()
        last = _last_host_hit.get(host, 0.0)
        elapsed = now - last
        if elapsed < min_delay:
            await asyncio.sleep(min_delay - elapsed)
        _last_host_hit[host] = asyncio.get_event_loop().time()

def load_sources_config() -> Dict[str, Any]:
    if not SOURCES_YAML_PATH.exists():
        raise FileNotFoundError(f"sources.yaml not found at {SOURCES_YAML_PATH}")
    with open(SOURCES_YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def normalize_url(url: str) -> str:
    """Strips marketing trackers, trailing slashes, and fragments to ensure true canonical URL."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        query_dict = parse_qs(parsed.query, keep_blank_values=False)
        cleaned_query = {k: v for k, v in query_dict.items() if k.lower() not in TRACKING_PARAMS}
        new_query = urlencode(cleaned_query, doseq=True)
        clean_path = parsed.path.rstrip("/")
        if not clean_path and not parsed.netloc:
            return url
        return urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            clean_path,
            "",
            new_query,
            ""
        ))
    except Exception:
        return url.strip()

def clean_text(raw_html: str, max_len: int = 340) -> str:
    """Strips HTML tags, collapses whitespaces, and unescapes entities."""
    if not raw_html:
        return ""
    clean = re.sub(r"<[^>]+>", " ", raw_html)
    clean = html.unescape(clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > max_len:
        return clean[:max_len].rsplit(" ", 1)[0] + "..."
    return clean

def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def make_id(prefix: str, identifier: str) -> str:
    seed = f"{prefix}:{identifier}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:16]

# -------------------------------------------------------------
# ZERO-SLUDGE NEWS FILTERS & GATES
# -------------------------------------------------------------

CLICKBAIT_PATTERNS = [
    r"\btop\s+\d+\b",
    r"\bbest\s+ai\s+tools\b",
    r"\bprompt\s+engineering\s+guide\b",
    r"\bmake\s+money\b",
    r"\bpassive\s+income\b",
    r"\bwill\s+change\s+everything\b",
    r"\bmind[-\s]?blowing\b",
    r"\binsane\b",
    r"\brevolutionize\s+your\b",
    r"\bai\s+girlfriend\b",
    r"\bcelebrity\b",
    r"\bhow\s+to\s+use\s+chatgpt\s+to\b",
    r"\bthreat\s+to\s+humanity\b",
    r"\bgodfather\s+of\s+ai\b"
]

ANCHOR_ENTITIES = [
    # Labs & Major Tools
    "antigravity", "agy", "cursor", "claude code", "windsurf", "copilot",
    "openai", "deepmind", "anthropic", "meta fair", "mistral", "xai", "groq",
    # Models & Codenames
    "argon", "astra", "gemini", "claude", "gpt-4", "gpt-5", "gpt-6", "orion", "strawberry",
    "deepseek", "qwen", "llama", "vllm", "ollama", "openrouter",
    # Infrastructure & Policy
    "blackwell", "tpu", "h100", "b200", "datacenter", "nuclear", "antitrust",
    "satellite", "prompt injection", "cve-"
]

ACTION_VERBS = [
    "deprecated", "remove", "removed", "dropped", "handshake", "alliance",
    "deal", "licensing", "antitrust", "breach", "vulnerability", "leak", "unannounced",
    "release", "weights", "weights dumped", "quant", "takeover", "anomaly", "drift"
]

def is_clickbait_or_sludge(title: str, summary: str) -> bool:
    """Returns True if the content matches known consumer clickbait / SEO filler."""
    text = (title + " " + summary).lower()
    for pattern in CLICKBAIT_PATTERNS:
        if re.search(pattern, text):
            return True
    return False

def has_anchor_intel(title: str, summary: str) -> bool:
    """Returns True if the story contains concrete technical entities and actions."""
    text = (title + " " + summary).lower()
    has_entity = any(k in text for k in ANCHOR_ENTITIES)
    has_action = any(k in text for k in ACTION_VERBS)
    return has_entity and has_action

def classify_category(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()
    if any(k in text for k in ["antigravity", "agy", "cursor", "claude code", "copilot", "windsurf", "deprecated", "removed", "model drop", "pricing cut", "vllm", "ollama", "release"]):
        if any(k in text for k in ["vllm", "ollama", "gguf", "exl2", "fine-tune", "localllama", "weights dumped"]):
            return "Open Source Frontier"
        return "IDEs & Model Drops"

    if any(k in text for k in ["leak", "mystery", "lmsys", "arena", "argon", "astra", "gpt-6", "checkpoint", "whisper", "unannounced"]):
        return "Whisper Net & Leaks"

    if any(k in text for k in ["satellite", "anomaly", "rogue", "drift", "secret meetup", "incident", "injection", "cve-", "vulnerability"]):
        return "Bizarre & Anomalies"

    if any(k in text for k in ["handshake", "alliance", "antitrust", "nuclear", "datacenter", "pentagon", "acquisition", "invest", "google and openai"]):
        return "Power & Lab Deals"

    if any(k in text for k in ["localllama", "hugging face", "open weight", "deepseek", "qwen", "mistral", "quant"]):
        return "Open Source Frontier"

    return "General AI"

# -------------------------------------------------------------
# CONDITIONAL GET HELPER
# -------------------------------------------------------------

def build_headers(cfg_headers: dict, source_id: str, custom_ua: str) -> dict:
    headers = dict(cfg_headers)
    headers["User-Agent"] = custom_ua
    etag, last_mod = database.get_source_conditional_headers(source_id)
    if etag:
        headers["If-None-Match"] = etag
    if last_mod:
        headers["If-Modified-Since"] = last_mod
    return headers

# -------------------------------------------------------------
# TRACK 1: SNAPSHOT DIFF WATCHERS (THE TRUE RADAR)
# -------------------------------------------------------------

async def watch_openrouter_models(client: httpx.AsyncClient, source_cfg: dict, custom_ua: str) -> List[Dict[str, Any]]:
    """
    Snapshots the entire OpenRouter model registry.
    Diffs against previous snapshot to catch newly deployed models and silent removals.
    """
    source_id = source_cfg["id"]
    endpoint = source_cfg["endpoint"]
    host = urlparse(endpoint).netloc
    await pace_host(host, 1.0)
    
    headers = build_headers({}, source_id, custom_ua)
    items = []
    
    try:
        res = await client.get(endpoint, headers=headers, timeout=12.0)
        if res.status_code == 304:
            database.update_source_health(source_id, 304, items_count=0)
            return []
            
        if res.status_code == 200:
            data = res.json()
            models_list = data.get("data", [])
            current_ids: Set[str] = {m["id"] for m in models_list if "id" in m}
            content_hash = compute_hash(json.dumps(sorted(list(current_ids))))
            
            # Retrieve previous snapshot
            prev_snapshot = database.get_latest_snapshot(source_id)
            prev_ids: Set[str] = set()
            if prev_snapshot:
                try:
                    prev_ids = set(json.loads(prev_snapshot["data_json"]))
                except Exception:
                    prev_ids = set()
                    
            # Compute Diff
            added_models = sorted(list(current_ids - prev_ids))
            removed_models = sorted(list(prev_ids - current_ids))
            
            # Save new snapshot
            database.save_snapshot(source_id, content_hash, json.dumps(sorted(list(current_ids))))
            database.update_source_health(
                source_id, 200, items_count=len(models_list),
                etag=res.headers.get("etag"),
                last_modified=res.headers.get("last-modified")
            )
            
            # If models changed, emit verified events!
            now_iso = datetime.now(timezone.utc).isoformat()
            if prev_snapshot and (added_models or removed_models):
                diff_lines = []
                for m in added_models:
                    diff_lines.append(f"+ ADDED: {m}")
                for m in removed_models:
                    diff_lines.append(f"- REMOVED: {m}")
                evidence_diff = "\n".join(diff_lines)
                
                title = f"[Model Catalog Shift] {len(added_models)} added, {len(removed_models)} dropped on OpenRouter"
                summary = f"Automated snapshot diff detected model catalog update. Added: {', '.join(added_models[:4]) or 'None'}. Removed: {', '.join(removed_models[:4]) or 'None'}."
                
                event_id = make_id("event", f"{source_id}:{content_hash}")
                canonical_url = f"https://openrouter.ai/models#diff-{content_hash[:8]}"
                
                database.create_event(
                    event_id=event_id,
                    event_type="catalog_diff",
                    entity_name="openrouter",
                    title=title,
                    what_changed=summary,
                    evidence_diff=evidence_diff,
                    evidence_tier="verified_artifact",
                    canonical_url=canonical_url,
                    track="radar_signal"
                )
                
                items.append({
                    "source_id": source_id,
                    "source_name": source_cfg["name"],
                    "source_native_id": content_hash[:16],
                    "canonical_url": canonical_url,
                    "title": title,
                    "summary": summary,
                    "primary_type": "release",
                    "evidence_tier": "verified_artifact",
                    "category": "IDEs & Model Drops",
                    "track": "radar_signal",
                    "base_score": 98.0,
                    "evidence_diff": evidence_diff,
                    "published_at": now_iso,
                    "discussion_urls": ["https://openrouter.ai/models"],
                    "raw_metadata": {"added": added_models, "removed": removed_models}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, error_message=str(e))
        
    return items

async def watch_litellm_prices(client: httpx.AsyncClient, source_cfg: dict, custom_ua: str) -> List[Dict[str, Any]]:
    """
    Snapshots LiteLLM model_prices_and_context_window.json.
    Detects unannounced models or price changes added to the proxy manifest.
    """
    source_id = source_cfg["id"]
    endpoint = source_cfg["endpoint"]
    host = urlparse(endpoint).netloc
    await pace_host(host, 0.5)
    
    headers = build_headers({}, source_id, custom_ua)
    items = []
    
    try:
        res = await client.get(endpoint, headers=headers, timeout=14.0)
        if res.status_code == 304:
            database.update_source_health(source_id, 304, items_count=0)
            return []
            
        if res.status_code == 200:
            data = res.json()
            current_keys: Set[str] = set(data.keys())
            content_hash = compute_hash(json.dumps(sorted(list(current_keys))))
            
            prev_snapshot = database.get_latest_snapshot(source_id)
            prev_keys: Set[str] = set()
            if prev_snapshot:
                try:
                    prev_keys = set(json.loads(prev_snapshot["data_json"]))
                except Exception:
                    prev_keys = set()
                    
            added_keys = sorted(list(current_keys - prev_keys))
            database.save_snapshot(source_id, content_hash, json.dumps(sorted(list(current_keys))))
            database.update_source_health(
                source_id, 200, items_count=len(current_keys),
                etag=res.headers.get("etag"),
                last_modified=res.headers.get("last-modified")
            )
            
            now_iso = datetime.now(timezone.utc).isoformat()
            if prev_snapshot and added_keys:
                diff_lines = [f"+ NEW MODEL DEFINITION: {k}" for k in added_keys[:10]]
                evidence_diff = "\n".join(diff_lines)
                
                title = f"[LiteLLM Manifest Drop] {len(added_keys)} new model definitions spotted"
                summary = f"LiteLLM pricing & context window manifest updated with models: {', '.join(added_keys[:5])}."
                canonical_url = "https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json"
                
                event_id = make_id("event", f"{source_id}:{content_hash}")
                database.create_event(
                    event_id=event_id,
                    event_type="manifest_diff",
                    entity_name="litellm",
                    title=title,
                    what_changed=summary,
                    evidence_diff=evidence_diff,
                    evidence_tier="verified_artifact",
                    canonical_url=canonical_url,
                    track="radar_signal"
                )
                
                items.append({
                    "source_id": source_id,
                    "source_name": source_cfg["name"],
                    "source_native_id": content_hash[:16],
                    "canonical_url": canonical_url,
                    "title": title,
                    "summary": summary,
                    "primary_type": "release",
                    "evidence_tier": "verified_artifact",
                    "category": "IDEs & Model Drops",
                    "track": "radar_signal",
                    "base_score": 95.0,
                    "evidence_diff": evidence_diff,
                    "published_at": now_iso,
                    "discussion_urls": [canonical_url],
                    "raw_metadata": {"added_models": added_keys}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, error_message=str(e))
        
    return items

# -------------------------------------------------------------
# PRIMARY GITHUB, ARXIV & CISA WATCHERS
# -------------------------------------------------------------

async def fetch_github_releases(client: httpx.AsyncClient, source_cfg: dict, custom_ua: str, token: str) -> List[Dict[str, Any]]:
    source_id = source_cfg["id"]
    endpoint = source_cfg["endpoint"]
    host = urlparse(endpoint).netloc
    await pace_host(host, 0.4)
    
    headers = build_headers({}, source_id, custom_ua)
    if token:
        headers["Authorization"] = f"token {token}"
        
    items = []
    try:
        res = await client.get(endpoint, headers=headers, timeout=10.0)
        if res.status_code == 304:
            database.update_source_health(source_id, 304)
            return []
            
        database.update_source_health(
            source_id, res.status_code,
            etag=res.headers.get("etag"),
            last_modified=res.headers.get("last-modified")
        )
        if res.status_code == 200:
            releases = res.json()
            for rel in releases:
                if rel.get("draft") or not rel.get("tag_name"):
                    continue
                tag = rel.get("tag_name")
                name = rel.get("name") or tag
                title = f"[{source_cfg['name'].replace('GitHub: ', '')}] {name} Released"
                body = clean_text(rel.get("body", ""), max_len=300)
                html_url = normalize_url(rel.get("html_url", ""))
                pub_iso = database.parse_to_utc_iso(rel.get("published_at"))
                
                items.append({
                    "source_id": source_id,
                    "source_name": source_cfg["name"],
                    "source_native_id": str(rel.get("id")),
                    "canonical_url": html_url,
                    "title": title,
                    "summary": body or f"Official version {tag} published.",
                    "primary_type": "release",
                    "evidence_tier": "verified_artifact",
                    "category": "Open Source Frontier" if "vllm" in source_id or "ollama" in source_id else "IDEs & Model Drops",
                    "track": "radar_signal",
                    "base_score": 92.0,
                    "published_at": pub_iso,
                    "discussion_urls": [html_url],
                    "raw_metadata": {"tag": tag, "author": rel.get("author", {}).get("login")}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, error_message=str(e))
        
    return items

async def fetch_arxiv_ai(client: httpx.AsyncClient, source_cfg: dict, custom_ua: str) -> List[Dict[str, Any]]:
    source_id = source_cfg["id"]
    endpoint = source_cfg["endpoint"]
    host = urlparse(endpoint).netloc
    # Respect arXiv 3-second etiquette
    await pace_host(host, 3.0)
    
    headers = build_headers({}, source_id, custom_ua)
    items = []
    
    try:
        res = await client.get(endpoint, headers=headers, timeout=12.0)
        database.update_source_health(source_id, res.status_code)
        if res.status_code == 200:
            feed = feedparser.parse(res.text)
            for entry in feed.entries:
                title = clean_text(entry.title).replace("\n", " ")
                summary = clean_text(entry.summary, max_len=300)
                link = normalize_url(entry.link)
                pub_iso = database.parse_to_utc_iso(entry.get("published"))
                
                # AI Research Strict Relevance Filter
                if not any(k in (title + " " + summary).lower() for k in ["llm", "language model", "reasoning", "transformer", "agent", "alignment", "safety", "benchmark", "inference"]):
                    continue
                    
                items.append({
                    "source_id": source_id,
                    "source_name": "arXiv",
                    "source_native_id": entry.id,
                    "canonical_url": link,
                    "title": f"[Paper] {title}",
                    "summary": summary,
                    "primary_type": "research",
                    "evidence_tier": "technical_paper",
                    "category": "Open Source Frontier",
                    "track": "radar_signal",
                    "base_score": 85.0,
                    "published_at": pub_iso,
                    "discussion_urls": [link],
                    "raw_metadata": {"authors": [a.get("name") for a in entry.get("authors", [])]}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, error_message=str(e))
        
    return items

async def fetch_cisa_security(client: httpx.AsyncClient, source_cfg: dict, custom_ua: str) -> List[Dict[str, Any]]:
    source_id = source_cfg["id"]
    endpoint = source_cfg["endpoint"]
    host = urlparse(endpoint).netloc
    await pace_host(host, 1.0)
    
    headers = build_headers({}, source_id, custom_ua)
    items = []
    
    try:
        res = await client.get(endpoint, headers=headers, timeout=12.0)
        if res.status_code == 304:
            database.update_source_health(source_id, 304)
            return []
            
        database.update_source_health(
            source_id, res.status_code,
            etag=res.headers.get("etag"),
            last_modified=res.headers.get("last-modified")
        )
        if res.status_code == 200:
            data = res.json()
            vulns = data.get("vulnerabilities", [])
            # Filter strictly for AI/ML and developer toolchains
            ai_keywords = ["artificial intelligence", "machine learning", "neural", "pytorch", "tensorflow", "ollama", "huggingface", "model", "langchain"]
            
            for v in reversed(vulns[-30:]):
                desc = clean_text(v.get("shortDescription", ""))
                vendor = v.get("vendorProject", "")
                product = v.get("product", "")
                combined = (desc + " " + vendor + " " + product).lower()
                
                # Strict AI filter - discard unrelated Apache/Windows printer CVEs
                if not any(k in combined for k in ai_keywords):
                    continue
                    
                cve_id = v.get("cveID")
                nvd_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
                pub_iso = database.parse_to_utc_iso(v.get("dateAdded"))
                
                title = f"[Security Advisory] {cve_id} in {vendor} {product}"
                items.append({
                    "source_id": source_id,
                    "source_name": "CISA KEV",
                    "source_native_id": cve_id,
                    "canonical_url": nvd_url,
                    "title": title,
                    "summary": f"{desc} (Remediation Due: {v.get('dueDate', 'N/A')})",
                    "primary_type": "security",
                    "evidence_tier": "security_advisory",
                    "category": "Bizarre & Anomalies",
                    "track": "radar_signal",
                    "base_score": 90.0,
                    "published_at": pub_iso,
                    "discussion_urls": [nvd_url],
                    "raw_metadata": {"cve": cve_id, "vendor": vendor}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, error_message=str(e))
        
    return items

# -------------------------------------------------------------
# TRACK 2: CURATED NEWS, SCOOPS & LEAKS
# -------------------------------------------------------------

async def fetch_rss_news(client: httpx.AsyncClient, source_cfg: dict, custom_ua: str) -> List[Dict[str, Any]]:
    source_id = source_cfg["id"]
    endpoint = source_cfg["endpoint"]
    host = urlparse(endpoint).netloc
    delay = source_cfg.get("host_delay_seconds", 1.0)
    await pace_host(host, delay)
    
    headers = build_headers({}, source_id, custom_ua)
    items = []
    
    try:
        res = await client.get(endpoint, headers=headers, timeout=10.0)
        if res.status_code == 304:
            database.update_source_health(source_id, 304)
            return []
            
        database.update_source_health(
            source_id, res.status_code,
            etag=res.headers.get("etag"),
            last_modified=res.headers.get("last-modified")
        )
        if res.status_code == 200:
            feed = feedparser.parse(res.text)
            for entry in feed.entries[:8]:
                title = clean_text(entry.title)
                raw_sum = entry.get("summary") or entry.get("description") or ""
                summary = clean_text(raw_sum, max_len=300)
                link = normalize_url(entry.link)
                pub_iso = database.parse_to_utc_iso(entry.get("published") or entry.get("updated"))
                
                # ANTI-SLUDGE & ANCHOR ENTITY GATE
                if is_clickbait_or_sludge(title, summary):
                    continue
                if source_cfg.get("relevance_gate") == "anti_sludge_strict":
                    # Google News and general feeds require concrete anchor intel
                    if "gnews" in source_id and not has_anchor_intel(title, summary):
                        continue

                items.append({
                    "source_id": source_id,
                    "source_name": source_cfg["name"],
                    "source_native_id": entry.id if hasattr(entry, "id") else link,
                    "canonical_url": link,
                    "title": title,
                    "summary": summary,
                    "primary_type": "news",
                    "evidence_tier": source_cfg.get("evidence_tier", "secondary_press"),
                    "category": classify_category(title, summary),
                    "track": "curated_news",
                    "base_score": 75.0 if "Simon" in source_cfg["name"] else 60.0,
                    "published_at": pub_iso,
                    "discussion_urls": [link],
                    "raw_metadata": {"feed": source_cfg["name"]}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, error_message=str(e))
        
    return items

async def fetch_hn_algolia(client: httpx.AsyncClient, source_cfg: dict, custom_ua: str) -> List[Dict[str, Any]]:
    source_id = source_cfg["id"]
    endpoint = source_cfg["endpoint"]
    host = urlparse(endpoint).netloc
    await pace_host(host, 0.5)
    
    headers = build_headers({}, source_id, custom_ua)
    items = []
    
    try:
        res = await client.get(endpoint, headers=headers, timeout=10.0)
        database.update_source_health(source_id, res.status_code)
        if res.status_code == 200:
            hits = res.json().get("hits", [])
            for hit in hits:
                story_id = str(hit.get("objectID"))
                title = clean_text(hit.get("title", ""))
                if not title or len(title) < 12:
                    continue
                raw_url = hit.get("url")
                hn_item_url = f"https://news.ycombinator.com/item?id={story_id}"
                canonical_url = normalize_url(raw_url) if raw_url else hn_item_url
                
                story_text = clean_text(hit.get("story_text", ""), max_len=280)
                points = hit.get("points") or 0
                comments = hit.get("num_comments") or 0
                pub_iso = database.parse_to_utc_iso(hit.get("created_at"))
                summary = story_text or f"Hacker News technical discussion by {hit.get('author')}. ({points} points, {comments} comments)."
                
                # ANTI-SLUDGE GATE
                if is_clickbait_or_sludge(title, summary):
                    continue
                    
                items.append({
                    "source_id": source_id,
                    "source_name": "Hacker News",
                    "source_native_id": story_id,
                    "canonical_url": canonical_url,
                    "title": title,
                    "summary": summary,
                    "primary_type": "community_lead",
                    "evidence_tier": "community_claim",
                    "category": classify_category(title, summary),
                    "track": "curated_news",
                    "base_score": 68.0,
                    "community_score": points,
                    "published_at": pub_iso,
                    "discussion_urls": [hn_item_url],
                    "raw_metadata": {"points": points, "num_comments": comments, "author": hit.get("author")}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, error_message=str(e))
        
    return items

# -------------------------------------------------------------
# MAIN ORCHESTRATOR
# -------------------------------------------------------------

async def fetch_all_and_store() -> Dict[str, Any]:
    """
    Executes the ingestion pipeline across both Track 1 (Radar Diffs) and Track 2 (Curated News).
    Preserves versioned observations and updates feed items.
    """
    database.init_db()
    cfg = load_sources_config()
    settings = cfg.get("settings", {})
    custom_ua = settings.get("user_agent", "AI-Radar/1.0 (+https://github.com/Spidey6978/ai-radar)")
    token_env = settings.get("github_token_env", "GITHUB_TOKEN")
    gh_token = os.environ.get(token_env, "")
    
    sources_list = cfg.get("sources", [])
    
    # Register all sources in SQLite
    for s in sources_list:
        database.register_source(
            source_id=s["id"],
            name=s["name"],
            tier=s.get("evidence_tier", s.get("layer", "secondary_press")),
            endpoint_url=s["endpoint"],
            track=s.get("track", "curated_news")
        )

    limits = httpx.Limits(max_keepalive_connections=15, max_connections=30)
    tasks = []
    
    async with httpx.AsyncClient(limits=limits, follow_redirects=True) as client:
        for s in sources_list:
            s_id = s["id"]
            if s_id == "openrouter_models":
                tasks.append(watch_openrouter_models(client, s, custom_ua))
            elif s_id == "litellm_prices":
                tasks.append(watch_litellm_prices(client, s, custom_ua))
            elif s_id.startswith("github_"):
                tasks.append(fetch_github_releases(client, s, custom_ua, gh_token))
            elif s_id == "arxiv_ai":
                tasks.append(fetch_arxiv_ai(client, s, custom_ua))
            elif s_id == "cisa_kev":
                tasks.append(fetch_cisa_security(client, s, custom_ua))
            elif s_id == "hn_algolia":
                tasks.append(fetch_hn_algolia(client, s, custom_ua))
            else:
                tasks.append(fetch_rss_news(client, s, custom_ua))
                
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)
        elif isinstance(r, Exception):
            logger.error(f"Adapter exception: {r}")

    obs_saved = 0
    items_upserted = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for item in all_items:
        canonical_url = item["canonical_url"]
        if not canonical_url:
            continue
            
        content_hash = compute_hash(item["title"] + " " + item.get("summary", ""))
        obs_record = {
            "source_id": item["source_id"],
            "source_native_id": item["source_native_id"],
            "canonical_url": canonical_url,
            "raw_title": item["title"],
            "raw_content": item.get("summary", ""),
            "content_hash": content_hash,
            "published_at": item.get("published_at", now_iso),
            "first_seen_at": now_iso,
            "raw_metadata": item.get("raw_metadata", {})
        }
        obs_id, is_new = database.save_observation(obs_record)
        if is_new:
            obs_saved += 1
            
        feed_item_id = make_id("item", canonical_url)
        feed_record = {
            "id": feed_item_id,
            "canonical_url": canonical_url,
            "primary_observation_id": obs_id,
            "source_name": item["source_name"],
            "title": item["title"],
            "clean_summary": item.get("summary", ""),
            "primary_type": item["primary_type"],
            "evidence_tier": item["evidence_tier"],
            "category": item["category"],
            "track": item.get("track", "curated_news"),
            "base_score": item["base_score"],
            "community_score": item.get("community_score", 0),
            "discussion_urls": item.get("discussion_urls", []),
            "evidence_diff": item.get("evidence_diff"),
            "published_at": item.get("published_at", now_iso)
        }
        database.upsert_feed_item(feed_record)
        items_upserted += 1

    database.recalculate_freshness()

    return {
        "status": "success",
        "observations_saved": obs_saved,
        "items_upserted": items_upserted,
        "total_fetched": len(all_items),
        "timestamp": now_iso
    }

if __name__ == "__main__":
    print("Testing resilient multi-track fetcher...")
    res = asyncio.run(fetch_all_and_store())
    print("Fetch complete:", json.dumps(res, indent=2))
