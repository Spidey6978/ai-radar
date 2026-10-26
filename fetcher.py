import asyncio
import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import feedparser
import httpx
import database

CUSTOM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 AI-Radar/1.0"
}

# Optional GitHub Token to bypass public API rate limits (60/hr -> 5000/hr)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "ref_src", "feature"
}

def normalize_url(url: str) -> str:
    """Removes marketing trackers, trailing slashes, and fragments to ensure true canonical URL."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        # Parse and filter query parameters
        query_dict = parse_qs(parsed.query, keep_blank_values=False)
        cleaned_query = {k: v for k, v in query_dict.items() if k.lower() not in TRACKING_PARAMS}
        # Re-encode query
        new_query = urlencode(cleaned_query, doseq=True)
        # Clean path
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

def clean_text(raw_html: str, max_len: int = 320) -> str:
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

def classify_category(title: str, summary: str, source_tier: str) -> str:
    """Determines topical domain independently from evidence level."""
    text = (title + " " + summary).lower()
    
    if any(k in text for k in ["antigravity", "agy", "cursor", "claude code", "copilot", "windsurf", "deprecated", "model drop", "token limit", "pricing cut", "vllm", "ollama", "release", "v0.", "v1."]):
        if any(k in text for k in ["vllm", "ollama", "gguf", "exl2", "fine-tune", "localllama", "weights dumped", "hugging face"]):
            return "Open Source Frontier"
        return "IDEs & Model Drops"

    if any(k in text for k in ["leak", "mystery", "lmsys", "arena", "argon", "astra", "gpt-6", "checkpoint", "whisper"]):
        return "Whisper Net & Leaks"

    if any(k in text for k in ["satellite", "anomaly", "rogue", "drift", "secret meetup", "incident", "hallucination", "injection", "kurzgesagt", "cve-", "vulnerability"]):
        return "Bizarre & Anomalies"

    if any(k in text for k in ["handshake", "alliance", "antitrust", "nuclear", "datacenter", "pentagon", "acquisition", "invest", "google and openai"]):
        return "Power & Lab Deals"

    if any(k in text for k in ["localllama", "hugging face", "open weight", "deepseek", "qwen", "mistral", "quant"]):
        return "Open Source Frontier"

    return "General AI"

# -------------------------------------------------------------
# ADAPTERS
# -------------------------------------------------------------

async def fetch_github_releases(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Layer A: Authoritative GitHub Releases for critical developer stacks."""
    repos = [
        ("ollama/ollama", "Ollama Engine"),
        ("vllm-project/vllm", "vLLM Inference"),
        ("huggingface/transformers", "HF Transformers"),
        ("BerriAI/litellm", "LiteLLM Proxy"),
        ("anthropics/anthropic-sdk-python", "Anthropic SDK")
    ]
    
    headers = dict(CUSTOM_HEADERS)
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
        
    items = []
    for repo, display_name in repos:
        source_id = f"github:{repo.replace('/', '_')}"
        database.register_source(
            source_id=source_id,
            name=f"GitHub Releases: {display_name}",
            tier="primary_technical",
            endpoint_url=f"https://api.github.com/repos/{repo}/releases"
        )
        try:
            url = f"https://api.github.com/repos/{repo}/releases?per_page=2"
            res = await client.get(url, headers=headers, timeout=10.0)
            database.update_source_health(source_id, res.status_code)
            
            if res.status_code == 200:
                releases = res.json()
                for rel in releases:
                    if rel.get("draft") or not rel.get("tag_name"):
                        continue
                    tag = rel.get("tag_name")
                    title = f"[{display_name}] {rel.get('name') or tag} Released"
                    body = clean_text(rel.get("body", ""), max_len=300)
                    html_url = normalize_url(rel.get("html_url", ""))
                    published_at = rel.get("published_at", "")
                    
                    items.append({
                        "source_id": source_id,
                        "source_name": f"GitHub ({repo})",
                        "source_native_id": str(rel.get("id")),
                        "canonical_url": html_url,
                        "title": title,
                        "summary": body or f"Official release {tag} published for {repo}.",
                        "primary_type": "release",
                        "evidence_tier": "verified_artifact",
                        "category": classify_category(title, body, "primary_technical"),
                        "base_score": 95.0,
                        "published_at": published_at,
                        "discussion_urls": [html_url],
                        "raw_metadata": {"tag": tag, "author": rel.get("author", {}).get("login")}
                    })
        except Exception as e:
            database.update_source_health(source_id, 500, str(e))
            
    return items

async def fetch_arxiv_preprints(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Layer A: arXiv AI preprints (cs.AI & cs.CL) for primary research disclosures."""
    source_id = "arxiv:ai_cl"
    endpoint = "https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.CL&sortBy=submittedDate&sortOrder=descending&max_results=8"
    database.register_source(
        source_id=source_id,
        name="arXiv AI / Computation and Language",
        tier="research",
        endpoint_url=endpoint
    )
    
    items = []
    try:
        res = await client.get(endpoint, headers=CUSTOM_HEADERS, timeout=12.0)
        database.update_source_health(source_id, res.status_code)
        if res.status_code == 200:
            feed = feedparser.parse(res.text)
            for entry in feed.entries:
                raw_title = clean_text(entry.title).replace("\n", " ")
                summary = clean_text(entry.summary, max_len=300)
                link = normalize_url(entry.link)
                pub_date = entry.get("published", "")
                
                items.append({
                    "source_id": source_id,
                    "source_name": "arXiv",
                    "source_native_id": entry.id,
                    "canonical_url": link,
                    "title": f"[Paper] {raw_title}",
                    "summary": summary,
                    "primary_type": "research",
                    "evidence_tier": "technical_paper",
                    "category": classify_category(raw_title, summary, "research"),
                    "base_score": 85.0,
                    "published_at": pub_date,
                    "discussion_urls": [link],
                    "raw_metadata": {"authors": [a.get("name") for a in entry.get("authors", [])]}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, str(e))
        
    return items

async def fetch_cisa_security(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Layer C: CISA KEV for critical systemic security vulnerabilities."""
    source_id = "cisa:kev"
    endpoint = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    database.register_source(
        source_id=source_id,
        name="CISA Known Exploited Vulnerabilities",
        tier="security",
        endpoint_url=endpoint
    )
    
    items = []
    try:
        res = await client.get(endpoint, headers=CUSTOM_HEADERS, timeout=12.0)
        database.update_source_health(source_id, res.status_code)
        if res.status_code == 200:
            data = res.json()
            vulns = data.get("vulnerabilities", [])
            # Look at the most recent 5 entries added
            recent_vulns = vulns[-5:] if len(vulns) >= 5 else vulns
            for v in reversed(recent_vulns):
                cve_id = v.get("cveID")
                vendor = v.get("vendorProject")
                product = v.get("product")
                desc = clean_text(v.get("shortDescription", ""), max_len=280)
                date_added = v.get("dateAdded", "")
                nvd_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
                
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
                    "base_score": 90.0,
                    "published_at": date_added,
                    "discussion_urls": [nvd_url],
                    "raw_metadata": {"cve": cve_id, "vendor": vendor, "product": product}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, str(e))
        
    return items

async def fetch_hacker_news(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Layer B: Hacker News via Algolia search for early developer discussions."""
    source_id = "hn:algolia"
    endpoint = "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=LLM+OR+AI+OR+Claude+OR+Gemini+OR+OpenAI&hitsPerPage=20"
    database.register_source(
        source_id=source_id,
        name="Hacker News (Algolia AI Index)",
        tier="discovery",
        endpoint_url=endpoint
    )
    
    items = []
    try:
        res = await client.get(endpoint, headers=CUSTOM_HEADERS, timeout=10.0)
        database.update_source_health(source_id, res.status_code)
        if res.status_code == 200:
            hits = res.json().get("hits", [])
            for hit in hits:
                story_id = str(hit.get("objectID"))
                title = clean_text(hit.get("title", ""))
                if not title or len(title) < 10:
                    continue
                raw_url = hit.get("url")
                hn_item_url = f"https://news.ycombinator.com/item?id={story_id}"
                canonical_url = normalize_url(raw_url) if raw_url else hn_item_url
                
                points = hit.get("points") or 0
                comments = hit.get("num_comments") or 0
                created_at = hit.get("created_at") or ""
                
                # Check for discussion text if self post
                story_text = clean_text(hit.get("story_text", ""), max_len=280)
                summary = story_text or f"Hacker News technical thread by {hit.get('author')}. ({points} points, {comments} comments)."
                
                items.append({
                    "source_id": source_id,
                    "source_name": "Hacker News",
                    "source_native_id": story_id,
                    "canonical_url": canonical_url,
                    "title": title,
                    "summary": summary,
                    "primary_type": "community_lead",
                    "evidence_tier": "community_claim",
                    "category": classify_category(title, summary, "discovery"),
                    "base_score": 70.0,
                    "community_score": points,
                    "published_at": created_at,
                    "discussion_urls": [hn_item_url],
                    "raw_metadata": {"points": points, "num_comments": comments, "author": hit.get("author")}
                })
    except Exception as e:
        database.update_source_health(source_id, 500, str(e))
        
    return items

async def fetch_reddit(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Layer B: Community subreddits (LocalLLaMA, MachineLearning, singularity)."""
    subreddits = ["LocalLLaMA", "MachineLearning", "singularity"]
    items = []
    
    # Reddit guidelines require descriptive User-Agent with contact/bot name
    reddit_headers = {
        "User-Agent": "desktop:com.airadar.aggregator:v1.0 (by /u/airadar_bot)"
    }
    
    for i, sub in enumerate(subreddits):
        if i > 0:
            await asyncio.sleep(1.2)  # Polite pacing to respect Reddit rate limits
        source_id = f"reddit:{sub.lower()}"
        endpoint = f"https://www.reddit.com/r/{sub}/new/.rss"
        database.register_source(
            source_id=source_id,
            name=f"Reddit: r/{sub}",
            tier="discovery",
            endpoint_url=endpoint
        )
        try:
            res = await client.get(endpoint, headers=reddit_headers, timeout=10.0)
            database.update_source_health(source_id, res.status_code)
            if res.status_code == 200:
                feed = feedparser.parse(res.text)
                for entry in feed.entries[:8]:
                    raw_title = clean_text(entry.title)
                    summary = clean_text(entry.get("summary", "") or entry.get("description", ""), max_len=280)
                    reddit_link = normalize_url(entry.link)
                    pub_date = entry.get("updated") or entry.get("published", "")
                    
                    items.append({
                        "source_id": source_id,
                        "source_name": f"r/{sub}",
                        "source_native_id": entry.id,
                        "canonical_url": reddit_link,
                        "title": raw_title,
                        "summary": summary or f"Discussion posted in r/{sub}.",
                        "primary_type": "community_lead",
                        "evidence_tier": "community_claim",
                        "category": classify_category(raw_title, summary, "discovery"),
                        "base_score": 65.0,
                        "published_at": pub_date,
                        "discussion_urls": [reddit_link],
                        "raw_metadata": {"author": entry.get("author", "")}
                    })
        except Exception as e:
            database.update_source_health(source_id, 500, str(e))
            
    return items

async def fetch_curated_blogs(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Layer B: Curated engineering blogs for prompt injection & frontier notes."""
    blogs = [
        ("Simon Willison", "https://simonwillison.net/atom/everything/", "blog:simon_willison"),
        ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", "blog:hf")
    ]
    items = []
    for name, feed_url, source_id in blogs:
        database.register_source(
            source_id=source_id,
            name=f"Engineering Blog: {name}",
            tier="discovery",
            endpoint_url=feed_url
        )
        try:
            res = await client.get(feed_url, headers=CUSTOM_HEADERS, timeout=10.0)
            database.update_source_health(source_id, res.status_code)
            if res.status_code == 200:
                feed = feedparser.parse(res.text)
                for entry in feed.entries[:5]:
                    title = clean_text(entry.title)
                    raw_sum = entry.get("summary") or entry.get("description") or ""
                    summary = clean_text(raw_sum, max_len=300)
                    link = normalize_url(entry.link)
                    pub_date = entry.get("published") or entry.get("updated", "")
                    
                    items.append({
                        "source_id": source_id,
                        "source_name": name,
                        "source_native_id": entry.id,
                        "canonical_url": link,
                        "title": title,
                        "summary": summary,
                        "primary_type": "news",
                        "evidence_tier": "verified_artifact" if "Hugging Face" in name else "community_claim",
                        "category": classify_category(title, summary, "discovery"),
                        "base_score": 80.0,
                        "published_at": pub_date,
                        "discussion_urls": [link],
                        "raw_metadata": {"feed": name}
                    })
        except Exception as e:
            database.update_source_health(source_id, 500, str(e))
            
    return items

async def fetch_google_news(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Layer B: Targeted Google News RSS net for specific leaks & shifts."""
    queries = [
        ("AI model removed OR deprecated", "gnews:model_deprecated"),
        ("AI satellite takeover OR anomaly", "gnews:satellite_anomaly"),
        ("OpenAI handshake OR alliance", "gnews:lab_alliances")
    ]
    items = []
    for q, source_id in queries:
        endpoint = f"https://news.google.com/rss/search?q={q.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
        database.register_source(
            source_id=source_id,
            name=f"Google News: {q[:24]}...",
            tier="discovery",
            endpoint_url=endpoint
        )
        try:
            res = await client.get(endpoint, headers=CUSTOM_HEADERS, timeout=10.0)
            database.update_source_health(source_id, res.status_code)
            if res.status_code == 200:
                feed = feedparser.parse(res.text)
                for entry in feed.entries[:5]:
                    title = clean_text(entry.title)
                    summary = clean_text(entry.summary, max_len=260)
                    link = normalize_url(entry.link)
                    pub_date = entry.get("published", "")
                    
                    items.append({
                        "source_id": source_id,
                        "source_name": "Google News RSS",
                        "source_native_id": entry.id,
                        "canonical_url": link,
                        "title": title,
                        "summary": summary,
                        "primary_type": "news",
                        "evidence_tier": "secondary_press",
                        "category": classify_category(title, summary, "discovery"),
                        "base_score": 55.0,
                        "published_at": pub_date,
                        "discussion_urls": [link],
                        "raw_metadata": {"query": q}
                    })
        except Exception as e:
            database.update_source_health(source_id, 500, str(e))
            
    return items

# -------------------------------------------------------------
# ORCHESTRATOR
# -------------------------------------------------------------

async def fetch_all_and_store() -> Dict[str, Any]:
    """Runs all source adapters concurrently, preserves raw observations, and upserts feed items."""
    database.init_db()
    
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)
    async with httpx.AsyncClient(headers=CUSTOM_HEADERS, limits=limits, follow_redirects=True) as client:
        results = await asyncio.gather(
            fetch_github_releases(client),
            fetch_arxiv_preprints(client),
            fetch_cisa_security(client),
            fetch_hacker_news(client),
            fetch_reddit(client),
            fetch_curated_blogs(client),
            fetch_google_news(client),
            return_exceptions=True
        )

    all_raw_items: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, list):
            all_raw_items.extend(r)
        elif isinstance(r, Exception):
            print(f"Adapter raised exception: {r}")

    observations_count = 0
    items_upserted = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for item in all_raw_items:
        canonical_url = item["canonical_url"]
        if not canonical_url:
            continue
            
        content_hash = compute_hash(item["title"] + " " + item.get("summary", ""))
        obs_id = make_id(item["source_id"], item["source_native_id"])
        
        # 1. Save observation
        obs_record = {
            "id": obs_id,
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
        _, is_new_obs = database.save_observation(obs_record)
        if is_new_obs:
            observations_count += 1
            
        # 2. Upsert feed item
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
            "base_score": item["base_score"],
            "community_score": item.get("community_score", 0),
            "discussion_urls": item.get("discussion_urls", []),
            "published_at": item.get("published_at", now_iso)
        }
        database.upsert_feed_item(feed_record)
        items_upserted += 1

    # Recalculate exponential decay across all active feed items
    database.recalculate_freshness()

    return {
        "observations_saved": observations_count,
        "items_upserted": items_upserted,
        "total_fetched": len(all_raw_items),
        "timestamp": now_iso
    }

if __name__ == "__main__":
    print("Running test ingestion...")
    stats = asyncio.run(fetch_all_and_store())
    print("Ingestion complete:", json.dumps(stats, indent=2))
