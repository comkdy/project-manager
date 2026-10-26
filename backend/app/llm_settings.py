"""Shared LLM transport settings and private, account-scoped credentials."""
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException
import httpx
from pydantic import SecretStr, field_validator
from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool

from .auth import get_current_user
from .credential_store import secret_cipher
from .db import SessionLocal, settings
from .models import Audit, LlmCredential, uid, utcnow
from .schemas import StrictModel

router = APIRouter(prefix="/api/llm", tags=["personal LLM settings"])
KEY_FILE = Path(__file__).resolve().parents[2] / ".local" / "llm-credentials.key"


class KeyInput(StrictModel):
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        key = value.get_secret_value().strip()
        if not 1 <= len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError("API 키는 공백·줄바꿈 없는 1~8192자의 영문·숫자·기호로 입력하세요.")
        return SecretStr(key)


def llm_configuration():
    return settings.llm_base_url.strip().rstrip("/"), settings.llm_model.strip()


def config_fingerprint(base_url, model):
    return hashlib.sha256(json.dumps([base_url, model]).encode()).hexdigest()


class LlmEndpointError(ValueError):
    """Safe configuration diagnostics without endpoint or credential values."""


def validate_llm_url(base_url):
    """Use the administrator's endpoint regardless of public/private addressing."""
    base_url = base_url.strip().rstrip("/")
    try:
        url = urlsplit(base_url)
        port = url.port
    except ValueError:
        raise LlmEndpointError("LLM 서버 주소의 형식과 포트를 확인해 주세요.") from None
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise LlmEndpointError("LLM_BASE_URL은 인증 정보나 쿼리가 없는 HTTP(S) 주소여야 합니다.")
    try:
        addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(
            url.hostname, port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except (OSError, ValueError):
        raise LlmEndpointError("LLM 서버의 주소를 확인할 수 없습니다.") from None
    addresses = {a.ipv4_mapped or a if isinstance(a, ipaddress.IPv6Address) else a for a in addresses}
    if not addresses or any(a.is_link_local or a.is_unspecified or a.is_multicast for a in addresses):
        raise LlmEndpointError("링크 로컬·미지정·멀티캐스트 주소는 LLM 서버로 사용할 수 없습니다.")
    return base_url if url.path.endswith("/chat/completions") else base_url + "/chat/completions"


def cipher(*, create=False):
    return secret_cipher(settings.llm_credentials_key, KEY_FILE,
                         "LLM API 키 암호화 설정을 확인해 주세요.", create=create)


def encrypt_key(user_id, key):
    # Bind the encrypted value to its owner, even if ciphertext is moved between rows.
    return cipher(create=True).encrypt(json.dumps({"user_id": user_id, "key": key}).encode()).decode()


def load_key(user_id):
    with SessionLocal() as db:
        item = db.get(LlmCredential, user_id)
        if item is None:
            raise HTTPException(400, "프로필의 LLM API 키 설정에서 본인 키를 등록해 주세요.")
        try:
            value = json.loads(cipher().decrypt(item.encrypted_key.encode()).decode())
            if value["user_id"] != user_id or not isinstance(value["key"], str) or not value["key"]:
                raise ValueError()
            return value["key"], item.revision
        except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
            raise HTTPException(503, "저장된 LLM API 키를 복원할 수 없습니다. 서버 암호화 설정을 확인하거나 키를 다시 등록해 주세요.") from None


def credential_status(user_id):
    base_url, model = llm_configuration()
    try:
        parsed = urlsplit(base_url)
        safe_url = urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
    except ValueError:
        safe_url = ""
    with SessionLocal() as db:
        item = db.get(LlmCredential, user_id)
        current_check = item is not None and item.checked_config == config_fingerprint(base_url, model)
        return {
            "server_configured": bool(base_url and model),
            "configured": bool(base_url and model and item),
            "key_registered": item is not None,
            "base_url": safe_url,
            "model": model,
            "status": item.check_status if current_check else "unchecked" if item else "not_registered",
            "checked_at": item.checked_at if current_check else None,
            "message": item.check_message if current_check else "",
        }


@router.get("/status")
def status(user=Depends(get_current_user)):
    return credential_status(user.id)


@router.put("/credential")
def save_key(body: KeyInput, user=Depends(get_current_user)):
    values = dict(encrypted_key=encrypt_key(user.id, body.api_key.get_secret_value()),
                  revision=uid(), updated_at=utcnow(), checked_at=None, checked_config=None,
                  check_status="unchecked", check_message="")
    with SessionLocal.begin() as db:
        db.execute(insert(LlmCredential).values(user_id=user.id, **values).on_conflict_do_update(
            index_elements=[LlmCredential.user_id], set_=values))
        db.add(Audit(user_id=user.id, action="llm.key_saved", entity_id=user.id, details={}))
    return credential_status(user.id)


@router.delete("/credential")
def remove_key(user=Depends(get_current_user)):
    with SessionLocal.begin() as db:
        db.execute(delete(LlmCredential).where(LlmCredential.user_id == user.id))
        db.add(Audit(user_id=user.id, action="llm.key_deleted", entity_id=user.id, details={}))
    return credential_status(user.id)


def check_http_error(status_code):
    if status_code in {401, 403}:
        return "인증에 실패했습니다. API 키와 모델 사용 권한을 확인해 주세요."
    if status_code == 429:
        return "LLM 사용 한도 또는 요청 제한에 도달했습니다. 잠시 후 다시 확인해 주세요."
    if status_code == 404:
        return "LLM endpoint 또는 모델명을 확인해 주세요."
    return f"LLM 요청이 실패했습니다 (HTTP {status_code})."


def record_check(user_id, revision, fingerprint, succeeded, message):
    with SessionLocal.begin() as db:
        changed = db.execute(update(LlmCredential).where(
            LlmCredential.user_id == user_id, LlmCredential.revision == revision).values(
                checked_at=utcnow(), checked_config=fingerprint,
                check_status="connected" if succeeded else "error", check_message=message))
        if changed.rowcount != 1:
            raise HTTPException(409, "확인 중 API 키가 변경되었습니다. 현재 키로 다시 확인해 주세요.")
        db.add(Audit(user_id=user_id, action="llm.connection_checked", entity_id=user_id,
                     details={"connected": succeeded}))
    return credential_status(user_id)


@router.post("/check")
async def check_connection(user=Depends(get_current_user)):
    base_url, model = llm_configuration()
    if not base_url or not model:
        raise HTTPException(400, "서버의 LLM_BASE_URL과 LLM_MODEL을 먼저 설정해 주세요.")
    key, revision = await run_in_threadpool(load_key, user.id)
    succeeded, message = False, ""
    try:
        endpoint = await run_in_threadpool(validate_llm_url, base_url)
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5), trust_env=False,
                                     follow_redirects=False) as client:
            response = await client.post(endpoint, headers={"Authorization": f"Bearer {key}"}, json={
                "model": model, "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 128, "stream": False})
        if response.status_code != 200:
            message = check_http_error(response.status_code)
        else:
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            succeeded, message = True, "연결을 확인했습니다."
    except httpx.TimeoutException:
        message = "연결 확인 시간이 초과되었습니다. 잠시 후 다시 확인해 주세요."
    except httpx.HTTPError:
        message = "LLM 서버에 연결할 수 없습니다. 서버 주소와 실행 상태를 확인해 주세요."
    except LlmEndpointError as exc:
        message = str(exc)
    except (ValueError, KeyError, IndexError, TypeError):
        message = "LLM endpoint 설정 또는 응답 형식을 확인해 주세요."
    return await run_in_threadpool(record_check, user.id, revision,
                                  config_fingerprint(base_url, model), succeeded, message)
