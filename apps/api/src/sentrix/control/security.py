"""控制面口令与会话令牌。只用标准库，3–5 个固定账号不引入认证平台。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

PASSWORD_ALGO = "scrypt"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024


def hash_password(password: str) -> tuple[str, str, str]:
    """返回 (salt_b64, hash_b64, params_json)。盐每账号随机。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    params = f'{{"n":{SCRYPT_N},"r":{SCRYPT_R},"p":{SCRYPT_P},"dklen":{SCRYPT_DKLEN}}}'
    return _b64(salt), _b64(digest), params


def verify_password(
    password: str,
    *,
    salt_b64: str,
    hash_b64: str,
    params_json: str,
    algo: str = PASSWORD_ALGO,
) -> bool:
    if algo != PASSWORD_ALGO:
        return False
    import json

    try:
        params = json.loads(params_json or "{}")
        n = int(params.get("n", SCRYPT_N))
        r = int(params.get("r", SCRYPT_R))
        p = int(params.get("p", SCRYPT_P))
        dklen = int(params.get("dklen", SCRYPT_DKLEN))
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
    except (ValueError, TypeError):
        return False
    try:
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=dklen,
            maxmem=SCRYPT_MAXMEM,
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)
