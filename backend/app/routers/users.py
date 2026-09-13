from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional

from app.database import get_db
from app.auth import get_password_hash, require_admin

router = APIRouter(prefix="/users", tags=["users"],
                   dependencies=[Depends(require_admin)])


class UserCreate(BaseModel):
    email: str = Field(min_length=3)
    name: str = Field(min_length=1)
    password: str = Field(min_length=8)
    role: str = "user"


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=8)


@router.get("")
def list_users(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT user_id, email, name, role, is_active, created_at
        FROM users ORDER BY user_id
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("")
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    if body.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'admin'")
    email = body.email.strip().lower()
    existing = db.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": email}).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="A user with that email already exists")
    row = db.execute(text("""
        INSERT INTO users (email, name, hashed_password, role)
        VALUES (:e, :n, :h, :r)
        RETURNING user_id, email, name, role, is_active, created_at
    """), {"e": email, "n": body.name.strip(),
           "h": get_password_hash(body.password), "r": body.role}).fetchone()
    db.commit()
    return dict(row._mapping)


@router.put("/{user_id}")
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db),
                admin=Depends(require_admin)):
    target = db.execute(text("SELECT user_id FROM users WHERE user_id = :id"),
                        {"id": user_id}).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Lockout guards: you can't strip your own access. (Another admin can,
    # which keeps mistaken self-inflicted lockouts impossible without
    # blocking legitimate admin changes.)
    if user_id == admin.user_id:
        if body.is_active is False:
            raise HTTPException(status_code=400, detail="You can't deactivate your own account")
        if body.role is not None and body.role != "admin":
            raise HTTPException(status_code=400, detail="You can't remove your own admin role")

    fields, params = [], {"id": user_id}
    if body.name is not None:
        fields.append("name = :name"); params["name"] = body.name.strip()
    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(status_code=400, detail="Role must be 'user' or 'admin'")
        fields.append("role = :role"); params["role"] = body.role
    if body.is_active is not None:
        fields.append("is_active = :active"); params["active"] = body.is_active
    if fields:
        db.execute(text(f"UPDATE users SET {', '.join(fields)} WHERE user_id = :id"), params)
        db.commit()
    row = db.execute(text("""
        SELECT user_id, email, name, role, is_active, created_at FROM users WHERE user_id = :id
    """), {"id": user_id}).fetchone()
    return dict(row._mapping)


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, body: PasswordReset, db: Session = Depends(get_db)):
    target = db.execute(text("SELECT user_id FROM users WHERE user_id = :id"),
                        {"id": user_id}).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    db.execute(text("UPDATE users SET hashed_password = :h WHERE user_id = :id"),
               {"h": get_password_hash(body.password), "id": user_id})
    db.commit()
    return {"status": "password reset"}
