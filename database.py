import email.utils
import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

# On Vercel serverless, filesystem outside /tmp is read-only scratch space
if os.environ.get("VERCEL"):
    DB_PATH = Path("/tmp") / "radar.db"
else:
    DB_PATH = Path(__file__).parent / "radar.db"

def parse_to_utc_iso(date_val: Any) -> str:
    """Robust parser that converts RFC822, ISO8601, and datetime objects into valid UTC ISO format."""
    now_utc = datetime.now(timezone.utc)
    if not date_val:
        return now_utc.isoformat()
    if isinstance(date_val, datetime):
        if date_val.tzinfo is None:
            date_val = date_val.replace(tzinfo=timezone.utc)
        return date_val.astimezone(timezone.utc).isoformat()
    val_str = str(date_val).strip()
    try:
        # Try ISO format
        if "T" in val_str or (len(val_str) >= 10 and val_str[4] == '-' and val_str[7] == '-'):
            clean_iso = val_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        # Try RFC822 format (e.g. Google News, Atom/RSS dates)
        dt = email.utils.parsedate_to_datetime(val_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    return now_utc.isoformat()

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Sources health & registry
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tier TEXT NOT NULL,
            track TEXT DEFAULT 'curated_news',
            endpoint_url TEXT NOT NULL,
            etag TEXT,
            last_modified TEXT,
            is_active INTEGER DEFAULT 1,
            last_checked_at TEXT,
            last_success_at TEXT,
            last_status_code INTEGER,
            items_found_last_run INTEGER DEFAULT 0,
            error_message TEXT
        )
    """)

    # 2. Raw observations (versioned & immutable)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources(id),
            source_native_id TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            raw_title TEXT NOT NULL,
            raw_content TEXT,
            content_hash TEXT NOT NULL,
            published_at TEXT,
            first_seen_at TEXT NOT NULL,
            raw_metadata TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_obs_canonical_url ON observations(canonical_url)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_obs_source ON observations(source_id)")

    # 3. Snapshots table for tracking full state of watched artifacts (OpenRouter, LiteLLM)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL REFERENCES sources(id),
            content_hash TEXT NOT NULL,
            data_json TEXT NOT NULL,
            observed_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_source_time ON snapshots(source_id, observed_at DESC)")

    # 4. Verified Events table (The True Radar Layer for Diffs & Drops)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            title TEXT NOT NULL,
            what_changed TEXT NOT NULL,
            evidence_diff TEXT,
            evidence_tier TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            track TEXT DEFAULT 'radar_signal',
            published_at TEXT NOT NULL,
            first_seen_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_published ON events(published_at DESC)")

    # 5. Normalized Feed Items
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feed_items (
            id TEXT PRIMARY KEY,
            canonical_url TEXT UNIQUE NOT NULL,
            primary_observation_id TEXT NOT NULL REFERENCES observations(id),
            source_name TEXT NOT NULL,
            title TEXT NOT NULL,
            clean_summary TEXT NOT NULL,
            primary_type TEXT NOT NULL,
            evidence_tier TEXT NOT NULL,
            category TEXT NOT NULL,
            track TEXT DEFAULT 'curated_news',
            base_score REAL DEFAULT 0.0,
            freshness_score REAL DEFAULT 0.0,
            community_score INTEGER DEFAULT 0,
            discussion_urls TEXT DEFAULT '[]',
            evidence_diff TEXT,
            is_read INTEGER DEFAULT 0,
            is_bookmarked INTEGER DEFAULT 0,
            is_dismissed INTEGER DEFAULT 0,
            published_at TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # Migration checks for existing databases before creating indexes
    cols_to_add = [
        ("sources", "track", "TEXT DEFAULT 'curated_news'"),
        ("sources", "etag", "TEXT"),
        ("sources", "last_modified", "TEXT"),
        ("sources", "items_found_last_run", "INTEGER DEFAULT 0"),
        ("feed_items", "track", "TEXT DEFAULT 'curated_news'"),
        ("feed_items", "evidence_diff", "TEXT")
    ]
    for table, col, col_type in cols_to_add:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except Exception:
            pass  # Already exists

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_scores ON feed_items(freshness_score DESC, published_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_category ON feed_items(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_evidence ON feed_items(evidence_tier)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_track ON feed_items(track)")

    # 6. SQLite FTS5 Virtual Table
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS feed_items_fts USING fts5(
            id UNINDEXED,
            title,
            clean_summary,
            source_name,
            category
        )
    """)

    # Triggers for FTS
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_feed_items_ai AFTER INSERT ON feed_items BEGIN
            INSERT INTO feed_items_fts (id, title, clean_summary, source_name, category)
            VALUES (new.id, new.title, new.clean_summary, new.source_name, new.category);
        END;
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_feed_items_ad AFTER DELETE ON feed_items BEGIN
            DELETE FROM feed_items_fts WHERE id = old.id;
        END;
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_feed_items_au AFTER UPDATE ON feed_items BEGIN
            UPDATE feed_items_fts 
            SET title = new.title, 
                clean_summary = new.clean_summary, 
                source_name = new.source_name, 
                category = new.category
            WHERE id = old.id;
        END;
    """)

    # 7. Feedback table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact TEXT,
            suggestion TEXT NOT NULL,
            willing_to_pay TEXT,
            pay_amount TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()

# -------------------------------------------------------------
# SOURCE HEALTH & REGISTRY
# -------------------------------------------------------------

def register_source(source_id: str, name: str, tier: str, endpoint_url: str, track: str = "curated_news"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sources (id, name, tier, endpoint_url, track)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            tier = excluded.tier,
            endpoint_url = excluded.endpoint_url,
            track = excluded.track
    """, (source_id, name, tier, endpoint_url, track))
    conn.commit()
    conn.close()

def update_source_health(
    source_id: str, 
    status_code: int, 
    items_count: int = 0, 
    etag: Optional[str] = None, 
    last_modified: Optional[str] = None, 
    error_message: Optional[str] = None
):
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    
    updates = [
        "last_checked_at = ?",
        "last_status_code = ?",
        "items_found_last_run = ?"
    ]
    params = [now, status_code, items_count]
    
    if status_code in (200, 304):
        updates.append("last_success_at = ?")
        updates.append("error_message = NULL")
        params.append(now)
    else:
        updates.append("error_message = ?")
        params.append(error_message)
        
    if etag:
        updates.append("etag = ?")
        params.append(etag)
    if last_modified:
        updates.append("last_modified = ?")
        params.append(last_modified)
        
    params.append(source_id)
    cursor.execute(f"UPDATE sources SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()

def get_source_conditional_headers(source_id: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (etag, last_modified) for conditional GETs."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT etag, last_modified FROM sources WHERE id = ?", (source_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row["etag"], row["last_modified"]
    return None, None

def get_sources_health() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sources ORDER BY track, tier, name")
    rows = cursor.fetchall()
    results = [dict(row) for row in rows]
    conn.close()
    return results

# -------------------------------------------------------------
# SNAPSHOTS & EVENTS (RADAR SIGNALS)
# -------------------------------------------------------------

def save_snapshot(source_id: str, content_hash: str, data_json: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute("""
        INSERT INTO snapshots (source_id, content_hash, data_json, observed_at)
        VALUES (?, ?, ?, ?)
    """, (source_id, content_hash, data_json, now))
    snap_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return snap_id

def get_latest_snapshot(source_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM snapshots WHERE source_id = ? ORDER BY observed_at DESC LIMIT 1", (source_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def create_event(
    event_id: str,
    event_type: str,
    entity_name: str,
    title: str,
    what_changed: str,
    evidence_diff: str,
    evidence_tier: str,
    canonical_url: str,
    track: str = "radar_signal"
) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute("""
        INSERT INTO events 
        (id, event_type, entity_name, title, what_changed, evidence_diff, evidence_tier, canonical_url, track, published_at, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            what_changed = excluded.what_changed,
            evidence_diff = excluded.evidence_diff
    """, (event_id, event_type, entity_name, title, what_changed, evidence_diff, evidence_tier, canonical_url, track, now, now))
    conn.commit()
    conn.close()
    return event_id

# -------------------------------------------------------------
# OBSERVATIONS (IMMUTABLE LOG)
# -------------------------------------------------------------

def save_observation(obs: Dict[str, Any]) -> tuple[str, bool]:
    """
    Saves an immutable observation version.
    ID is derived from (source_id:source_native_id:content_hash).
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    source_id = obs["source_id"]
    native_id = str(obs.get("source_native_id", ""))
    content_hash = obs["content_hash"]
    
    # Versioned immutable ID
    version_seed = f"{source_id}:{native_id}:{content_hash}".encode("utf-8")
    obs_id = hashlib.sha256(version_seed).hexdigest()[:20]
    
    now = datetime.now(timezone.utc).isoformat()
    pub_iso = parse_to_utc_iso(obs.get("published_at"))
    metadata_json = json.dumps(obs.get("raw_metadata", {}))
    is_new = False
    
    try:
        cursor.execute("""
            INSERT INTO observations 
            (id, source_id, source_native_id, canonical_url, raw_title, raw_content, content_hash, published_at, first_seen_at, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            obs_id,
            source_id,
            native_id,
            obs["canonical_url"],
            obs["raw_title"],
            obs.get("raw_content", ""),
            content_hash,
            pub_iso,
            obs.get("first_seen_at", now),
            metadata_json
        ))
        is_new = True
    except sqlite3.IntegrityError:
        # Exact content version already archived
        is_new = False
    finally:
        conn.commit()
        conn.close()
        
    return obs_id, is_new

# -------------------------------------------------------------
# NORMALIZED FEED ITEMS & DYNAMIC FRESHNESS
# -------------------------------------------------------------

def compute_freshness_score(base_score: float, evidence_tier: str, published_at_iso: str, community_score: int = 0) -> float:
    """
    Computes dynamic query-time freshness:
    S(t) = (base_score + community_boost) * exp(-lambda * delta_hours)
    """
    half_lives = {
        "verified_artifact": 72.0,
        "technical_paper": 48.0,
        "security_advisory": 48.0,
        "community_claim": 20.0,
        "secondary_press": 20.0
    }
    half_life = half_lives.get(evidence_tier, 24.0)
    decay_lambda = math.log(2) / half_life
    
    try:
        clean_iso = published_at_iso.replace("Z", "+00:00")
        pub_dt = datetime.fromisoformat(clean_iso)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        delta_hours = max(0.0, (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600.0)
    except Exception:
        delta_hours = 0.0

    # Logarithmic community boost (max +12 pts so viral HN thread gets boost without breaking evidence hierarchy)
    comm_boost = min(12.0, math.log10(max(1, community_score) + 1) * 4.0)
    effective_base = base_score + comm_boost
    
    freshness = effective_base * math.exp(-decay_lambda * delta_hours)
    return round(freshness, 2)

def upsert_feed_item(item: Dict[str, Any]) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    canonical_url = item["canonical_url"]
    pub_iso = parse_to_utc_iso(item.get("published_at"))
    
    cursor.execute("SELECT id, discussion_urls, evidence_tier, base_score, community_score FROM feed_items WHERE canonical_url = ?", (canonical_url,))
    existing = cursor.fetchone()
    
    discussion_urls = item.get("discussion_urls", [])
    comm_score = item.get("community_score", 0)
    base_score = item.get("base_score", 50.0)
    tier = item.get("evidence_tier", "secondary_press")
    
    freshness = compute_freshness_score(base_score, tier, pub_iso, comm_score)
    
    if existing:
        item_id = existing["id"]
        try:
            curr_disc = json.loads(existing["discussion_urls"] or "[]")
        except Exception:
            curr_disc = []
        for d in discussion_urls:
            if d not in curr_disc:
                curr_disc.append(d)
                
        tiers_rank = {
            "verified_artifact": 5,
            "technical_paper": 4,
            "security_advisory": 4,
            "community_claim": 2,
            "secondary_press": 1
        }
        old_rank = tiers_rank.get(existing["evidence_tier"], 0)
        new_rank = tiers_rank.get(tier, 0)
        target_tier = tier if new_rank > old_rank else existing["evidence_tier"]
        target_base = max(existing["base_score"], base_score)
        target_comm = max(existing["community_score"], comm_score)
        
        cursor.execute("""
            UPDATE feed_items 
            SET discussion_urls = ?,
                evidence_tier = ?,
                base_score = ?,
                freshness_score = ?,
                community_score = ?
            WHERE id = ?
        """, (json.dumps(curr_disc), target_tier, target_base, freshness, target_comm, item_id))
    else:
        item_id = item["id"]
        cursor.execute("""
            INSERT INTO feed_items 
            (id, canonical_url, primary_observation_id, source_name, title, clean_summary, primary_type, evidence_tier, category, track, base_score, freshness_score, community_score, discussion_urls, evidence_diff, is_read, is_bookmarked, is_dismissed, published_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
        """, (
            item_id,
            canonical_url,
            item["primary_observation_id"],
            item["source_name"],
            item["title"],
            item.get("clean_summary", ""),
            item.get("primary_type", "news"),
            tier,
            item.get("category", "General AI"),
            item.get("track", "curated_news"),
            base_score,
            freshness,
            comm_score,
            json.dumps(discussion_urls),
            item.get("evidence_diff"),
            pub_iso,
            now
        ))
        
    conn.commit()
    conn.close()
    return item_id

def recalculate_freshness():
    """Batch updates freshness for all active items using dynamic half-life formula."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, base_score, evidence_tier, published_at, created_at, community_score FROM feed_items WHERE is_dismissed = 0")
    rows = cursor.fetchall()
    
    for row in rows:
        pub_iso = row["published_at"] or row["created_at"]
        freshness = compute_freshness_score(row["base_score"], row["evidence_tier"], pub_iso, row["community_score"])
        cursor.execute("UPDATE feed_items SET freshness_score = ? WHERE id = ?", (freshness, row["id"]))
        
    conn.commit()
    conn.close()

def get_feed_items(
    track: Optional[str] = "All",
    category: Optional[str] = None,
    evidence_tier: Optional[str] = None,
    search: Optional[str] = None,
    only_bookmarked: bool = False,
    include_dismissed: bool = False,
    limit: int = 60
) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    
    params = []
    where_clauses = []
    
    if not include_dismissed:
        where_clauses.append("f.is_dismissed = 0")
        
    if only_bookmarked:
        where_clauses.append("f.is_bookmarked = 1")
        
    if track and track != "All":
        where_clauses.append("f.track = ?")
        params.append(track)

    if category and category != "All":
        where_clauses.append("f.category = ?")
        params.append(category)
        
    if evidence_tier and evidence_tier != "All":
        where_clauses.append("f.evidence_tier = ?")
        params.append(evidence_tier)

    # SQLite FTS5 search with prefix matching and hyphen preservation
    if search and search.strip():
        # Sanitize query for FTS syntax while preserving words and numbers
        words = search.strip().split()
        fts_tokens = []
        for w in words:
            clean_w = "".join(c for c in w if c.isalnum() or c in ("-", "_")).strip()
            if clean_w:
                fts_tokens.append(f'"{clean_w}"*')
        if fts_tokens:
            fts_expr = " AND ".join(fts_tokens)
            where_clauses.append("f.id IN (SELECT id FROM feed_items_fts WHERE feed_items_fts MATCH ?)")
            params.append(fts_expr)
            
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    
    query = f"""
        SELECT f.* 
        FROM feed_items f
        {where_sql}
        ORDER BY f.freshness_score DESC, f.published_at DESC
        LIMIT ?
    """
    params.append(limit)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    results = []
    for row in rows:
        d = dict(row)
        try:
            d["discussion_urls"] = json.loads(d.get("discussion_urls") or "[]")
        except Exception:
            d["discussion_urls"] = []
        results.append(d)
        
    conn.close()
    return results

def get_bookmark_count() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM feed_items WHERE is_bookmarked = 1 AND is_dismissed = 0")
    row = cursor.fetchone()
    conn.close()
    return row["count"] if row else 0

def update_item_state(item_id: str, is_read: Optional[int] = None, is_bookmarked: Optional[int] = None, is_dismissed: Optional[int] = None) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    updates = []
    params = []
    if is_read is not None:
        updates.append("is_read = ?")
        params.append(1 if is_read else 0)
    if is_bookmarked is not None:
        updates.append("is_bookmarked = ?")
        params.append(1 if is_bookmarked else 0)
    if is_dismissed is not None:
        updates.append("is_dismissed = ?")
        params.append(1 if is_dismissed else 0)
        
    if not updates:
        conn.close()
        return False
        
    params.append(item_id)
    cursor.execute(f"UPDATE feed_items SET {', '.join(updates)} WHERE id = ?", params)
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def save_feedback(contact: str, suggestion: str, willing_to_pay: str, pay_amount: str = "") -> int:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute("""
        INSERT INTO feedback (contact, suggestion, willing_to_pay, pay_amount, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (contact, suggestion, willing_to_pay, pay_amount, now))
    feedback_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return feedback_id

def get_feedback_list(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    results = [dict(row) for row in rows]
    conn.close()
    return results

if __name__ == "__main__":
    init_db()
    print("Database initialized with snapshots, events, versioned observations, and track support.")
