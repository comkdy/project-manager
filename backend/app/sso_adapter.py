"""Company-owned SSO integration boundary, matching expert-agent's contract.

Implement both functions after the company API contract is supplied. Validate
the opaque cookie with the company SSO, or verify its signature, issuer,
audience, and expiry before returning an employee ID. Never decode an unverified
cookie as identity, trust browser profile/role headers, or log/persist raw cookies.
Directory API credentials must remain exclusively on the backend.
"""
from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class EmployeeProfile:
    employee_id: str
    name: str
    team: str


def verify_sso_cookie(cookie: str) -> str:
    """TODO: return the employee ID authenticated by the company SSO."""
    raise HTTPException(503, "사내 SSO 검증 연결이 준비되지 않았습니다. 관리자에게 문의해 주세요.")


def fetch_employee_profile(employee_id: str) -> EmployeeProfile:
    """TODO: fetch name and team from the company directory for this identity."""
    raise HTTPException(503, "사내 사용자 프로필 API 연결이 준비되지 않았습니다.")
