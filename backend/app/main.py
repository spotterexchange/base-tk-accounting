import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine
from app.routers import imports, payouts, sales, purchasers, reports, sync, adjustments, duplicate_reviews, auth, expenses, users, cv_import
from app.auth import get_current_user
from sqlalchemy import text

app = FastAPI(title="Reachpro Reconciliation API")

# Comma-separated list of allowed frontend origins, settable without a code
# change/redeploy since the deployed frontend's URL isn't known until it
# exists. Defaults to local dev origins.
_default_origins = "http://localhost:3000,http://localhost:5173,http://localhost:5174"
allowed_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# /auth itself must stay open (that's how you get a token in the first place).
# Every other router requires a valid logged-in user.
app.include_router(auth.router)
_protected = Depends(get_current_user)
app.include_router(imports.router, dependencies=[_protected])
app.include_router(payouts.router, dependencies=[_protected])
app.include_router(sales.router, dependencies=[_protected])
app.include_router(purchasers.router, dependencies=[_protected])
app.include_router(reports.router, dependencies=[_protected])
app.include_router(sync.router, dependencies=[_protected])
app.include_router(adjustments.router, dependencies=[_protected])
app.include_router(duplicate_reviews.router, dependencies=[_protected])
app.include_router(expenses.router, dependencies=[_protected])
app.include_router(cv_import.router, dependencies=[_protected])
app.include_router(users.router)  # enforces its own admin-only dependency

async def _fulfillment_keepalive():
    """Ping the fulfillment dashboard's /health every 5 minutes during its
    working window (14-23 UTC = 10am-8pm ET) so Render's free-tier idle
    spin-down never hits it while the team is on. Lives here because this
    service is on an always-on paid instance; the GitHub Actions pinger it
    replaces billed Actions minutes and silently died whenever the
    account's free minutes ran out."""
    import asyncio
    import urllib.request
    from datetime import datetime, timezone
    url = os.environ.get("FULFILLMENT_URL", "https://fulfillment-dashboard.onrender.com").rstrip("/") + "/health"
    while True:
        if 14 <= datetime.now(timezone.utc).hour <= 23:
            try:
                await asyncio.to_thread(urllib.request.urlopen, url, timeout=90)
            except Exception:
                pass  # cold starts and blips self-correct on the next ping
        await asyncio.sleep(300)


@app.on_event("startup")
async def _start_schedulers():
    import asyncio
    from app.routers.sync import nightly_inventory_scheduler
    asyncio.create_task(nightly_inventory_scheduler())
    asyncio.create_task(_fulfillment_keepalive())


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
