from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from app.database import get_db
from app.auth import verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT user_id, email, name, hashed_password, role, is_active
        FROM users WHERE email = :email
    """), {"email": body.email.strip().lower()}).fetchone()
    if not row or not row.is_active or not verify_password(body.password, row.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token({"sub": str(row.user_id)})
    return {"access_token": token, "token_type": "bearer", "name": row.name, "role": row.role}


@router.get("/me")
def me(current_user=Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role,
    }
