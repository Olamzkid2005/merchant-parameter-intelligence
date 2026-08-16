"""Auth router — opt-in access control endpoints (roadmap #1 slice 2).

Handlers moved verbatim from api.py during the router split; paths and
response shapes are unchanged.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = ""
    password: str = ""


class AuthConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    session_ttl_hours: Optional[float] = None


class AuthUserRequest(BaseModel):
    username: str = ""
    password: str = ""
    role: str = "viewer"


class AuthPasswordRequest(BaseModel):
    username: str = ""
    password: str = ""


@router.post("/api/auth/login")
def auth_login(req: LoginRequest):
    """Username + password -> expiring session cookie (mi_session)."""
    from merchant_intelligence import auth
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(status_code=400,
                            detail="username and password are required")
    cfg = auth.load_config()
    user = next((u for u in cfg["users"]
                 if u["username"].lower() == username.lower()), None)
    if not user or not auth.verify_password(req.password, user["salt"],
                                            user["hash"]):
        raise HTTPException(status_code=401,
                            detail="invalid username or password")
    token = auth.create_session(user["username"], user["role"])
    ttl = float(cfg.get("session_ttl_hours", 12)) * 3600
    resp = JSONResponse({"ok": True, "user": user["username"],
                         "role": user["role"]})
    resp.set_cookie("mi_session", token, httponly=True, samesite="lax",
                    path="/", max_age=int(ttl))
    return resp


@router.post("/api/auth/logout")
def auth_logout(request: Request):
    """Destroy the current session and clear the cookie."""
    from merchant_intelligence import auth
    token = request.cookies.get("mi_session")
    if token:
        auth.destroy_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("mi_session", path="/")
    return resp


@router.get("/api/auth/me")
def auth_me(request: Request):
    """Auth status for the UI: enabled + who am I (if logged in)."""
    from merchant_intelligence import auth
    cfg = auth.load_config()
    if not cfg.get("enabled"):
        return {"enabled": False, "authenticated": False}
    token = request.cookies.get("mi_session")
    session = auth.get_session(token) if token else None
    if not session:
        return {"enabled": True, "authenticated": False}
    return {"enabled": True, "authenticated": True,
            "user": session["username"], "role": session["role"]}


@router.get("/api/auth/config")
def auth_config():
    """Security config for the Rule Engine card (users shown without
    hashes). Write endpoints are admin-gated once access control is on."""
    from merchant_intelligence import auth
    cfg = auth.load_config()
    return {"enabled": bool(cfg.get("enabled")),
            "session_ttl_hours": cfg.get("session_ttl_hours", 12),
            "users": [{"username": u["username"], "role": u["role"]}
                      for u in cfg["users"]]}


@router.post("/api/auth/config")
def auth_save_config(req: AuthConfigRequest):
    """Toggle access control and/or the session TTL. Enabling with zero
    users would lock everyone out, so that is refused."""
    from merchant_intelligence import auth
    cfg = auth.load_config()
    if req.enabled is not None:
        cfg["enabled"] = bool(req.enabled)
        if cfg["enabled"] and not cfg["users"]:
            raise HTTPException(
                status_code=400,
                detail="add a user before enabling access control")
    if req.session_ttl_hours is not None:
        cfg["session_ttl_hours"] = max(1, min(168,
                                               float(req.session_ttl_hours)))
    auth.save_config(cfg)
    return {"ok": True, "enabled": cfg["enabled"],
            "session_ttl_hours": cfg["session_ttl_hours"]}


@router.post("/api/auth/users")
def auth_add_user(req: AuthUserRequest):
    """Create a user (bootstrap path works while access control is off)."""
    from merchant_intelligence import auth
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(status_code=400,
                            detail="username and password are required")
    if req.role not in auth.ROLES:
        raise HTTPException(status_code=400,
                            detail=f"role must be one of {auth.ROLES}")
    if len(req.password) < 8:
        raise HTTPException(status_code=400,
                            detail="password must be at least 8 characters")
    cfg = auth.load_config()
    if any(u["username"].lower() == username.lower()
           for u in cfg["users"]):
        raise HTTPException(status_code=400,
                            detail="username already exists")
    salt, pw_hash = auth.hash_password(req.password)
    cfg["users"].append({"username": username, "role": req.role,
                          "salt": salt, "hash": pw_hash})
    auth.save_config(cfg)
    return {"ok": True,
            "users": [{"username": u["username"], "role": u["role"]}
                      for u in cfg["users"]]}


@router.delete("/api/auth/users")
def auth_remove_user(req: AuthUserRequest):
    """Remove a user; disabling access control when the last user goes."""
    from merchant_intelligence import auth
    username = req.username.strip()
    cfg = auth.load_config()
    before = len(cfg["users"])
    cfg["users"] = [u for u in cfg["users"]
                     if u["username"].lower() != username.lower()]
    if len(cfg["users"]) == before:
        raise HTTPException(status_code=404, detail="username not found")
    if not cfg["users"]:
        cfg["enabled"] = False  # never leave a locked box
    auth.save_config(cfg)
    return {"ok": True, "enabled": cfg["enabled"],
            "users": [{"username": u["username"], "role": u["role"]}
                      for u in cfg["users"]]}


@router.put("/api/auth/password")
def auth_reset_password(req: AuthPasswordRequest):
    """Reset a user's password (admin-gated when access control is on)."""
    from merchant_intelligence import auth
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(status_code=400,
                            detail="username and password are required")
    if len(req.password) < 8:
        raise HTTPException(status_code=400,
                            detail="password must be at least 8 characters")
    cfg = auth.load_config()
    user = next((u for u in cfg["users"]
                 if u["username"].lower() == username.lower()), None)
    if not user:
        raise HTTPException(status_code=404, detail="username not found")
    salt, pw_hash = auth.hash_password(req.password)
    user["salt"], user["hash"] = salt, pw_hash
    auth.save_config(cfg)
    return {"ok": True}
