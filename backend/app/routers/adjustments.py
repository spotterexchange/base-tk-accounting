from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from pydantic import BaseModel
from typing import Optional
from datetime import date

router = APIRouter(prefix="/adjustments", tags=["adjustments"])

VALID_TREATMENTS = ("full_amount", "full_amount_minus_cost", "none")

VALID_REASONS = ("missed_ihd", "doe", "cancelled_event", "rejected_replacements", "other")
REASONS_REQUIRING_NOTE = ("rejected_replacements", "other")
REASON_DEFAULT_TREATMENT = {"cancelled_event": "full_amount_minus_cost"}


@router.get("/needs-offset")
def needs_offset(db: Session = Depends(get_db)):
    """Cancelled sales whose linked payouts are still net-positive - i.e. the
    marketplace paid but no offsetting negative adjustment has been imported
    yet. Automatiq/B2B settlement files carry the payout but never the
    clawback (it lives in separate statements), so this is the hunt list:
    each row is a Sale Id + amount to verify against the marketplace's
    records, then resolve: add an offset (in-app or as a manual line in the
    next settlement file) or ignore with a note. Ignored rows are still
    returned, flagged, so the UI can show them collapsed and allow undo."""
    rows = db.execute(text("""
        SELECT
            s.sale_id,
            s.reachpro_sale_id,
            s.fulfillment_status,
            s.offset_resolution,
            s.offset_resolution_note,
            s.offset_resolved_at,
            s.sale_date::date AS sale_date,
            MIN(mp.marketplace_order_id) AS order_id,
            MIN(m.name) AS marketplace,
            e.performer AS event_name,
            (SELECT STRING_AGG(p.name, ', ' ORDER BY p.name)
             FROM sale_purchasers sp JOIN purchasers p ON p.purchaser_id = sp.purchaser_id
             WHERE sp.sale_id = s.sale_id) AS purchaser_names,
            SUM(mp.amount) AS net_unoffset,
            COUNT(*) FILTER (WHERE mp.payment_type = 'Adjustment') AS adjustment_rows,
            MIN(COALESCE(mp.payment_date, pif.payment_date, mp.created_at::date)) AS first_paid,
            BOOL_OR(mp.commission_included_in_payout_id IS NOT NULL) AS any_in_commission_batch
        FROM sales s
        JOIN marketplace_payouts mp ON mp.sale_id = s.sale_id
        JOIN marketplaces m ON m.marketplace_id = mp.marketplace_id
        LEFT JOIN events e ON e.event_id = s.event_id
        LEFT JOIN payout_import_files pif ON pif.import_file_id = mp.import_file_id
        WHERE (s.fulfillment_status ILIKE '%cancel%' OR s.cancellation_date IS NOT NULL)
        GROUP BY s.sale_id, e.performer
        HAVING SUM(mp.amount) > 0.01
        ORDER BY SUM(mp.amount) DESC
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


class IgnoreOffset(BaseModel):
    note: str


@router.post("/needs-offset/{sale_id}/ignore")
def ignore_needs_offset(sale_id: int, body: IgnoreOffset, db: Session = Depends(get_db)):
    """Resolve a hunt-list row as 'this unoffset payout is fine' - e.g. the
    marketplace confirmed no clawback is coming. Requires a note saying why."""
    if not body.note.strip():
        raise HTTPException(status_code=400, detail="A note explaining why this is OK is required.")
    result = db.execute(text("""
        UPDATE sales SET offset_resolution = 'ignored',
            offset_resolution_note = :note, offset_resolved_at = NOW()
        WHERE sale_id = :sid
    """), {"note": body.note.strip(), "sid": sale_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Sale not found")
    db.commit()
    return {"status": "ok"}


@router.delete("/needs-offset/{sale_id}/ignore")
def unignore_needs_offset(sale_id: int, db: Session = Depends(get_db)):
    result = db.execute(text("""
        UPDATE sales SET offset_resolution = NULL,
            offset_resolution_note = NULL, offset_resolved_at = NULL
        WHERE sale_id = :sid
    """), {"sid": sale_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Sale not found")
    db.commit()
    return {"status": "ok"}


class ManualOffset(BaseModel):
    amount: float
    payment_date: date
    note: Optional[str] = None


@router.post("/needs-offset/{sale_id}/offset")
def add_manual_offset(sale_id: int, body: ManualOffset, db: Session = Depends(get_db)):
    """Record the clawback in-app: a negative Adjustment payout on the sale,
    dated so it lands in the chosen commission window. Defaults to the
    cancelled-event treatment (full amount minus cost), reviewable on the
    Adjustments tab like any imported adjustment. Has no import file, so the
    ReachPro push never picks it up - commission math only; use a manual line
    in the next settlement file if ReachPro itself must show the clawback."""
    if body.amount >= 0:
        raise HTTPException(status_code=400, detail="Offset amount must be negative.")
    ref = db.execute(text("""
        SELECT mp.marketplace_id, mp.marketplace_order_id
        FROM marketplace_payouts mp
        WHERE mp.sale_id = :sid
        ORDER BY mp.amount DESC LIMIT 1
    """), {"sid": sale_id}).fetchone()
    if not ref:
        raise HTTPException(status_code=404, detail="Sale has no linked payouts")
    seq = db.execute(text("""
        SELECT COUNT(*) FROM marketplace_payouts
        WHERE payout_key LIKE :prefix
    """), {"prefix": f"manual-offset-{sale_id}-%"}).scalar()
    payout = db.execute(text("""
        INSERT INTO marketplace_payouts (
            marketplace_id, sale_id, marketplace_order_id, amount,
            payment_date, payout_key, payment_type,
            adjustment_reason, adjustment_commission_treatment,
            adjustment_review_notes
        ) VALUES (
            :mkt_id, :sid, :order_id, :amount,
            :pdate, :key, 'Adjustment',
            'cancelled_event', 'full_amount_minus_cost',
            :note
        ) RETURNING payout_id
    """), {
        "mkt_id": ref.marketplace_id,
        "sid": sale_id,
        "order_id": ref.marketplace_order_id,
        "amount": round(body.amount, 2),
        "pdate": body.payment_date,
        "key": f"manual-offset-{sale_id}-{seq + 1}",
        "note": (body.note or "").strip() or "Manual offset entered in-app for cancelled sale",
    }).fetchone()
    db.commit()
    return {"status": "ok", "payout_id": payout.payout_id}


@router.get("")
def list_adjustments(
    purchaser_id: Optional[int] = None,
    marketplace_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    treatment: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """All payouts labeled as Adjustment, with the sale/event/purchaser they're
    linked to, for manual review and commission-treatment selection."""
    query = """
        SELECT
            mp.payout_id, mp.marketplace_order_id, mp.amount,
            mp.adjustment_review_notes, mp.adjustment_commission_treatment,
            mp.adjustment_reason,
            (mp.commission_included_in_payout_id IS NOT NULL) AS locked,
            m.marketplace_id, m.name AS marketplace_name,
            s.sale_id, s.cost, s.proceeds, s.pnl AS sale_pnl, s.sale_date,
            s.quantity, s.fulfillment_status, s.commission_status,
            e.performer AS event_name, e.event_date, e.venue,
            pif.filename AS source_file,
            COALESCE(mp.payment_date, pif.payment_date) AS effective_payment_date,
            (SELECT STRING_AGG(p.name, ', ' ORDER BY p.name)
             FROM sale_purchasers sp JOIN purchasers p ON p.purchaser_id = sp.purchaser_id
             WHERE sp.sale_id = s.sale_id) AS purchaser_names
        FROM marketplace_payouts mp
        JOIN marketplaces m ON m.marketplace_id = mp.marketplace_id
        JOIN sales s ON s.sale_id = mp.sale_id
        LEFT JOIN events e ON e.event_id = s.event_id
        LEFT JOIN payout_import_files pif ON pif.import_file_id = mp.import_file_id
        WHERE mp.payment_type = 'Adjustment'
    """
    params = {}
    if purchaser_id is not None:
        query += """ AND EXISTS (
            SELECT 1 FROM sale_purchasers sp2
            WHERE sp2.sale_id = s.sale_id AND sp2.purchaser_id = :purchaser_id
        )"""
        params["purchaser_id"] = purchaser_id
    if marketplace_id is not None:
        query += " AND mp.marketplace_id = :marketplace_id"
        params["marketplace_id"] = marketplace_id
    if date_from is not None:
        query += " AND COALESCE(mp.payment_date, pif.payment_date) >= :date_from"
        params["date_from"] = date_from
    if date_to is not None:
        query += " AND COALESCE(mp.payment_date, pif.payment_date) <= :date_to"
        params["date_to"] = date_to
    if treatment == "unset":
        query += " AND mp.adjustment_commission_treatment IS NULL"
    elif treatment in VALID_TREATMENTS:
        query += " AND mp.adjustment_commission_treatment = :treatment"
        params["treatment"] = treatment

    query += " ORDER BY COALESCE(mp.payment_date, pif.payment_date) DESC NULLS LAST, mp.payout_id DESC"
    rows = db.execute(text(query), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/filter-options")
def filter_options(db: Session = Depends(get_db)):
    purchasers = db.execute(text("""
        SELECT DISTINCT p.purchaser_id AS id, p.name
        FROM purchasers p
        JOIN sale_purchasers sp ON sp.purchaser_id = p.purchaser_id
        JOIN marketplace_payouts mp ON mp.sale_id = sp.sale_id
        WHERE mp.payment_type = 'Adjustment'
        ORDER BY p.name
    """)).fetchall()
    marketplaces = db.execute(text("""
        SELECT DISTINCT m.marketplace_id AS id, m.name
        FROM marketplaces m
        JOIN marketplace_payouts mp ON mp.marketplace_id = m.marketplace_id
        WHERE mp.payment_type = 'Adjustment'
        ORDER BY m.name
    """)).fetchall()
    return {
        "purchasers": [dict(r._mapping) for r in purchasers],
        "marketplaces": [dict(r._mapping) for r in marketplaces],
    }


class NotesUpdate(BaseModel):
    notes: str


@router.put("/{payout_id}/notes")
def update_notes(payout_id: int, body: NotesUpdate, db: Session = Depends(get_db)):
    """Free-form notes can always be edited, even after the treatment is locked."""
    result = db.execute(text("""
        UPDATE marketplace_payouts SET adjustment_review_notes = :notes
        WHERE payout_id = :pid AND payment_type = 'Adjustment'
    """), {"notes": body.notes, "pid": payout_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    db.commit()
    return {"status": "ok"}


class TreatmentUpdate(BaseModel):
    treatment: str


@router.put("/{payout_id}/commission-treatment")
def update_treatment(payout_id: int, body: TreatmentUpdate, db: Session = Depends(get_db)):
    if body.treatment not in VALID_TREATMENTS:
        raise HTTPException(status_code=400, detail=f"treatment must be one of {VALID_TREATMENTS}")

    row = db.execute(text("""
        SELECT commission_included_in_payout_id FROM marketplace_payouts
        WHERE payout_id = :pid AND payment_type = 'Adjustment'
    """), {"pid": payout_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    if row.commission_included_in_payout_id is not None:
        raise HTTPException(
            status_code=400,
            detail="This adjustment has already been counted in a commission payout and cannot be changed.",
        )

    db.execute(text("""
        UPDATE marketplace_payouts SET adjustment_commission_treatment = :treatment
        WHERE payout_id = :pid
    """), {"treatment": body.treatment, "pid": payout_id})
    db.commit()
    return {"status": "ok"}


class ReasonUpdate(BaseModel):
    reason: str


@router.put("/{payout_id}/reason")
def update_reason(payout_id: int, body: ReasonUpdate, db: Session = Depends(get_db)):
    if body.reason not in VALID_REASONS:
        raise HTTPException(status_code=400, detail=f"reason must be one of {VALID_REASONS}")

    row = db.execute(text("""
        SELECT commission_included_in_payout_id, adjustment_review_notes FROM marketplace_payouts
        WHERE payout_id = :pid AND payment_type = 'Adjustment'
    """), {"pid": payout_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    if row.commission_included_in_payout_id is not None:
        raise HTTPException(
            status_code=400,
            detail="This adjustment has already been counted in a commission payout and cannot be changed.",
        )
    if body.reason in REASONS_REQUIRING_NOTE and not (row.adjustment_review_notes or "").strip():
        raise HTTPException(
            status_code=400,
            detail=f"'{body.reason.replace('_', ' ').title()}' requires a note - add one in the Notes column first.",
        )

    default_treatment = REASON_DEFAULT_TREATMENT.get(body.reason)
    if default_treatment:
        db.execute(text("""
            UPDATE marketplace_payouts
            SET adjustment_reason = :reason, adjustment_commission_treatment = :treatment
            WHERE payout_id = :pid
        """), {"reason": body.reason, "treatment": default_treatment, "pid": payout_id})
    else:
        db.execute(text("""
            UPDATE marketplace_payouts SET adjustment_reason = :reason
            WHERE payout_id = :pid
        """), {"reason": body.reason, "pid": payout_id})
    db.commit()
    return {"status": "ok", "adjustment_commission_treatment": default_treatment}
