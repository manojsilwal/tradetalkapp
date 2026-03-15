from fastapi import APIRouter, Depends
from pydantic import BaseModel
from ..auth import login_with_google, get_current_user, UserInfo, DEV_MODE

router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleLoginRequest(BaseModel):
    token: str   # Google ID token from @react-oauth/google


@router.post("/google")
def google_login(req: GoogleLoginRequest):
    """
    Exchange a Google ID token for a K2-Optimus JWT session token.
    In dev-mode (no GOOGLE_CLIENT_ID set), pass token="dev" to get a test session.
    """
    return login_with_google(req.token)


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
