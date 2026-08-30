"""OIDC JWKS verification and key-rotation tests."""

import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from packages.auth.exceptions import AuthContextInvalidError
from packages.auth.oidc import OidcJwtVerifier


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _HttpClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls = 0

    async def get(self, url: str, *, timeout: float) -> _Response:
        assert url == "https://issuer.example/.well-known/jwks.json"
        assert timeout == 2.0
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return _Response(response)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_oidc_verifier_caches_jwks_and_validates_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _HttpClient([{"keys": [{"kid": "key-a", "kty": "RSA"}]}])
    monkeypatch.setattr(
        jwt,
        "get_unverified_header",
        lambda token: {"kid": "key-a", "alg": "RS256"},
    )
    monkeypatch.setattr(
        "packages.auth.oidc._jwk_to_public_key",
        lambda jwk, algorithm: f"{jwk['kid']}:{algorithm}",
    )
    monkeypatch.setattr(
        jwt,
        "decode",
        lambda token, key, algorithms, issuer, audience, options: {
            "sub": "user-a",
            "tenant_id": "tenant-a",
            "type": "access",
            "key": key,
        },
    )
    verifier = OidcJwtVerifier(
        "https://issuer.example/.well-known/jwks.json",
        algorithms=("RS256",),
        issuer="https://issuer.example",
        audience="aegisrag",
        timeout_seconds=2.0,
        client=client,  # type: ignore[arg-type]
        clock=lambda: 100.0,
    )

    first = await verifier.verify("token-a")
    second = await verifier.verify("token-a")

    assert first["key"] == "key-a:RS256"
    assert second == first
    assert client.calls == 1


@pytest.mark.asyncio
async def test_oidc_verifier_refreshes_cache_once_for_rotated_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _HttpClient(
        [
            {"keys": [{"kid": "old", "kty": "RSA"}]},
            {"keys": [{"kid": "new", "kty": "RSA"}]},
        ]
    )
    monkeypatch.setattr(
        jwt,
        "get_unverified_header",
        lambda token: {"kid": "new", "alg": "RS256"},
    )
    monkeypatch.setattr(
        "packages.auth.oidc._jwk_to_public_key",
        lambda jwk, algorithm: f"{jwk['kid']}:{algorithm}",
    )
    monkeypatch.setattr(
        jwt,
        "decode",
        lambda token, key, algorithms, issuer, audience, options: {"key": key},
    )
    verifier = OidcJwtVerifier(
        "https://issuer.example/.well-known/jwks.json",
        algorithms=("RS256",),
        issuer="https://issuer.example",
        audience="aegisrag",
        client=client,  # type: ignore[arg-type]
        clock=lambda: 100.0,
    )

    claims = await verifier.verify("token-new")

    assert claims["key"] == "new:RS256"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_oidc_verifier_rejects_unknown_algorithm_and_bad_jwks() -> None:
    client = _HttpClient([{"keys": []}])
    verifier = OidcJwtVerifier(
        "https://issuer.example/.well-known/jwks.json",
        algorithms=("RS256",),
        issuer="https://issuer.example",
        audience="aegisrag",
        client=client,  # type: ignore[arg-type]
    )

    with pytest.raises(AuthContextInvalidError) as exc_info:
        await verifier.verify("token")

    assert exc_info.value.details["reason"] in {
        "oidc_token_header_invalid",
        "oidc_signing_key_not_found",
    }


@pytest.mark.asyncio
async def test_oidc_verifier_checks_real_signature_issuer_and_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "real-key", "alg": "RS256", "use": "sig"})
    client = _HttpClient([{"keys": [public_jwk]}])
    verifier = OidcJwtVerifier(
        "https://issuer.example/.well-known/jwks.json",
        algorithms=("RS256",),
        issuer="https://issuer.example",
        audience="aegisrag",
        client=client,  # type: ignore[arg-type]
    )
    token = jwt.encode(
        {
            "sub": "user-a",
            "tenant_id": "tenant-a",
            "iss": "https://issuer.example",
            "aud": "aegisrag",
            "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "real-key"},
    )

    claims = await verifier.verify(token)

    assert claims["sub"] == "user-a"

    bad_audience = jwt.encode(
        {
            "sub": "user-a",
            "tenant_id": "tenant-a",
            "iss": "https://issuer.example",
            "aud": "another-client",
            "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "real-key"},
    )
    with pytest.raises(AuthContextInvalidError):
        await verifier.verify(bad_audience)
