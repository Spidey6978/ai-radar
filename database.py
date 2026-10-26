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

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # High-concurrency WAL mode
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
            endpoint_url TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            last_checked_at TEXT,
            last_success_at TEXT,
            last_status_code INTEGER,
            error_message TEXT
        )
    """)

    # 2. Raw observations (immutable audit log)
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

    # 3. Normalized Feed Items
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
            base_score REAL DEFAULT 0.0,
            freshness_score REAL DEFAULT 0.0,
            community_score INTEGER DEFAULT 0,
            discussion_urls TEXT DEFAULT '[]',
            is_read INTEGER DEFAULT 0,
            is_bookmarked INTEGER DEFAULT 0,
            is_dismissed INTEGER DEFAULT 0,
            published_at TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_scores ON feed_items(freshness_score DESC, published_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_category ON feed_items(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_evidence ON feed_items(evidence_tier)")

    # 4. Full-Text Search Virtual Table
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS feed_items_fts USING fts5(
            id UNINDEXED,
            title,
            clean_summary,
            source_name,
            category
        )
    """)

    # Triggers to keep FTS in sync
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

    # 5. Feedback & User Suggestions table
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

def register_source(source_id: str, name: str, tier: str, endpoint_url: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sources (id, name, tier, endpoint_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            tier = excluded.tier,
            endpoint_url = excluded.endpoint_url
    """, (source_id, name, tier, endpoint_url))
    conn.commit()
    conn.close()

def update_source_health(source_id: str, status_code: int, error_message: Optional[str] = None):
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    if status_code == 200:
        cursor.execute("""
            UPDATE sources 
            SET last_checked_at = ?, last_success_at = ?, last_status_code = ?, error_message = NULL
            WHERE id = ?
        """, (now, now, status_code, source_id))
    else:
        cursor.execute("""
            UPDATE sources 
            SET last_checked_at = ?, last_status_code = ?, error_message = ?
            WHERE id = ?
        """, (now, status_code, error_message, source_id))
    conn.commit()
    conn.close()

def get_sources_health() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sources ORDER BY tier, name")
    rows = cursor.fetchall()
    results = [dict(row) for row in rows]
    conn.close()
    return results

def save_observation(obs: Dict[str, Any]) -> tuple[str, bool]:
    """
    Saves a raw observation. Returns (observation_id, is_new).
    """
    conn = get_connection()
    cursor = conn.cursor()
    obs_id = obs["id"]
    now = datetime.now(timezone.utc).isoformat()
    
    metadata_json = json.dumps(obs.get("raw_metadata", {}))
    is_new = False
    
    try:
        cursor.execute("""
            INSERT INTO observations 
            (id, source_id, source_native_id, canonical_url, raw_title, raw_content, content_hash, published_at, first_seen_at, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                raw_content = excluded.raw_content,
                content_hash = excluded.content_hash,
                raw_metadata = excluded.raw_metadata
        """, (
            obs_id,
            obs["source_id"],
            obs.get("source_native_id", obs_id),
            obs["canonical_url"],
            obs["raw_title"],
            obs.get("raw_content", ""),
            obs["content_hash"],
            obs.get("published_at"),
            obs.get("first_seen_at", now),
            metadata_json
        ))
        if cursor.rowcount > 0:
            is_new = True
    except Exception as e:
        print(f"Error saving observation {obs_id}: {e}")
    finally:
        conn.commit()
        conn.close()
        
    return obs_id, is_new

def upsert_feed_item(item: Dict[str, Any]) -> str:
    """
    Deduplicates and merges observations into normalized feed_items.
    If an item with canonical_url already exists, merges discussion URLs or retains highest evidence tier.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    canonical_url = item["canonical_url"]
    
    # Check if item with this canonical_url already exists
    cursor.execute("SELECT id, discussion_urls, evidence_tier, base_score, community_score FROM feed_items WHERE canonical_url = ?", (canonical_url,))
    existing = cursor.fetchone()
    
    discussion_urls = item.get("discussion_urls", [])
    
    if existing:
        item_id = existing["id"]
        # Merge discussion URLs
        try:
            curr_disc = json.loads(existing["discussion_urls"] or "[]")
        except Exception:
            curr_disc = []
        for d in discussion_urls:
            if d not in curr_disc:
                curr_disc.append(d)
                
        # Only upgrade evidence_tier if incoming is higher priority
        tiers_rank = {
            "verified_artifact": 4,
            "technical_paper": 3,
            "security_advisory": 3,
            "community_claim": 2,
            "secondary_press": 1
        }
        old_rank = tiers_rank.get(existing["evidence_tier"], 0)
        new_rank = tiers_rank.get(item["evidence_tier"], 0)
        
        target_tier = item["evidence_tier"] if new_rank > old_rank else existing["evidence_tier"]
        target_score = max(existing["base_score"], item.get("base_score", 0.0))
        target_comm_score = max(existing["community_score"], item.get("community_score", 0))
        
        cursor.execute("""
            UPDATE feed_items 
            SET discussion_urls = ?,
                evidence_tier = ?,
                base_score = ?,
                community_score = ?
            WHERE id = ?
        """, (json.dumps(curr_disc), target_tier, target_score, target_comm_score, item_id))
    else:
        item_id = item["id"]
        cursor.execute("""
            INSERT INTO feed_items 
            (id, canonical_url, primary_observation_id, source_name, title, clean_summary, primary_type, evidence_tier, category, base_score, freshness_score, community_score, discussion_urls, is_read, is_bookmarked, is_dismissed, published_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
        """, (
            item_id,
            canonical_url,
            item["primary_observation_id"],
            item["source_name"],
            item["title"],
            item.get("clean_summary", ""),
            item.get("primary_type", "news"),
            item.get("evidence_tier", "secondary_press"),
            item.get("category", "General AI"),
            item.get("base_score", 50.0),
            item.get("freshness_score", item.get("base_score", 50.0)),
            item.get("community_score", 0),
            json.dumps(discussion_urls),
            item.get("published_at"),
            now
        ))
        
    conn.commit()
    conn.close()
    return item_id

def recalculate_freshness():
    """
    Applies evidence-aware exponential decay:
    - verified_artifact: 72h half-life (stable technical release)
    - technical_paper / security_advisory: 48h half-life
    - community_claim / secondary_press: 20h half-life (fast-decay social lead)
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc)
    
    half_lives = {
        "verified_artifact": 72.0,
        "technical_paper": 48.0,
        "security_advisory": 48.0,
        "community_claim": 20.0,
        "secondary_press": 20.0
    }
    ln2 = math.log(2)

    cursor.execute("SELECT id, evidence_tier, base_score, published_at, created_at FROM feed_items WHERE is_dismissed = 0")
    rows = cursor.fetchall()
    
    for row in rows:
        item_id = row["id"]
        tier = row["evidence_tier"]
        base_score = row["base_score"]
        pub_str = row["published_at"] or row["created_at"]
        
        half_life = half_lives.get(tier, 24.0)
        decay_lambda = ln2 / half_life
        
        try:
            if pub_str.endswith("Z"):
                pub_dt = datetime.fromisoformat(pub_str[:-1] + "+00:00")
            elif "+" in pub_str or "-" in pub_str[10:]:
                pub_dt = datetime.fromisoformat(pub_str)
            else:
                pub_dt = datetime.fromisoformat(pub_str).replace(tzinfo=timezone.utc)
            delta_hours = max(0.0, (now - pub_dt).total_seconds() / 3600.0)
        except Exception:
            delta_hours = 0.0
            
        freshness = base_score * math.exp(-decay_lambda * delta_hours)
        cursor.execute("UPDATE feed_items SET freshness_score = ? WHERE id = ?", (round(freshness, 2), item_id))
        
    conn.commit()
    conn.close()

def get_feed_items(
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
        
    if category and category != "All":
        where_clauses.append("f.category = ?")
        params.append(category)
        
    if evidence_tier and evidence_tier != "All":
        where_clauses.append("f.evidence_tier = ?")
        params.append(evidence_tier)

    # Use FTS5 if search query is provided
    if search and search.strip():
        # Clean search query for FTS syntax
        clean_q = "".join(c for c in search if c.isalnum() or c.isspace()).strip()
        if clean_q:
            where_clauses.append("f.id IN (SELECT id FROM feed_items_fts WHERE feed_items_fts MATCH ?)")
            params.append(f'"{clean_q}"*')
            
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
    print("Database initialized with normalized schema, WAL mode, and FTS5 triggers.")
