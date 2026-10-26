import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database
import fetcher

app = FastAPI(title="AI Radar - Frontier & Niche Intel Fetcher")

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if not STATIC_DIR.exists():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
if not TEMPLATES_DIR.exists():
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory tracking
is_fetching = False
last_fetch_time: Optional[str] = None

@app.on_event("startup")
def on_startup():
    database.init_db()

class FeedbackPayload(BaseModel):
    contact: Optional[str] = ""
    suggestion: str
    willing_to_pay: str
    pay_amount: Optional[str] = ""

async def background_fetch_task():
    global is_fetching, last_fetch_time
    if is_fetching:
        return
    is_fetching = True
    try:
        items = await fetcher.fetch_all_intel()
        database.save_articles(items)
        last_fetch_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception as e:
        print(f"Background fetch error: {e}")
    finally:
        is_fetching = False

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    articles = database.get_articles(limit=50)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "articles": articles,
        "last_updated": last_fetch_time or "Ready"
    })

@app.get("/api/feed")
async def get_feed(category: Optional[str] = None, search: Optional[str] = None, limit: int = 60):
    articles = database.get_articles(category=category, search=search, limit=limit)
    return JSONResponse(content={
        "count": len(articles),
        "articles": articles,
        "is_fetching": is_fetching,
        "last_updated": last_fetch_time
    })

@app.post("/api/refresh")
async def trigger_refresh(background_tasks: BackgroundTasks):
    global is_fetching
    if is_fetching:
        return JSONResponse({"status": "already_running", "message": "Intel fetch is already in progress"})
    
    background_tasks.add_task(background_fetch_task)
    return JSONResponse({"status": "started", "message": "Fresh sweep started across all channels"})

@app.post("/api/feedback")
async def submit_feedback(payload: FeedbackPayload):
    if not payload.suggestion.strip():
        return JSONResponse({"error": "Suggestion cannot be empty"}, status_code=400)
    
    feedback_id = database.save_feedback(
        contact=payload.contact or "Anonymous",
        suggestion=payload.suggestion,
        willing_to_pay=payload.willing_to_pay,
        pay_amount=payload.pay_amount or ""
    )
    return JSONResponse({"status": "success", "id": feedback_id, "message": "Feedback received. Thank you!"})

@app.get("/api/feedback")
async def list_feedback():
    items = database.get_feedback_list(limit=50)
    return JSONResponse({"feedback": items})

@app.get("/api/stats")
async def get_stats():
    conn = database.get_connection()
    cursor = conn.cursor()
    total_articles = cursor.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    category_counts = dict(cursor.execute("SELECT category, COUNT(*) FROM articles GROUP BY category").fetchall())
    total_feedback = cursor.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    conn.close()
    
    return JSONResponse({
        "total_articles": total_articles,
        "categories": category_counts,
        "total_feedback": total_feedback,
        "is_fetching": is_fetching,
        "last_updated": last_fetch_time
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
