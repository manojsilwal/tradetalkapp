from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional
from ..auth import (
    login_with_google,
    login_with_password,
    create_manual_user,
    get_current_user,
    UserInfo,
    DEV_MODE,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleLoginRequest(BaseModel):
    token: str   # Google ID token from @react-oauth/google


class SignupRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = ""


class LoginManualRequest(BaseModel):
    email: str
    password: str


@router.post("/google")
def google_login(req: GoogleLoginRequest):
    """
    Exchange a Google ID token for a TradeTalk JWT session token.
    In dev-mode (no GOOGLE_CLIENT_ID set), pass token="dev" to get a test session.
    """
    return login_with_google(req.token)


@router.post("/signup")
def signup(req: SignupRequest):
    """Create a new user profile using email, password, and optional name."""
    return create_manual_user(req.email, req.password, req.name or "")


@router.post("/login-manual")
def login_manual(req: LoginManualRequest):
    """Log in using email and password, returning a JWT session token."""
    return login_with_password(req.email, req.password)


@router.get("/me")
def me(user: UserInfo = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return {
        "user_id":  user.id,
        "email":    user.email,
        "name":     user.name,
        "avatar":   user.avatar,
        "dev_mode": DEV_MODE,
    }


@router.post("/logout")
def logout():
    """Client-side logout — just tells the client to delete its token."""
    return {"status": "logged_out"}
