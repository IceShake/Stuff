"""Personal, fixed-upstream reverse proxy for the FreeTheAI API.

This service forwards /v1/* requests to https://api.freetheai.org and does not
store or log API keys or request bodies. It is not an arbitrary/open URL proxy.
"""
from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

UPSTREAM_BASE_URL = os.getenv(
    "UPSTREAM_BASE_URL", "https://api.freetheai.org"
).rstrip("/")
_ALLOWED_UPSTREAM_HOST = "api.freetheai.org"
_PROXY_SHARED_KEY = os.getenv("PROXY_SHARED_KEY", "").strip()
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(25 * 1024 * 1024)))

_parsed_upstream = urlsplit(UPSTREAM_BASE_URL)
if (
    _parsed_upstream.scheme != "https"
    or (_parsed_upstream.hostname or "").lower() != _ALLOWED_UPSTREAM_HOST
    or _parsed_upstream.path not in ("", "/")
    or _parsed_upstream.query
    or _parsed_upstream.fragment
):
    raise RuntimeError(
        "UPSTREAM_BASE_URL must be the HTTPS origin https://api.freetheai.org "
        "(without /v1)."
    )

_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
}
_CLIENT_FORWARDING_HEADERS = {
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
}


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": "proxy_error"}},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
    app.state.http = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(
    title="Personal FreeTheAI Relay",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Render health check; does not call the upstream API."""
    return {"status": "ok"}


@app.api_route("/v1", methods=_PROXY_METHODS)
@app.api_route("/v1/{path:path}", methods=_PROXY_METHODS)
async def relay(request: Request, path: str = "") -> Response:
    # Restrict this service to FreeTheAI API paths. The upstream hostname is
    # fixed above, so callers cannot turn this into an SSRF/open proxy.
    if request.url.path != "/v1" and not request.url.path.startswith("/v1/"):
        return _error(404, "Only /v1 API routes are available.")

    if _PROXY_SHARED_KEY:
        provided = request.headers.get("x-proxy-key", "")
        if not hmac.compare_digest(provided, _PROXY_SHARED_KEY):
            return _error(401, "Invalid proxy key.")

    # FreeTheAI requires an API key. Requiring a caller-provided key also
    # avoids offering anonymous upstream access through this relay.
    if not (
        request.headers.get("authorization")
        or request.headers.get("x-api-key")
        or request.headers.get("api-key")
    ):
        return _error(401, "Send your own FreeTheAI API key in the client settings.")

    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > MAX_BODY_BYTES:
                return _error(413, "Request body is too large for this relay.")
        except ValueError:
            return _error(400, "Invalid Content-Length header.")

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _error(413, "Request body is too large for this relay.")

    target = httpx.URL(f"{UPSTREAM_BASE_URL}{request.url.path}").copy_with(
        query=request.url.query.encode("ascii")
    )

    connection_tokens = {
        token.strip().lower()
        for token in request.headers.get("connection", "").split(",")
        if token.strip()
    }
    blocked_request_headers = (
        _HOP_BY_HOP
        | _CLIENT_FORWARDING_HEADERS
        | connection_tokens
        | {"host", "content-length", "x-proxy-key"}
    )
    forwarded_headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in blocked_request_headers
    }

    upstream_request = request.app.state.http.build_request(
        method=request.method,
        url=target,
        headers=forwarded_headers,
        content=body,
    )

    try:
        upstream = await request.app.state.http.send(upstream_request, stream=True)
    except httpx.TimeoutException:
        return _error(504, "The upstream API timed out.")
    except httpx.RequestError:
        return _error(502, "Could not reach the upstream API.")

    response_connection_tokens = {
        token.strip().lower()
        for token in upstream.headers.get("connection", "").split(",")
        if token.strip()
    }
    blocked_response_headers = _HOP_BY_HOP | response_connection_tokens
    response_headers: dict[str, str] = {}
    for name, value in upstream.headers.multi_items():
        lower_name = name.lower()
        if lower_name in blocked_response_headers:
            continue
        # If the upstream ever redirects to itself, keep the redirect on this
        # proxy hostname rather than exposing the upstream hostname.
        if lower_name == "location" and value.startswith(UPSTREAM_BASE_URL):
            value = value[len(UPSTREAM_BASE_URL) :] or "/"
        response_headers[name] = value

    if upstream.status_code in (204, 304):
        await upstream.aclose()
        response_headers.pop("content-length", None)
        return Response(status_code=upstream.status_code, headers=response_headers)

    async def response_body():
        try:
            # Raw streaming preserves Server-Sent Events and compressed bodies.
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        response_body(),
        status_code=upstream.status_code,
        headers=response_headers,
    )
