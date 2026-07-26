from __future__ import annotations

import base64
import json

import pytest

from src.auth import UserManager, decode_google_id_token_claims


def make_id_token(payload: dict[str, str]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_decode_google_id_token_claims_handles_profile_picture() -> None:
    token = make_id_token({"picture": "https://lh3.googleusercontent.com/avatar"})
    assert decode_google_id_token_claims(token)["picture"].endswith("/avatar")
    assert decode_google_id_token_claims("invalid") == {}


@pytest.mark.asyncio
async def test_google_verification_uses_verified_claim_picture_fallback(monkeypatch) -> None:
    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"aud": "client-id", "email": "person@example.com", "sub": "google-id"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("src.auth.httpx.AsyncClient", Client)
    token = make_id_token(
        {
            "picture": "https://lh3.googleusercontent.com/avatar",
            "given_name": "Ada",
            "family_name": "Lovelace",
        }
    )
    manager = UserManager(
        engine=object(),  # type: ignore[arg-type]
        session_secret="test-secret",
        google_client_id="client-id",
    )

    result = await manager._verify_google_token(token)

    assert result["picture"] == "https://lh3.googleusercontent.com/avatar"
    assert result["given_name"] == "Ada"
