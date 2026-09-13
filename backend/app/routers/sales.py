from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.services import sales_import_service
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("/import")
async def import_sales(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    return sales_import_service.import_sales(db, content)


@router.get("/splits")
def get_split_sales(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT
            s.sale_id,
            s.reachpro_sale_id,
            e.performer,
            e.venue,
            e.event_date,
            s.proceeds,
            s.pnl,
            s.marketplace_order_id,
            JSON_AGG(
                JSON_BUILD_OBJECT(
                    'purchaser_id', p.purchaser_id,
                    'name', p.name,
                    'split_pct', sp.split_pct
                ) ORDER BY p.name
            ) AS purchasers
        FROM sales s
        JOIN sale_purchasers sp ON sp.sale_id = s.sale_id
        JOIN purchasers p ON p.purchaser_id = sp.purchaser_id
        LEFT JOIN events e ON e.event_id = s.event_id
        WHERE s.sale_id IN (
            SELECT sale_id FROM sale_purchasers GROUP BY sale_id HAVING COUNT(*) > 1
        )
        GROUP BY s.sale_id, s.reachpro_sale_id, e.performer, e.venue, e.event_date,
                 s.proceeds, s.pnl, s.marketplace_order_id
        ORDER BY e.event_date, s.reachpro_sale_id
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


class SplitEntry(BaseModel):
    purchaser_id: int
    split_pct: float


class UpdateSplitsRequest(BaseModel):
    splits: List[SplitEntry]


@router.put("/{sale_id}/splits")
def update_splits(sale_id: int, body: UpdateSplitsRequest, db: Session = Depends(get_db)):
    total = sum(s.split_pct for s in body.splits)
    if abs(total - 1.0) > 0.001:
        raise HTTPException(
            status_code=400,
            detail=f"Split percentages must sum to 100% (got {total * 100:.2f}%)"
        )
    for entry in body.splits:
        db.execute(text("""
            UPDATE sale_purchasers SET split_pct = :pct
            WHERE sale_id = :sid AND purchaser_id = :pid
        """), {"pct": entry.split_pct, "sid": sale_id, "pid": entry.purchaser_id})
    db.commit()
    return {"ok": True}
