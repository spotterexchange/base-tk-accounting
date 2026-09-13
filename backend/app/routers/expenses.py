from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user

router = APIRouter(prefix="/expenses", tags=["expenses"])


class ExpenseCreate(BaseModel):
    purchaser_id: int
    expense_date: date
    description: str = Field(min_length=1)
    amount: float


class ExpenseUpdate(BaseModel):
    expense_date: date
    description: str = Field(min_length=1)
    amount: float


def _get_locked(db: Session, expense_id: int):
    """Return (expense_row, locked). An expense is locked while it's claimed
    by a commission batch (rollback releases the claim, so a rolled-back
    batch never holds a lock)."""
    row = db.execute(text("""
        SELECT e.expense_id, e.included_in_payout_id, pp.status AS batch_status
        FROM purchaser_expenses e
        LEFT JOIN purchaser_payouts pp ON pp.purchaser_payout_id = e.included_in_payout_id
        WHERE e.expense_id = :eid
    """), {"eid": expense_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expense not found")
    return row, row.included_in_payout_id is not None


@router.get("")
def list_expenses(purchaser_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT e.expense_id, e.purchaser_id, e.expense_date, e.description,
               e.amount, e.included_in_payout_id, e.created_by, e.created_at,
               pp.status AS batch_status, pp.payout_date AS batch_payout_date
        FROM purchaser_expenses e
        LEFT JOIN purchaser_payouts pp ON pp.purchaser_payout_id = e.included_in_payout_id
        WHERE e.purchaser_id = :pid
        ORDER BY e.expense_date DESC, e.expense_id DESC
    """), {"pid": purchaser_id}).fetchall()
    return [
        {**dict(r._mapping), "locked": r.included_in_payout_id is not None}
        for r in rows
    ]


@router.post("")
def create_expense(body: ExpenseCreate, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    purchaser = db.execute(text(
        "SELECT purchaser_id FROM purchasers WHERE purchaser_id = :pid"
    ), {"pid": body.purchaser_id}).fetchone()
    if not purchaser:
        raise HTTPException(status_code=404, detail="Purchaser not found")
    row = db.execute(text("""
        INSERT INTO purchaser_expenses (purchaser_id, expense_date, description, amount, created_by)
        VALUES (:pid, :edate, :descr, :amount, :by)
        RETURNING expense_id
    """), {
        "pid": body.purchaser_id,
        "edate": body.expense_date,
        "descr": body.description.strip(),
        "amount": round(body.amount, 2),
        "by": user.name,
    }).fetchone()
    db.commit()
    return {"expense_id": row.expense_id}


@router.put("/{expense_id}")
def update_expense(expense_id: int, body: ExpenseUpdate, db: Session = Depends(get_db)):
    _, locked = _get_locked(db, expense_id)
    if locked:
        raise HTTPException(status_code=400,
                            detail="Expense is locked to a commission batch - roll the batch back to edit it")
    db.execute(text("""
        UPDATE purchaser_expenses
        SET expense_date = :edate, description = :descr, amount = :amount
        WHERE expense_id = :eid
    """), {
        "eid": expense_id,
        "edate": body.expense_date,
        "descr": body.description.strip(),
        "amount": round(body.amount, 2),
    })
    db.commit()
    return {"status": "updated"}


@router.delete("/{expense_id}")
def delete_expense(expense_id: int, db: Session = Depends(get_db)):
    _, locked = _get_locked(db, expense_id)
    if locked:
        raise HTTPException(status_code=400,
                            detail="Expense is locked to a commission batch - roll the batch back to delete it")
    db.execute(text(
        "DELETE FROM purchaser_expenses WHERE expense_id = :eid"
    ), {"eid": expense_id})
    db.commit()
    return {"status": "deleted"}
