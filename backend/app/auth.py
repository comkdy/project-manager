"""Verified company identity and live application permissions.

Local automatic administrator access is explicitly limited to demo mode and
loopback clients. SSO never reads legacy password sessions or falls back to it.
"""
import ipaddress
import re
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from . import sso_adapter
from .db import SessionLocal, settings
from .models import Audit, User, utcnow
from .schemas import StrictModel

router = APIRouter(prefix="/api/auth", tags=["auth"])
LOCAL_ADMIN_USERNAME = "pm-demo-admin"
EMPLOYEE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


class UserCreate(StrictModel):
    employee_id: str = Field(pattern=EMPLOYEE_PATTERN, max_length=64)
    role: Literal["admin", "editor", "viewer"] = "viewer"
    active: bool = True


class UserUpdate(StrictModel):
    role: Literal["admin", "editor", "viewer"] | None = None
    active: bool | None = None
    expected_version: int = Field(ge=1)


def user_json(user):
    return {key: getattr(user, key) for key in (
        "id", "username", "employee_id", "name", "team", "role", "active", "profile_source", "version"
    )}


def employee_id(value):
    if not isinstance(value, str) or not re.fullmatch(EMPLOYEE_PATTERN, value):
        raise HTTPException(503, "사내 SSO 사번 형식을 확인해야 합니다.")
    return value


def auth_mode():
    if settings.auth_mode not in {"local_admin", "sso"}:
        raise HTTPException(503, "로그인 방식 설정을 확인해 주세요.")
    return settings.auth_mode


def local_user(request: Request):
    if not settings.demo_mode:
        raise HTTPException(503, "테스트 관리자 접속은 DEMO_MODE 환경에서만 사용할 수 있습니다.")
    try:
        loopback = bool(request.client) and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise HTTPException(403, "테스트 관리자 접속은 이 PC의 로컬 요청에서만 사용할 수 있습니다.")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == LOCAL_ADMIN_USERNAME))
        if not user or user.profile_source != "local" or user.employee_id is not None:
            raise HTTPException(503, "테스트 관리자 계정의 초기화를 확인해 주세요.")
        if not user.active:
            raise HTTPException(403, "테스트 관리자 계정이 비활성화되어 있습니다.")
        return user


def sso_user(request: Request):
    cookie_name = settings.sso_cookie_name.strip()
    if not cookie_name:
        raise HTTPException(503, "사내 SSO 쿠키 이름을 설정해야 합니다.")
    cookie = request.cookies.get(cookie_name)
    if not cookie:
        raise HTTPException(401, "사내 SSO 로그인이 필요합니다. 사내 로그인 후 다시 확인해 주세요.")
    if len(cookie) > 16384:
        raise HTTPException(401, "사내 SSO 인증 정보를 확인할 수 없습니다.")
    try:
        verified_id = employee_id(sso_adapter.verify_sso_cookie(cookie))
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.employee_id == verified_id))
            if user and (not user.active or user.profile_source != "sso"):
                raise HTTPException(403, "사용이 중지되었거나 SSO에 연결되지 않은 계정입니다.")
            stale = not user or not user.profile_synced_at or utcnow() - user.profile_synced_at > timedelta(hours=1)
        if stale:
            profile = sso_adapter.fetch_employee_profile(verified_id)
            if (profile.employee_id != verified_id or not isinstance(profile.name, str)
                    or not profile.name.strip() or len(profile.name) > 120
                    or not isinstance(profile.team, str) or len(profile.team) > 200):
                raise HTTPException(503, "사내 사용자 프로필 응답을 확인해야 합니다.")
            with SessionLocal.begin() as db:
                # Serialize first visits by identity; never replace role/active.
                db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": "identity:" + verified_id})
                user = db.scalar(select(User).where(User.employee_id == verified_id).with_for_update())
                if user is None:
                    user = User(username="sso:" + verified_id, employee_id=verified_id, role="viewer",
                                active=True, profile_source="sso", password_hash="")
                    db.add(user)
                    db.flush()
                    db.add(Audit(user_id=user.id, action="user.sso_provision", entity_id=user.id,
                                 details={"employee_id": verified_id, "role": "viewer"}))
                if not user.active or user.profile_source != "sso":
                    raise HTTPException(403, "사용이 중지된 계정입니다.")
                user.name, user.team = profile.name.strip(), profile.team.strip()
                user.profile_synced_at = utcnow()
        # Re-read permissions after directory I/O; profile cache never caches access.
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.employee_id == verified_id))
            if not user or not user.active or user.profile_source != "sso":
                raise HTTPException(403, "사용이 중지된 계정입니다.")
            return user
    except HTTPException:
        raise
    except Exception:
        # Never reveal cookies, private URLs, provider bodies or credentials.
        raise HTTPException(503, "사내 인증 또는 사용자 프로필 조회를 완료하지 못했습니다.") from None


def get_current_user(request: Request) -> User:
    return local_user(request) if auth_mode() == "local_admin" else sso_user(request)


def require_editor(user: User = Depends(get_current_user)):
    if not user.active or user.role not in {"admin", "editor"}:
        raise HTTPException(403, "편집자 권한이 필요합니다.")
    return user


def require_admin(user: User = Depends(get_current_user)):
    if not user.active or user.role != "admin":
        raise HTTPException(403, "관리자 권한이 필요합니다.")
    return user


def lock_user_management(db, actor):
    # One lock protects the last-admin invariant across different target rows.
    db.execute(text("SELECT pg_advisory_xact_lock(7126001902)"))
    live = db.get(User, actor.id, populate_existing=True)
    if not live or not live.active or live.role != "admin":
        raise HTTPException(403, "관리자 권한이 변경되었습니다. 사용자 정보를 다시 확인해 주세요.")


@router.get("/session")
def session(request: Request):
    mode = auth_mode()
    try:
        return {"mode": mode, "user": user_json(get_current_user(request)), "status": "authenticated"}
    except HTTPException as error:
        status = {401: "unauthenticated", 403: "disabled"}.get(error.status_code, "unavailable")
        return {"mode": mode, "user": None, "status": status, "message": str(error.detail)}
    except SQLAlchemyError:
        return {"mode": mode, "user": None, "status": "unavailable", "message": "사용자 정보를 불러오지 못했습니다."}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_json(user)


@router.post("/login")
def login():
    raise HTTPException(410, "앱 자체 비밀번호 로그인은 지원하지 않습니다. 사내 SSO로 접속해 주세요.")


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("wafer_session", path="/")
    return {"ok": True, "message": "사내 SSO 로그아웃은 사내 인증 서비스에서 관리합니다."}
