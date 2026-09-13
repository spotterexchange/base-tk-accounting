from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.auth import require_admin
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/purchasers", tags=["purchasers"])


class PurchaserUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    default_commission_pct: Optional[float] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class OverrideCreate(BaseModel):
    event_tag: Optional[str] = None
    purchased_before: Optional[str] = None  # YYYY-MM-DD
    purchased_from: Optional[str] = None    # YYYY-MM-DD
    commission_pct: float


@router.get("/")
def list_purchasers(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT
            p.purchaser_id,
            p.name,
            p.type,
            p.default_commission_pct,
            p.notes,
            p.is_active,
            COUNT(DISTINCT sp.sale_id) AS sale_count
        FROM purchasers p
        LEFT JOIN sale_purchasers sp ON sp.purchaser_id = p.purchaser_id
        GROUP BY p.purchaser_id
        ORDER BY p.name
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/{purchaser_id}")
def update_purchaser(purchaser_id: int, body: PurchaserUpdate, db: Session = Depends(get_db),
                     _admin=Depends(require_admin)):
    existing = db.execute(
        text("SELECT purchaser_id FROM purchasers WHERE purchaser_id = :id"),
        {"id": purchaser_id}
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Purchaser not found")

    fields = []
    params = {"id": purchaser_id}
    if body.name is not None:
        fields.append("name = :name"); params["name"] = body.name
    if body.type is not None:
        fields.append("type = :type"); params["type"] = body.type
    if body.default_commission_pct is not None:
        fields.append("default_commission_pct = :pct"); params["pct"] = body.default_commission_pct
    if body.notes is not None:
        fields.append("notes = :notes"); params["notes"] = body.notes
    if body.is_active is not None:
        fields.append("is_active = :is_active"); params["is_active"] = body.is_active

    if fields:
        db.execute(text(f"UPDATE purchasers SET {', '.join(fields)} WHERE purchaser_id = :id"), params)
        db.commit()

    row = db.execute(
        text("SELECT * FROM purchasers WHERE purchaser_id = :id"), {"id": purchaser_id}
    ).fetchone()
    return dict(row._mapping)


@router.get("/{purchaser_id}/overrides")
def list_overrides(purchaser_id: int, db: Session = Depends(get_db)):
    # Tag rules first (they outrank date-only rules at resolution time),
    # mirroring _resolve_commission's evaluation order
    rows = db.execute(
        text("""
            SELECT * FROM commission_overrides WHERE purchaser_id = :id
            ORDER BY (event_tag IS NULL), override_id
        """),
        {"id": purchaser_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/{purchaser_id}/overrides")
def create_override(purchaser_id: int, body: OverrideCreate, db: Session = Depends(get_db),
                    _admin=Depends(require_admin)):
    tag = (body.event_tag or "").strip() or None
    if not tag and not body.purchased_before and not body.purchased_from:
        raise HTTPException(status_code=400,
                            detail="A rule needs at least one condition (tag or purchase date)")
    try:
        if tag:
            row = db.execute(
                text("""
                    INSERT INTO commission_overrides
                        (purchaser_id, event_tag, purchased_before, purchased_from, commission_pct)
                    VALUES (:pid, :tag, :before, :frm, :pct)
                    ON CONFLICT (purchaser_id, event_tag) DO UPDATE SET
                        commission_pct = EXCLUDED.commission_pct,
                        purchased_before = EXCLUDED.purchased_before,
                        purchased_from = EXCLUDED.purchased_from
                    RETURNING *
                """),
                {"pid": purchaser_id, "tag": tag, "before": body.purchased_before,
                 "frm": body.purchased_from, "pct": body.commission_pct}
            ).fetchone()
        else:
            row = db.execute(
                text("""
                    INSERT INTO commission_overrides
                        (purchaser_id, event_tag, purchased_before, purchased_from, commission_pct)
                    VALUES (:pid, NULL, :before, :frm, :pct)
                    RETURNING *
                """),
                {"pid": purchaser_id, "before": body.purchased_before,
                 "frm": body.purchased_from, "pct": body.commission_pct}
            ).fetchone()
        db.commit()
        return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{purchaser_id}/overrides/{override_id}")
def delete_override(purchaser_id: int, override_id: int, db: Session = Depends(get_db),
                    _admin=Depends(require_admin)):
    db.execute(
        text("DELETE FROM commission_overrides WHERE override_id = :oid AND purchaser_id = :pid"),
        {"oid": override_id, "pid": purchaser_id}
    )
    db.commit()
    return {"ok": True}
