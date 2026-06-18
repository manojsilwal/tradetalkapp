from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional
from ..auth import (
    login_with_google,
    login_with_password,
    create_manual_user,
    signup_with_google,
    complete_set_password,
    complete_login_with_otp,
    get_current_user,
    _user_profile_payload,
    UserInfo,
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


class SetPasswordRequest(BaseModel):
    setup_token: str
    password: str


class VerifyOtpRequest(BaseModel):
    otp_session_id: str
    code: str


@router.post("/google")
def google_login(req: GoogleLoginRequest):
    """
    Exchange a Google ID token for a TradeTalk JWT session token.
    In dev-mode (no GOOGLE_CLIENT_ID set), pass token="dev" to get a test session.
    """
    return login_with_google(req.token)


@router.post("/google/signup")
def google_signup(req: GoogleLoginRequest):
    """
    Google account creation — returns a setup token to set password before sign-in.
    """
    return signup_with_google(req.token)


@router.post("/set-password")
def set_password(req: SetPasswordRequest):
    """Set password after Google signup (requires setup_token from /auth/google/signup)."""
    return complete_set_password(req.setup_token, req.password)


@router.post("/signup")
def signup(req: SignupRequest):
    """Create a new user profile using email, password, and optional name."""
    return create_manual_user(req.email, req.password, req.name or "")


@router.post("/login-manual")
def login_manual(req: LoginManualRequest):
    """Verify email/password and send email OTP (step 1 of sign-in)."""
    return login_with_password(req.email, req.password)


@router.post("/verify-otp")
def verify_otp_route(req: VerifyOtpRequest):
    """Verify email OTP and return JWT session token (step 2 of sign-in)."""
    return complete_login_with_otp(req.otp_session_id, req.code)


@router.get("/me")
def me(user: UserInfo = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return _user_profile_payload(user)


@router.post("/logout")
def logout():
    """Client-side logout — just tells the client to delete its token."""
    return {"status": "logged_out"}
