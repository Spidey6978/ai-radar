import asyncio
import hashlib
import re
import html
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import feedparser
import httpx

# User-Agent to ensure Reddit and Google News allow requests
CUSTOM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 AI-Radar/1.0"
}

def clean_html(raw_html: str) -> str:
    """Strip HTML tags and unescape entities for clean summaries."""
    if not raw_html:
        return ""
    clean_text = re.sub(r"<[^>]+>", " ", raw_html)
    clean_text = html.unescape(clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    return clean_text[:300] + ("..." if len(clean_text) > 300 else "")

def make_id(url: str, title: str) -> str:
    seed = f"{url}:{title}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:16]

def classify_and_score(title: str, summary: str, source: str) -> tuple[str, str, int]:
    """
    Classifies the story into our 5 core domains, assigns a credibility badge,
    and calculates an initial priority score.
    """
    text = (title + " " + summary).lower()
    
    # 1. Bizarre & Anomalies
    bizarre_keywords = [
        "satellite", "takeover", "rogue", "drift", "anomaly", "anomalies",
        "secret meetup", "incident", "bizarre", "hallucination", "injection",
        "kurzgesagt", "escaped", "jailbreak", "autonomous loop", "disaster",
        "drone", "weapon", "misfire"
    ]
    if any(k in text for k in bizarre_keywords):
        category = "Bizarre & Anomalies"
        credibility = "🔴 Fringe / Wild" if "secret" in text or "takeover" in text else "🟡 Developing"
        score = 85
        return category, credibility, score

    # 2. Whisper Net & Leaks
    leak_keywords = [
        "leak", "leaked", "leaks", "rumor", "unannounced", "mystery",
        "lmsys", "arena", "argon", "astra", "gpt-6", "orion", "strawberry",
        "checkpoint", "weights dumped", "codenamed", "whisper"
    ]
    if any(k in text for k in leak_keywords):
        category = "Whisper Net & Leaks"
        credibility = "🟡 Credible Intel" if "lmsys" in text or "bench" in text else "🔴 Unverified / Rumor"
        score = 90
        return category, credibility, score

    # 3. IDEs & Model Drops (The Meta Layer)
    meta_keywords = [
        "antigravity", "agy", "cursor", "claude code", "gemini 3", "gemini pro",
        "removed", "deprecated", "pricing cut", "context window", "system prompt",
        "copilot", "windsurf", "api key", "rate limit", "token limit", "model drop"
    ]
    if any(k in text for k in meta_keywords):
        category = "IDEs & Model Drops"
        credibility = "🟢 Confirmed" if "release" in text or "github" in text or "blog" in source.lower() else "🟡 Developing"
        score = 95
        return category, credibility, score

    # 4. Power & Backroom Deals
    power_keywords = [
        "handshake", "deal", "partnership", "allian", "antitrust", "pentagon",
        "military", "nuclear", "power plant", "datacenter", "invest", "acquisition",
        "altman", "hassabis", "amodei", "microsoft", "google and openai"
    ]
    if any(k in text for k in power_keywords):
        category = "Power & Lab Deals"
        credibility = "🟢 Confirmed" if "announces" in text or "deal" in text else "🟡 Developing"
        score = 80
        return category, credibility, score

    # 5. Open Source & Local Frontier
    open_keywords = [
        "localllama", "ollama", "vllm", "gguf", "exl2", "fine-tune", "quant",
        "hugging face", "open weight", "deepseek", "qwen", "mistral", "llama"
    ]
    if any(k in text for k in open_keywords) or "LocalLLaMA" in source:
        category = "Open Source Frontier"
        credibility = "🟢 Confirmed" if "release" in text or "weights" in text else "🟡 Community Intel"
        score = 75
        return category, credibility, score

    return "General AI", "🟡 Community Intel", 60

async def fetch_hacker_news(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Fetch recent high-signal tech discussions from Hacker News via Algolia API."""
    queries = [
        "Antigravity AI",
        "Gemini 3 Pro",
        "OpenAI secret",
        "AI satellite takeover",
        "LMSYS arena mystery",
        "Claude Code removed",
        "Argon AI Astra"
    ]
    
    articles = []
    for q in queries:
        url = f"https://hn.algolia.com/api/v1/search?query={q}&tags=story&hitsPerPage=10"
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                for hit in data.get("hits", []):
                    title = hit.get("title") or ""
                    article_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                    hn_item_url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                    points = hit.get("points") or 0
                    created_at = hit.get("created_at")
                    
                    category, credibility, score = classify_and_score(title, "", "Hacker News")
                    
                    articles.append({
                        "id": make_id(article_url, title),
                        "title": title,
                        "url": article_url,
                        "source": "Hacker News",
                        "source_type": "discussion",
                        "category": category,
                        "credibility": credibility,
                        "summary": f"Discussion with {hit.get('num_comments', 0)} comments and {points} upvotes on Hacker News.",
                        "discussion_url": hn_item_url,
                        "score": points + score,
                        "published_at": created_at or datetime.now(timezone.utc).isoformat()
                    })
        except Exception as e:
            print(f"[HN] Error querying '{q}': {e}")
            
    return articles

async def fetch_reddit_feeds(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Fetch latest discussions from relevant subreddits using RSS feeds."""
    subreddits = [
        ("r/LocalLLaMA", "https://www.reddit.com/r/LocalLLaMA/hot/.rss?limit=15"),
        ("r/singularity", "https://www.reddit.com/r/singularity/hot/.rss?limit=15"),
        ("r/MachineLearning", "https://www.reddit.com/r/MachineLearning/new/.rss?limit=15")
    ]
    
    articles = []
    for sub_name, feed_url in subreddits:
        try:
            resp = await client.get(feed_url, headers=CUSTOM_HEADERS, timeout=12.0)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.text)
                for entry in feed.entries:
                    title = entry.get("title", "")
                    url = entry.get("link", "")
                    raw_summary = entry.get("summary", "")
                    clean_sum = clean_html(raw_summary)
                    pub_date = entry.get("published", "")
                    
                    category, credibility, score = classify_and_score(title, clean_sum, sub_name)
                    
                    articles.append({
                        "id": make_id(url, title),
                        "title": title,
                        "url": url,
                        "source": sub_name,
                        "source_type": "reddit",
                        "category": category,
                        "credibility": credibility,
                        "summary": clean_sum or f"Direct dispatch from {sub_name}",
                        "discussion_url": url,
                        "score": score + 10,
                        "published_at": pub_date or datetime.now(timezone.utc).isoformat()
                    })
        except Exception as e:
            print(f"[Reddit] Error fetching {sub_name}: {e}")
            
    return articles

async def fetch_google_news_rss(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Fetch targeted Google News RSS feeds for niche AI topics and leaks."""
    queries = [
        '("Antigravity" OR "Claude Code" OR "Gemini removed") AND (AI OR IDE)',
        '("Argon AI" OR "GPT-6 Astra" OR "mystery model" OR "LMSYS") AND AI',
        '("AI satellite" OR "AI takeover" OR "secret AI meetup")',
        '("Google" AND "OpenAI" AND ("handshake" OR "deal" OR "alliance"))'
    ]
    
    articles = []
    for q in queries:
        encoded_q = httpx.URL("", params={"q": q}).query.decode("utf-8")
        feed_url = f"https://news.google.com/rss/search?{encoded_q}&hl=en-US&gl=US&ceid=US:en"
        try:
            resp = await client.get(feed_url, headers=CUSTOM_HEADERS, timeout=12.0)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:8]:
                    title = entry.get("title", "")
                    url = entry.get("link", "")
                    pub_date = entry.get("published", "")
                    raw_summary = entry.get("summary", "")
                    clean_sum = clean_html(raw_summary)
                    
                    # Extract publisher from title if format is "Title - Publisher"
                    source_name = "Google News"
                    if " - " in title:
                        parts = title.rsplit(" - ", 1)
                        title = parts[0]
                        source_name = parts[1]
                        
                    category, credibility, score = classify_and_score(title, clean_sum, source_name)
                    
                    articles.append({
                        "id": make_id(url, title),
                        "title": title,
                        "url": url,
                        "source": source_name,
                        "source_type": "news",
                        "category": category,
                        "credibility": credibility,
                        "summary": clean_sum or f"Reported by {source_name}",
                        "discussion_url": None,
                        "score": score,
                        "published_at": pub_date or datetime.now(timezone.utc).isoformat()
                    })
        except Exception as e:
            print(f"[Google News] Error fetching query '{q}': {e}")
            
    return articles

async def fetch_specialized_blogs(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Fetch from specialized blogs and engineering trackers."""
    blogs = [
        ("Simon Willison Weblog", "https://simonwillison.net/atom/everything/"),
        ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml")
    ]
    
    articles = []
    for name, feed_url in blogs:
        try:
            resp = await client.get(feed_url, headers=CUSTOM_HEADERS, timeout=12.0)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:6]:
                    title = entry.get("title", "")
                    url = entry.get("link", "")
                    pub_date = entry.get("published", "")
                    raw_sum = entry.get("summary", "")
                    clean_sum = clean_html(raw_sum)
                    
                    category, _, score = classify_and_score(title, clean_sum, name)
                    
                    articles.append({
                        "id": make_id(url, title),
                        "title": title,
                        "url": url,
                        "source": name,
                        "source_type": "blog",
                        "category": category,
                        "credibility": "🟢 Confirmed Source",
                        "summary": clean_sum,
                        "discussion_url": None,
                        "score": score + 20,
                        "published_at": pub_date or datetime.now(timezone.utc).isoformat()
                    })
        except Exception as e:
            print(f"[Blog] Error fetching {name}: {e}")
            
    return articles

async def fetch_all_intel() -> List[Dict[str, Any]]:
    """Runs all async fetchers concurrently and deduplicates results."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            fetch_hacker_news(client),
            fetch_reddit_feeds(client),
            fetch_google_news_rss(client),
            fetch_specialized_blogs(client),
            return_exceptions=True
        )
        
    all_articles = []
    for res in results:
        if isinstance(res, list):
            all_articles.extend(res)
        elif isinstance(res, Exception):
            print(f"Fetcher error occurred: {res}")
            
    # Deduplicate by clean URL or identical title
    seen_urls = set()
    seen_titles = set()
    deduped = []
    
    for art in all_articles:
        url_key = art["url"].split("?")[0].rstrip("/")
        norm_title = re.sub(r"[^a-zA-Z0-9]", "", art["title"].lower())
        
        if url_key in seen_urls or norm_title in seen_titles:
            continue
            
        seen_urls.add(url_key)
        seen_titles.add(norm_title)
        deduped.append(art)
        
    # Sort by score and recency
    deduped.sort(key=lambda x: x.get("score", 0), reverse=True)
    return deduped

if __name__ == "__main__":
    import sys
    # Ensure stdout handles unicode characters on Windows
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')

    from database import init_db, save_articles, get_articles
    init_db()
    print("Running test fetch...")
    items = asyncio.run(fetch_all_intel())
    print(f"Fetched {len(items)} unique items across all channels.")
    saved = save_articles(items)
    print(f"Saved {saved} new items to radar.db.")
    
    print("\nSample top 5 items:")
    for i, it in enumerate(items[:5], 1):
        print(f"[{i}] [{it['category']}] ({it['credibility']}) {it['title']} ({it['source']})")
