import json
import logging
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, Request, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.services import import_service
from app.routers.sync import trigger_background_sync
from app.auth import get_current_user

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/stage")
async def stage_import(
    request: Request,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """
    Preview what would happen if these files were imported.
    No data is written to the database.
    """
    logger.info(f"Stage request received. Content-Type: {request.headers.get('content-type')}")
    logger.info(f"Files received: {[f.filename for f in files]}")
    file_data = []
    for f in files:
        content = await f.read()
        logger.info(f"  {f.filename}: {len(content)} bytes")
        file_data.append((f.filename, content))
    return import_service.stage_files(db, file_data)


@router.post("/commit")
async def commit_import(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    payment_dates: str = Form("{}"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Import validated payout files into the database.
    Skips files already imported or unrecognized formats.
    payment_dates is a JSON-encoded {filename: "YYYY-MM-DD"} map of manually
    entered payment dates, for marketplaces with no date signal in the filename.
    Automatically kicks off a background sync scoped to just the files
    committed here - matching only writes to our own DB (sales/sale_id),
    it never touches ReachPro, so it's safe to run without a human trigger.
    """
    file_data = [(f.filename, await f.read()) for f in files]
    try:
        dates = json.loads(payment_dates)
    except json.JSONDecodeError:
        dates = {}
    result = import_service.commit_files(db, file_data, imported_by=current_user.name, payment_dates=dates)

    imported_file_ids = [f["import_file_id"] for f in result["files"] if f.get("status") == "imported"]
    result["import_file_ids"] = imported_file_ids
    result["auto_sync_started"] = (
        bool(imported_file_ids) and trigger_background_sync(background_tasks, imported_file_ids)
    )
    return result


@router.get("/skipped-rows")
def list_skipped_rows(
    import_file_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Rows the parser dropped during a commit (blank/malformed order ID, footer
    rows, etc.) - persisted permanently so this data is auditable after the
    fact, not just visible in the staging preview for a few minutes.
    """
    filters = []
    params: dict = {}
    if import_file_id is not None:
        filters.append("s.import_file_id = :import_file_id")
        params["import_file_id"] = import_file_id
    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    rows = db.execute(text(f"""
        SELECT s.skipped_row_id, s.import_file_id, s.filename, m.name AS marketplace,
               s.reason, s.raw_row, s.created_at
        FROM import_skipped_rows s
        LEFT JOIN marketplaces m ON m.marketplace_id = s.marketplace_id
        {where}
        ORDER BY s.created_at DESC, s.skipped_row_id DESC
        LIMIT 500
    """), params).fetchall()

    return {"rows": [dict(r._mapping) for r in rows]}


@router.get("/batch-status")
def batch_status(
    import_file_ids: str = Query(..., description="Comma-separated import_file_id list"),
    db: Session = Depends(get_db),
):
    """
    Resolution status for one just-committed import batch: how many rows are
    matched, dismissed as won't-match, or still unresolved - plus the detail
    needed to review and act on whatever's still unresolved. Drives the
    Import screen's 'resolve everything before you can push' gate.
    """
    try:
        file_ids = [int(x) for x in import_file_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="import_file_ids must be a comma-separated list of integers")
    if not file_ids:
        return {"total": 0, "matched": 0, "dismissed": 0, "unresolved": 0, "unresolved_rows": []}

    rows = db.execute(text("""
        SELECT mp.payout_id, mp.import_file_id, m.name AS marketplace, mp.marketplace_order_id,
               mp.amount, mp.payment_type, mp.sale_id, mp.match_dismissed_at, mp.raw_data
        FROM marketplace_payouts mp
        JOIN marketplaces m ON m.marketplace_id = mp.marketplace_id
        WHERE mp.import_file_id = ANY(:fids)
    """), {"fids": file_ids}).fetchall()

    matched = sum(1 for r in rows if r.sale_id is not None)
    dismissed = sum(1 for r in rows if r.sale_id is None and r.match_dismissed_at is not None)

    unresolved_rows = []
    for r in rows:
        if r.sale_id is None and r.match_dismissed_at is None:
            raw = r.raw_data or {}
            unresolved_rows.append({
                "payout_id": r.payout_id,
                "import_file_id": r.import_file_id,
                "marketplace": r.marketplace,
                "order_id": r.marketplace_order_id,
                "amount": float(r.amount),
                "payment_type": r.payment_type,
                "event": raw.get("Event") or raw.get("EventName") or raw.get("Event Name"),
                "venue": raw.get("Venue") or raw.get("Venue Name"),
            })

    return {
        "total": len(rows),
        "matched": matched,
        "dismissed": dismissed,
        "unresolved": len(unresolved_rows),
        "unresolved_rows": unresolved_rows,
    }
