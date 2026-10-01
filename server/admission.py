"""Versioned relay proofs; only opaque identity metadata crosses this boundary."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from uuid import UUID


HEADER = "X-Ask-Admission"
FIELDS = {"v", "sub", "tier", "attempt", "exp", "method", "path", "body_sha256"}
MESSAGES = {
    "admission_required": "请从问问立正页面发起请求。",
    "invalid_admission": "这次请求的身份验证已失效，请重新发起。",
    "quota_unavailable": "使用额度暂时无法核对，请稍后再试。",
    "quota_exhausted": "今天的回答额度已用完或正在使用，请稍后再试。",
    "attempt_replayed": "这次请求已提交过，请重新发起。",
}


class AdmissionError(Exception):
    def __init__(self, code: str, status: int = 403, quota: dict | None = None):
        self.code, self.status, self.quota = code, status, quota
        super().__init__(code)

    def payload(self) -> dict:
        result = {"code": self.code, "message": MESSAGES[self.code]}
        if self.quota is not None:
            result.update(quota=self.quota, remaining=self.quota["remaining"], reset_at=self.quota["reset_at"])
        return result


@dataclass(frozen=True)
class Principal:
    subject: str
    tier: str
    attempt: str


def _decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid encoding")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate claim")
        result[key] = value
    return result


def verify_proof(proof: str | None, secret: str, method: str, path: str, body: bytes, now: float | None = None) -> Principal:
    if len(secret.encode("utf-8")) < 32:
        raise AdmissionError("quota_unavailable", 503)
    if not proof:
        raise AdmissionError("admission_required")
    try:
        if len(proof) > 2048:
            raise ValueError("proof bound")
        version, encoded, signature = proof.split(".")
        expected = hmac.new(secret.encode("utf-8"), ("v1." + encoded).encode("ascii"), hashlib.sha256).digest()
        if version != "v1" or not hmac.compare_digest(expected, _decode(signature)):
            raise ValueError("signature")
        claims = json.loads(_decode(encoded), object_pairs_hook=_unique_object)
        clock = time.time() if now is None else now
        if not isinstance(claims, dict) or set(claims) != FIELDS or type(claims["v"]) is not int or claims["v"] != 1:
            raise ValueError("claims")
        if type(claims["exp"]) is not int or not clock < claims["exp"] <= clock + 120:
            raise ValueError("expiry")
        if not isinstance(claims["sub"], str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,160}", claims["sub"]):
            raise ValueError("subject")
        if claims["tier"] not in {"public", "founding"}:
            raise ValueError("tier")
        if not isinstance(claims["attempt"], str) or str(UUID(claims["attempt"])) != claims["attempt"]:
            raise ValueError("attempt")
        if (method, path) not in {("POST", "/api/ask"), ("GET", "/api/quota")} or claims["method"] != method or claims["path"] != path:
            raise ValueError("destination")
        if not isinstance(claims["body_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", claims["body_sha256"]):
            raise ValueError("body hash")
        if not hmac.compare_digest(claims["body_sha256"], hashlib.sha256(body).hexdigest()):
            raise ValueError("body binding")
        return Principal(claims["sub"], claims["tier"], claims["attempt"])
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise AdmissionError("invalid_admission") from None
