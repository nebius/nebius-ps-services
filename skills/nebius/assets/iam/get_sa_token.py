"""Snippet: exchange a service-account JWT for a short-lived IAM access token."""

from __future__ import annotations

import time

import jwt
from nebius.api.nebius.iam.v1 import ExchangeTokenRequest, TokenExchangeServiceClient

GRANT_TYPE_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
REQUESTED_TOKEN_TYPE_ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
SUBJECT_TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"


def _wait(op_or_message):  # type: ignore[no-untyped-def]
    return op_or_message.wait() if hasattr(op_or_message, "wait") else op_or_message


def exchange_service_account_token(
    *,
    sdk,
    service_account_id: str,
    auth_public_key_id: str,
    private_key_pem: str,
    audience: str | None = None,
    scopes: list[str] | None = None,
) -> tuple[str, int]:
    """
    Returns (access_token, expires_in_seconds).

    Requires:
    - existing IAM auth public key id (`auth_public_key_id`) for the service account
    - matching private key PEM (`private_key_pem`)
    """
    now = int(time.time())
    payload = {
        "iss": service_account_id,
        "sub": service_account_id,
        "iat": now,
        "exp": now + 3600,
    }
    if audience:
        payload["aud"] = audience

    signed_jwt = jwt.encode(
        payload,
        private_key_pem,
        algorithm="RS256",
        headers={"kid": auth_public_key_id},
    )

    request_kwargs: dict[str, object] = {
        "grant_type": GRANT_TYPE_TOKEN_EXCHANGE,
        "requested_token_type": REQUESTED_TOKEN_TYPE_ACCESS_TOKEN,
        "subject_token": signed_jwt,
        "subject_token_type": SUBJECT_TOKEN_TYPE_JWT,
    }
    if audience:
        request_kwargs["audience"] = audience
    if scopes:
        request_kwargs["scopes"] = scopes

    response = _wait(
        TokenExchangeServiceClient(sdk).exchange(
            ExchangeTokenRequest(**request_kwargs),
        )
    )

    access_token = str(getattr(response, "access_token", "") or "").strip()
    if not access_token:
        raise RuntimeError("Token exchange succeeded but access_token is empty")
    expires_in = int(getattr(response, "expires_in", 0) or 0)
    return access_token, expires_in
