"""Asynchronous OIDC JWT verification with bounded JWKS caching."""

# pattern: Imperative Shell

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

import httpx
import jwt
from jwt.algorithms import Algorithm
from jwt.exceptions import InvalidSignatureError, InvalidTokenError
from jwt.types import Options

from packages.auth.exceptions import AuthContextInvalidError


class OidcJwtVerifier:
    """Verify signed OIDC tokens and refresh keys on kid rotation."""

    def __init__(
        self,
        jwks_url: str,
        *,
        algorithms: Sequence[str] = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"),
        issuer: str | None = None,
        audience: str | None = None,
        cache_seconds: float = 300.0,
        timeout_seconds: float = 2.0,
        client: object | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not jwks_url.strip():
            raise ValueError("jwks_url must not be blank")
        normalized_algorithms = tuple(item.strip() for item in algorithms if item.strip())
        if not normalized_algorithms or any(
            item not in _SUPPORTED_ASYMMETRIC_ALGORITHMS for item in normalized_algorithms
        ):
            raise ValueError("JWKS verification requires supported asymmetric algorithms")
        if issuer is None or not issuer.strip() or audience is None or not audience.strip():
            raise ValueError("JWKS verification requires nonblank issuer and audience")
        if cache_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("JWKS cache and timeout must be positive")
        self._jwks_url = jwks_url.strip()
        self._algorithms = normalized_algorithms
        self._issuer = issuer.strip() if issuer and issuer.strip() else None
        self._audience = audience.strip() if audience and audience.strip() else None
        self._cache_seconds = cache_seconds
        self._timeout_seconds = timeout_seconds
        self._clock = clock or time.monotonic
        self._client = cast(_HttpClient, client or httpx.AsyncClient())
        self._owns_client = client is None
        self._keys: dict[str, Mapping[str, object]] = {}
        self._loaded_at: float | None = None
        self._last_forced_refresh_at: float | None = None
        self._lock = asyncio.Lock()

    async def verify(self, token: str) -> dict[str, object]:
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise AuthContextInvalidError(
                details={"reason": "oidc_token_header_invalid"}
            ) from exc
        algorithm = header.get("alg")
        kid = header.get("kid")
        if not isinstance(algorithm, str) or algorithm not in self._algorithms:
            raise AuthContextInvalidError(details={"reason": "oidc_algorithm_not_allowed"})
        if not isinstance(kid, str) or not kid.strip():
            raise AuthContextInvalidError(details={"reason": "oidc_kid_missing"})

        keys = await self._get_keys(force_refresh=False)
        jwk = keys.get(kid)
        if jwk is None:
            loaded_at_before_refresh = self._loaded_at
            keys = await self._get_keys(
                force_refresh=True,
                previous_loaded_at=loaded_at_before_refresh,
            )
            jwk = keys.get(kid)
        if jwk is None:
            raise AuthContextInvalidError(details={"reason": "oidc_signing_key_not_found"})

        try:
            public_key = _jwk_to_public_key(jwk, algorithm)
            options: Options = {"require": ["exp", "sub"]}
            if self._issuer is None:
                options["verify_iss"] = False
            if self._audience is None:
                options["verify_aud"] = False
            claims = self._decode(token, public_key, options)
        except InvalidSignatureError as exc:
            loaded_at_before_refresh = self._loaded_at
            refreshed = await self._get_keys(
                force_refresh=True,
                previous_loaded_at=loaded_at_before_refresh,
            )
            rotated_jwk = refreshed.get(kid)
            if rotated_jwk is not None and rotated_jwk != jwk:
                try:
                    claims = self._decode(
                        token,
                        _jwk_to_public_key(rotated_jwk, algorithm),
                        options,
                    )
                except (InvalidTokenError, ValueError, TypeError):
                    raise AuthContextInvalidError(
                        details={"reason": "oidc_token_invalid"}
                    ) from exc
            else:
                raise AuthContextInvalidError(
                    details={"reason": "oidc_token_invalid"}
                ) from exc
        except (InvalidTokenError, ValueError, TypeError) as exc:
            raise AuthContextInvalidError(
                details={"reason": "oidc_token_invalid"}
            ) from exc
        if not isinstance(claims, dict):
            raise AuthContextInvalidError(details={"reason": "oidc_claims_invalid"})
        return cast(dict[str, object], claims)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _decode(self, token: str, public_key: object, options: Options) -> object:
        return jwt.decode(
            token,
            cast(Any, public_key),
            algorithms=list(self._algorithms),
            issuer=self._issuer,
            audience=self._audience,
            options=options,
        )

    async def _get_keys(
        self,
        *,
        force_refresh: bool,
        previous_loaded_at: float | None = None,
    ) -> dict[str, Mapping[str, object]]:
        now = self._clock()
        async with self._lock:
            if (
                not force_refresh
                and self._loaded_at is not None
                and now - self._loaded_at < self._cache_seconds
            ):
                return dict(self._keys)
            if (
                force_refresh
                and previous_loaded_at is not None
                and self._loaded_at != previous_loaded_at
            ):
                return dict(self._keys)
            if (
                force_refresh
                and previous_loaded_at == self._loaded_at
                and self._last_forced_refresh_at is not None
                and now - self._last_forced_refresh_at < 1.0
            ):
                return dict(self._keys)
            try:
                response = await asyncio.wait_for(
                    self._client.get(self._jwks_url, timeout=self._timeout_seconds),
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, OSError, TimeoutError, ValueError) as exc:
                raise AuthContextInvalidError(
                    details={"reason": "oidc_jwks_unavailable"}
                ) from exc
            keys = _parse_jwks(payload)
            self._keys = keys
            self._loaded_at = now
            if force_refresh:
                self._last_forced_refresh_at = now
            return dict(keys)


class _HttpClient(Protocol):
    async def get(self, url: str, *, timeout: float) -> _HttpResponse: ...

    async def aclose(self) -> None: ...


class _HttpResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


def _parse_jwks(payload: object) -> dict[str, Mapping[str, object]]:
    if not isinstance(payload, Mapping):
        raise AuthContextInvalidError(details={"reason": "oidc_jwks_invalid"})
    raw_keys = payload.get("keys")
    if not isinstance(raw_keys, list):
        raise AuthContextInvalidError(details={"reason": "oidc_jwks_invalid"})
    parsed: dict[str, Mapping[str, object]] = {}
    for item in raw_keys:
        if not isinstance(item, Mapping):
            continue
        kid = item.get("kid")
        if isinstance(kid, str) and kid.strip():
            parsed[kid] = cast(Mapping[str, object], item)
    return parsed


def _jwk_to_public_key(jwk: Mapping[str, object], algorithm: str) -> object:
    declared_algorithm = jwk.get("alg")
    if declared_algorithm is not None and declared_algorithm != algorithm:
        raise ValueError("JWKS key algorithm does not match token")
    if jwk.get("use") not in {None, "sig"}:
        raise ValueError("JWKS key is not a signing key")
    key_ops = jwk.get("key_ops")
    if key_ops is not None and (not isinstance(key_ops, list) or "verify" not in key_ops):
        raise ValueError("JWKS key cannot verify signatures")
    key_type = jwk.get("kty")
    if algorithm.startswith("RS") and key_type != "RSA":
        raise ValueError("JWKS key type does not match algorithm")
    if algorithm.startswith("ES") and key_type != "EC":
        raise ValueError("JWKS key type does not match algorithm")
    if algorithm == "EdDSA" and key_type != "OKP":
        raise ValueError("JWKS key type does not match algorithm")
    algorithms = jwt.algorithms.get_default_algorithms()
    implementation = algorithms.get(algorithm)
    if implementation is None or not isinstance(implementation, Algorithm):
        raise ValueError("unsupported OIDC algorithm")
    return implementation.from_jwk(json.dumps(dict(jwk)))


_SUPPORTED_ASYMMETRIC_ALGORITHMS: frozenset[str] = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"}
)
