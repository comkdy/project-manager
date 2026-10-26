"""Encrypted DB credentials; the encryption key lives outside the metadata DB."""
import os
from pathlib import Path
import threading

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from .db import settings

KEY_FILE = Path(__file__).resolve().parents[2] / ".local" / "db-credentials.key"
_key_lock = threading.Lock()


def secret_cipher(key_value, key_file, error_message, *, create=False):
    try:
        key = key_value.strip().encode()
        if not key:
            with _key_lock:
                if create and not key_file.exists():
                    key_file.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation prevents accidental key replacement.
                    try:
                        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        pass
                    else:
                        with os.fdopen(fd, "wb") as target:
                            target.write(Fernet.generate_key())
                key = key_file.read_bytes().strip()
        return Fernet(key)
    except (OSError, ValueError):
        raise HTTPException(503, error_message) from None


def cipher(*, create=False):
    return secret_cipher(settings.db_credentials_key, KEY_FILE,
                         "DB 접속 정보 암호화 키를 확인해 주세요.", create=create)


def encrypt_url(url):
    return cipher(create=True).encrypt(url.render_as_string(hide_password=False).encode()).decode()


def decrypt_url(value):
    from sqlalchemy.engine import make_url
    try:
        return make_url(cipher().decrypt(value.encode()).decode())
    except (InvalidToken, ValueError, UnicodeError):
        raise HTTPException(503, "저장된 DB 접속 정보를 복원할 수 없습니다. 암호화 키를 확인하거나 비밀번호를 다시 입력하세요.") from None
