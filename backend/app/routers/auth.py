import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr

from ..core.database import get_db
from ..core.security import hash_password, verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    confirm_password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _validate_password(password: str):
    if len(password) < 8:
        raise HTTPException(400, "Mật khẩu phải có ít nhất 8 ký tự")
    if not re.search(r"[A-Z]", password) or not re.search(r"[0-9]", password):
        raise HTTPException(400, "Mật khẩu phải chứa ít nhất 1 chữ hoa và 1 chữ số")


@router.post("/register")
def register(payload: RegisterRequest):
    if payload.password != payload.confirm_password:
        raise HTTPException(400, "Xác nhận mật khẩu không khớp")
    _validate_password(payload.password)

    with get_db() as db:
        existing = db.execute("SELECT id FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            raise HTTPException(400, "Email đã được sử dụng")

        cur = db.execute(
            "INSERT INTO users (full_name, email, password_hash, role_id) VALUES (?, ?, ?, "
            "(SELECT id FROM roles WHERE name = 'STUDENT'))",
            (payload.full_name, payload.email, hash_password(payload.password)),
        )
        user_id = cur.lastrowid

    token = create_access_token({"sub": str(user_id)})
    return {"access_token": token, "token_type": "bearer"}


@router.post("/login")
def login(payload: LoginRequest):
    with get_db() as db:
        row = db.execute(
            "SELECT u.id, u.password_hash, u.status, r.name as role "
            "FROM users u JOIN roles r ON u.role_id = r.id WHERE u.email = ?",
            (payload.email,),
        ).fetchone()

    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(401, "Email hoặc mật khẩu không đúng")
    if row["status"] != "active":
        raise HTTPException(403, "Tài khoản đã bị khóa")

    with get_db() as db:
        db.execute("INSERT INTO activity_logs (user_id, action, detail) VALUES (?, 'login', '')",
                   (row["id"],))

    token = create_access_token({"sub": str(row["id"])})
    return {"access_token": token, "token_type": "bearer", "role": row["role"]}


@router.post("/logout")
def logout(user: dict = Depends(get_current_user)):
    with get_db() as db:
        db.execute("INSERT INTO activity_logs (user_id, action, detail) VALUES (?, 'logout', '')",
                   (user["id"],))
    return {"message": "Đăng xuất thành công"}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    date_of_birth: Optional[str] = None  # định dạng YYYY-MM-DD
    gender: Optional[str] = None
    major: Optional[str] = None
    student_code: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None


@router.put("/me")
def update_me(payload: UpdateProfileRequest, user: dict = Depends(get_current_user)):
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "Không có thông tin nào để cập nhật")

    if "full_name" in fields and not fields["full_name"].strip():
        raise HTTPException(400, "Họ tên không được để trống")

    set_clause = ", ".join(f"{col} = ?" for col in fields.keys())
    values = list(fields.values()) + [user["id"]]

    with get_db() as db:
        db.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        db.execute(
            "INSERT INTO activity_logs (user_id, action, detail) VALUES (?, 'update_profile', '')",
            (user["id"],),
        )
        row = db.execute(
            "SELECT u.id, u.full_name, u.email, r.name as role, u.status, "
            "u.date_of_birth, u.gender, u.major, u.student_code, u.phone, "
            "u.address, u.created_at "
            "FROM users u JOIN roles r ON u.role_id = r.id WHERE u.id = ?",
            (user["id"],),
        ).fetchone()

    return dict(row)
