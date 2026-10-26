import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

# On Vercel serverless, filesystem outside /tmp is read-only
if os.environ.get("VERCEL"):
    DB_PATH = Path("/tmp") / "radar.db"
else:
    DB_PATH = Path(__file__).parent / "radar.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Articles / Intel table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            category TEXT NOT NULL,
            credibility TEXT NOT NULL,
            summary TEXT,
            discussion_url TEXT,
            score INTEGER DEFAULT 0,
            published_at TEXT,
            created_at TEXT
        )
    """)
    
    # Feedback & User Suggestions table
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

def save_articles(articles: List[Dict[str, Any]]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    new_count = 0
    now = datetime.now(timezone.utc).isoformat()
    
    for art in articles:
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO articles 
                (id, title, url, source, source_type, category, credibility, summary, discussion_url, score, published_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                art.get("id"),
                art.get("title"),
                art.get("url"),
                art.get("source"),
                art.get("source_type", "rss"),
                art.get("category", "General AI"),
                art.get("credibility", "Unverified"),
                art.get("summary", ""),
                art.get("discussion_url"),
                art.get("score", 0),
                art.get("published_at"),
                now
            ))
            if cursor.rowcount > 0:
                new_count += 1
        except Exception as e:
            print(f"Error saving article {art.get('title')}: {e}")
            
    conn.commit()
    conn.close()
    return new_count

def get_articles(category: Optional[str] = None, search: Optional[str] = None, limit: int = 60) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM articles WHERE 1=1"
    params = []
    
    if category and category != "All":
        query += " AND category = ?"
        params.append(category)
        
    if search:
        query += " AND (title LIKE ? OR summary LIKE ? OR source LIKE ?)"
        wildcard = f"%{search}%"
        params.extend([wildcard, wildcard, wildcard])
        
    query += " ORDER BY published_at DESC, created_at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    results = [dict(row) for row in rows]
    conn.close()
    return results

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
    print("Database initialized successfully.")
