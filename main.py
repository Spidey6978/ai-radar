import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Header, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database
import collector

# Production-safe admin key handling: reject default string in production
ADMIN_KEY = os.environ.get("ADMIN_KEY")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Modern lifespan initialization
    database.init_db()
    yield

app = FastAPI(
    title="AI Radar // Frontier Engineering Intelligence",
    description="Dual-track intelligence radar tracking verified model diffs, research disclosures, and curated scoops.",
    lifespan=lifespan
)

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if not STATIC_DIR.exists():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
if not TEMPLATES_DIR.exists():
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# -------------------------------------------------------------
# PYDANTIC SCHEMAS
# -------------------------------------------------------------

class FeedbackPayload(BaseModel):
    contact: Optional[str] = ""
    suggestion: str
    willing_to_pay: str
    pay_amount: Optional[str] = ""

class ItemActionPayload(BaseModel):
    is_read: Optional[bool] = None
    is_bookmarked: Optional[bool] = None
    is_dismissed: Optional[bool] = None

# -------------------------------------------------------------
# WEB ROUTES
# -------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    items = database.get_feed_items(limit=60)
    sources = database.get_sources_health()
    bookmark_count = database.get_bookmark_count()
    
    active_count = sum(1 for s in sources if s.get("last_status_code") in (200, 304))
    total_sources = len(sources)
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "items": items,
        "sources": sources,
        "active_sources": active_count,
        "total_sources": total_sources,
        "bookmark_count": bookmark_count,
        "now_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    })

# -------------------------------------------------------------
# API ROUTES
# -------------------------------------------------------------

@app.get("/api/feed")
async def get_feed(
    track: Optional[str] = "All",
    category: Optional[str] = "All",
    evidence_tier: Optional[str] = "All",
    search: Optional[str] = None,
    only_bookmarked: bool = False,
    only_cluster_masters: bool = True,
    limit: int = Query(60, le=100)
):
    """
    Query-time ranked feed supporting dual-track selection, cluster deduplication, FTS search, and multi-tier filtering.
    """
    items = database.get_feed_items(
        track=track,
        category=category,
        evidence_tier=evidence_tier,
        search=search,
        only_bookmarked=only_bookmarked,
        only_cluster_masters=only_cluster_masters,
        limit=limit
    )
    bookmark_count = database.get_bookmark_count()
    return JSONResponse(content={
        "items": items,
        "count": len(items),
        "bookmark_count": bookmark_count
    })

@app.get("/api/feed/cluster/{cluster_id}")
async def get_cluster_details(cluster_id: str):
    """
    Returns all stories grouped under a given master cluster.
    """
    items = database.get_cluster_items(cluster_id)
    if not items:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return {"cluster_id": cluster_id, "count": len(items), "items": items}

@app.post("/api/feed/{item_id}/action")
async def update_item_action(item_id: str, payload: ItemActionPayload):
    """
    Update item state: read, bookmark, or dismiss.
    """
    success = database.update_item_state(
        item_id=item_id,
        is_read=payload.is_read,
        is_bookmarked=payload.is_bookmarked,
        is_dismissed=payload.is_dismissed
    )
    if not success:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"status": "ok", "item_id": item_id, "bookmark_count": database.get_bookmark_count()}

@app.get("/api/sources/health")
async def get_sources_health():
    """
    Returns live health, conditional status, and last checked times across all registered adapters.
    """
    sources = database.get_sources_health()
    active_count = sum(1 for s in sources if s.get("last_status_code") in (200, 304))
    return {
        "active_sources": active_count,
        "total_sources": len(sources),
        "sources": sources
    }

@app.post("/api/collector/trigger")
async def trigger_collector(background_tasks: BackgroundTasks):
    """
    Asynchronous, non-blocking trigger for collector refresh cycle.
    """
    background_tasks.add_task(collector.run_collector_cycle)
    return {"status": "triggered", "message": "Collector refresh initiated in background."}

@app.post("/api/feedback")
async def submit_feedback(payload: FeedbackPayload):
    if not payload.suggestion.strip():
        raise HTTPException(status_code=400, detail="Suggestion cannot be empty")
        
    feedback_id = database.save_feedback(
        contact=payload.contact or "Anonymous",
        suggestion=payload.suggestion,
        willing_to_pay=payload.willing_to_pay,
        pay_amount=payload.pay_amount or ""
    )
    return {"status": "success", "id": feedback_id}

@app.get("/api/feedback")
async def view_feedback_admin(x_admin_key: Optional[str] = Header(None)):
    """
    Secured feedback inspection route. Requires valid X-Admin-Key header.
    Query parameter tokens are rejected to prevent log leakage.
    """
    if not ADMIN_KEY or not x_admin_key or x_admin_key != ADMIN_KEY:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Valid X-Admin-Key header required."
        )
    return database.get_feedback_list(limit=50)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
