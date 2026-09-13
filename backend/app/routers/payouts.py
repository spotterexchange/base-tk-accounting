from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/payouts", tags=["payouts"])


@router.get("/")
def list_payouts(
    db: Session = Depends(get_db),
    marketplace_id: Optional[int] = Query(None),
    import_file_id: Optional[int] = Query(None),
    payment_type: Optional[str] = Query(None),
    is_parking: Optional[bool] = Query(None),
    unmatched_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
):
    offset = (page - 1) * page_size
    # base_filters excludes the parking filter so parking_count can report how
    # many rows are flagged as parking within the rest of the current view,
    # regardless of whether the parking filter is applied - same pattern as
    # list_unmatched below.
    base_filters = []
    params: dict = {"limit": page_size, "offset": offset}

    if marketplace_id is not None:
        base_filters.append("mp.marketplace_id = :marketplace_id")
        params["marketplace_id"] = marketplace_id
    if import_file_id is not None:
        base_filters.append("mp.import_file_id = :import_file_id")
        params["import_file_id"] = import_file_id
    if payment_type is not None:
        base_filters.append("mp.payment_type = :payment_type")
        params["payment_type"] = payment_type
    if unmatched_only:
        # The working queue: not matched AND not explicitly dismissed as
        # won't-match - dismissed rows are resolved and drop out, same
        # definition batch-status and _run_sync use.
        base_filters.append("mp.sale_id IS NULL")
        base_filters.append("mp.match_dismissed_at IS NULL")

    filters = list(base_filters)
    if is_parking is not None:
        filters.append("mp.is_parking = :is_parking")
        params["is_parking"] = is_parking

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    base_where = ("WHERE " + " AND ".join(base_filters + ["mp.is_parking IS TRUE"])) if base_filters else "WHERE mp.is_parking IS TRUE"
    base_params = {k: v for k, v in params.items() if k not in ("limit", "offset", "is_parking")}

    rows = db.execute(text(f"""
        SELECT
            mp.payout_id,
            m.name           AS marketplace,
            f.filename,
            mp.import_file_id,
            f.payment_date   AS file_payment_date,
            mp.marketplace_order_id,
            mp.amount,
            mp.payment_type,
            mp.is_parking,
            mp.sale_id,
            mp.match_dismissed_at,
            mp.notes,
            mp.created_at
        FROM marketplace_payouts mp
        JOIN marketplaces m ON m.marketplace_id = mp.marketplace_id
        LEFT JOIN payout_import_files f ON f.import_file_id = mp.import_file_id
        {where}
        ORDER BY mp.payout_id DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    count_row = db.execute(text(f"""
        SELECT COUNT(*)
        FROM marketplace_payouts mp
        JOIN marketplaces m ON m.marketplace_id = mp.marketplace_id
        LEFT JOIN payout_import_files f ON f.import_file_id = mp.import_file_id
        {where}
    """), {k: v for k, v in params.items() if k not in ("limit", "offset")}).scalar()

    parking_count = db.execute(text(f"""
        SELECT COUNT(*)
        FROM marketplace_payouts mp
        {base_where}
    """), base_params).scalar()

    return {
        "total": count_row,
        "parking_count": parking_count,
        "page": page,
        "page_size": page_size,
        "rows": [dict(r._mapping) for r in rows],
    }


@router.get("/unmatched")
def list_unmatched(
    db: Session = Depends(get_db),
    marketplace_id: Optional[int] = Query(None),
    exclude_parking: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
):
    offset = (page - 1) * page_size
    base_filters = ["mp.sale_id IS NULL"]
    params: dict = {"limit": page_size, "offset": offset}

    if marketplace_id is not None:
        base_filters.append("mp.marketplace_id = :marketplace_id")
        params["marketplace_id"] = marketplace_id

    filters = list(base_filters)
    if exclude_parking:
        filters.append("mp.is_parking IS NOT TRUE")

    where = "WHERE " + " AND ".join(filters)
    base_where = "WHERE " + " AND ".join(base_filters)
    base_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

    rows = db.execute(text(f"""
        SELECT
            mp.payout_id,
            m.name              AS marketplace,
            mp.marketplace_order_id,
            mp.payment_type,
            mp.amount,
            mp.payment_date,
            mp.notes,
            mp.is_parking,
            f.filename
        FROM marketplace_payouts mp
        JOIN marketplaces m ON m.marketplace_id = mp.marketplace_id
        LEFT JOIN payout_import_files f ON f.import_file_id = mp.import_file_id
        {where}
        ORDER BY m.name, mp.marketplace_order_id
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    count_row = db.execute(text(f"""
        SELECT COUNT(*)
        FROM marketplace_payouts mp
        JOIN marketplaces m ON m.marketplace_id = mp.marketplace_id
        LEFT JOIN payout_import_files f ON f.import_file_id = mp.import_file_id
        {where}
    """), base_params).scalar()

    # How many rows are flagged as parking (auto-detected via a substring match
    # on the raw CSV, so it can misfire) within the same base filter, regardless
    # of whether exclude_parking is currently applied - lets the UI show what's
    # actually being hidden instead of a silent toggle.
    parking_count = db.execute(text(f"""
        SELECT COUNT(*)
        FROM marketplace_payouts mp
        JOIN marketplaces m ON m.marketplace_id = mp.marketplace_id
        {base_where} AND mp.is_parking IS TRUE
    """), base_params).scalar()

    return {
        "total": count_row,
        "parking_count": parking_count,
        "page": page,
        "page_size": page_size,
        "rows": [dict(r._mapping) for r in rows],
    }


@router.get("/files")
def list_files(
    db: Session = Depends(get_db),
    marketplace_id: Optional[int] = Query(None),
):
    """One row per imported file (not per payout), with the ReachPro payment
    date and aggregate row count/amount — for setting the date at the file
    level instead of hunting through individual payout rows."""
    filters = []
    params: dict = {}
    if marketplace_id is not None:
        filters.append("f.marketplace_id = :marketplace_id")
        params["marketplace_id"] = marketplace_id
    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    rows = db.execute(text(f"""
        SELECT
            f.import_file_id,
            f.filename,
            m.name AS marketplace,
            f.payment_date,
            COUNT(mp.payout_id) AS row_count,
            COALESCE(SUM(mp.amount), 0) AS total_amount
        FROM payout_import_files f
        JOIN marketplaces m ON m.marketplace_id = f.marketplace_id
        LEFT JOIN marketplace_payouts mp ON mp.import_file_id = f.import_file_id
        {where}
        GROUP BY f.import_file_id, f.filename, m.name, f.payment_date
        ORDER BY f.filename
    """), params).fetchall()

    return {"files": [dict(r._mapping) for r in rows]}


@router.get("/filter-options")
def filter_options(db: Session = Depends(get_db)):
    marketplaces = db.execute(text("""
        SELECT DISTINCT m.marketplace_id, m.name
        FROM marketplaces m
        JOIN marketplace_payouts mp ON mp.marketplace_id = m.marketplace_id
        ORDER BY m.name
    """)).fetchall()

    files = db.execute(text("""
        SELECT f.import_file_id, f.filename, m.name AS marketplace
        FROM payout_import_files f
        JOIN marketplaces m ON m.marketplace_id = f.marketplace_id
        ORDER BY f.filename
    """)).fetchall()

    types = db.execute(text("""
        SELECT DISTINCT payment_type FROM marketplace_payouts
        WHERE payment_type IS NOT NULL ORDER BY payment_type
    """)).fetchall()

    return {
        "marketplaces": [{"id": r[0], "name": r[1]} for r in marketplaces],
        "files": [{"id": r[0], "filename": r[1], "marketplace": r[2]} for r in files],
        "payment_types": [r[0] for r in types],
    }


class DismissMatchRequest(BaseModel):
    dismissed_by: str
    note: str


@router.put("/{payout_id}/dismiss-match")
def dismiss_match(payout_id: int, body: DismissMatchRequest, db: Session = Depends(get_db)):
    """Explicitly mark an unmatched payout as 'won't match' - e.g. the
    underlying sale was cancelled/voided in ReachPro and no invoice will ever
    exist for it. Requires a note so the reasoning is auditable. Excludes the
    row from future automatic sync attempts (see _run_sync)."""
    if not body.note.strip():
        raise HTTPException(status_code=400, detail="A note is required to dismiss a row as unmatchable")

    row = db.execute(text(
        "SELECT sale_id FROM marketplace_payouts WHERE payout_id = :pid"
    ), {"pid": payout_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Payout not found")
    if row.sale_id is not None:
        raise HTTPException(status_code=400, detail="This payout is already matched - nothing to dismiss")

    db.execute(text("""
        UPDATE marketplace_payouts
        SET match_dismissed_at = NOW(), match_dismissed_by = :by, match_dismissal_note = :note
        WHERE payout_id = :pid
    """), {"by": body.dismissed_by, "note": body.note, "pid": payout_id})
    db.commit()
    return {"status": "ok"}
