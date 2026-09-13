from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/duplicate-reviews", tags=["duplicate-reviews"])


@router.get("")
def list_duplicate_reviews(
    reviewed: Optional[bool] = None,
    marketplace_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Every payout_key collision hit during import (marketplace + order ID +
    amount already existed) - flagged here instead of silently dropped, so a
    human can confirm it really is the same transaction re-arriving and not
    two distinct transactions that happened to share a key.
    """
    query = """
        SELECT
            d.duplicate_review_id, d.import_file_id, d.filename, d.marketplace_order_id,
            d.amount, d.payout_key, d.raw_row, d.reviewed, d.reviewed_by, d.reviewed_at,
            d.review_notes, d.created_at,
            m.name AS marketplace_name
        FROM import_duplicate_reviews d
        LEFT JOIN marketplaces m ON m.marketplace_id = d.marketplace_id
        WHERE 1=1
    """
    params = {}
    if reviewed is not None:
        query += " AND d.reviewed = :reviewed"
        params["reviewed"] = reviewed
    if marketplace_id is not None:
        query += " AND d.marketplace_id = :marketplace_id"
        params["marketplace_id"] = marketplace_id
    query += " ORDER BY d.created_at DESC, d.duplicate_review_id DESC LIMIT 500"

    rows = db.execute(text(query), params).fetchall()
    return [dict(r._mapping) for r in rows]


class ReviewUpdate(BaseModel):
    reviewed_by: str
    review_notes: Optional[str] = None


@router.put("/{duplicate_review_id}/review")
def mark_reviewed(duplicate_review_id: int, body: ReviewUpdate, db: Session = Depends(get_db)):
    result = db.execute(text("""
        UPDATE import_duplicate_reviews
        SET reviewed = TRUE, reviewed_by = :by, reviewed_at = NOW(), review_notes = :notes
        WHERE duplicate_review_id = :id
    """), {"by": body.reviewed_by, "notes": body.review_notes, "id": duplicate_review_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Duplicate review row not found")
    db.commit()
    return {"status": "ok"}
