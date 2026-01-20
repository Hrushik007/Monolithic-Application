from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from database import get_db
from checkout import checkout_logic
import sqlite3
from functools import lru_cache

app = FastAPI()
SRN = "PES1UG23AM118"

# Cache templates
templates = Jinja2Templates(directory="templates")

# Add template caching via Jinja2 bytecode cache
templates.env.bytecode_cache = None  # Can enable if needed
templates.env.auto_reload = False  # Disable auto-reload in production


@app.on_event("startup")
def startup():
    db = get_db()

    # Enhanced SQLite tuning for maximum read performance
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute("PRAGMA mmap_size=30000000000")
    db.execute("PRAGMA cache_size=-64000")  # 64MB cache
    db.execute("PRAGMA page_size=4096")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA wal_autocheckpoint=1000")
    
    # Connection pooling settings
    db.execute("PRAGMA locking_mode=NORMAL")  # Allow multiple readers

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            fee INTEGER
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS registrations (
            username TEXT,
            event_id INTEGER,
            PRIMARY KEY (username, event_id)
        )
    """)

    # Optimized indexes for the JOIN query
    db.execute("CREATE INDEX IF NOT EXISTS idx_reg_user ON registrations(username)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_reg_event ON registrations(event_id)")
    
    # Covering index for my-events query (includes all needed columns)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_reg_user_event 
        ON registrations(username, event_id)
    """)

    db.commit()
    
    # Run ANALYZE to update query planner statistics
    db.execute("ANALYZE")
    db.commit()


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
def register(username: str = Form(...), password: str = Form(...)):
    db = get_db()
    try:
        db.execute("INSERT INTO users VALUES (?, ?)", (username, password))
        db.commit()
    except:
        return HTMLResponse("Username already exists. Try a different one.")
    return RedirectResponse("/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    db = get_db()
    user = db.execute(
        "SELECT 1 FROM users WHERE username = ? AND password = ?",
        (username, password)
    ).fetchone()

    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "❌ Invalid username or password", "user": ""}
        )

    return RedirectResponse(f"/events?user={username}", status_code=302)


@app.get("/events", response_class=HTMLResponse)
def events(request: Request, user: str):
    db = get_db()
    rows = db.execute("SELECT * FROM events").fetchall()

    return templates.TemplateResponse(
        "events.html",
        {"request": request, "events": rows, "user": user}
    )


@app.get("/register_event/{event_id}")
def register_event(event_id: int, user: str):
    db = get_db()

    exists = db.execute(
        "SELECT 1 FROM events WHERE id = ?",
        (event_id,)
    ).fetchone()

    if not exists:
        return RedirectResponse(f"/events?user={user}", status_code=302)

    db.execute(
        "INSERT OR IGNORE INTO registrations VALUES (?, ?)",
        (user, event_id)
    )
    db.commit()

    return RedirectResponse(f"/my-events?user={user}", status_code=302)


# 🚀🚀🚀 HEAVILY OPTIMIZED FOR LOCUST 🚀🚀🚀
@app.get("/my-events", response_class=HTMLResponse)
def my_events(request: Request, user: str):
    db = get_db()

    # Read-only mode - no transaction overhead
    db.isolation_level = None
    
    # Optimized query with explicit column selection
    # Use the covering index idx_reg_user_event
    rows = db.execute(
        """
        SELECT e.name, e.fee
        FROM registrations r
        JOIN events e ON e.id = r.event_id
        WHERE r.username = ?
        """,
        (user,)
    ).fetchall()

    # Pre-build context dict (minor optimization)
    context = {
        "request": request,
        "events": rows,
        "user": user
    }

    return templates.TemplateResponse("my_events.html", context)


@app.get("/checkout", response_class=HTMLResponse)
def checkout(request: Request):
    total = checkout_logic()
    return templates.TemplateResponse(
        "checkout.html",
        {"request": request, "total": total, "user": ""}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    user = request.query_params.get("user", "")

    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 500,
            "detail": str(exc),
            "user": user
        },
        status_code=500
    )