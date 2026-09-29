from __future__ import annotations

from starlette.requests import Request

from libmyown.site_config import SiteConfig, normalize_public_url

__all__ = ["normalize_public_url", "request_is_secure", "request_origin"]


def request_is_secure(request: Request, *, env_https_enabled: bool | None = None) -> bool:
    # uvicorn applies X-Forwarded-Proto only for TRUSTED_PROXIES, so the scheme is trustworthy.
    if env_https_enabled is not None:
        return env_https_enabled
    return request.url.scheme == "https"


def request_origin(request: Request, site: SiteConfig) -> str:
    configured = site.public_url.strip().rstrip("/")
    if configured:
        return configured
    host = request.headers.get("host", request.url.netloc)
    if not host:
        return "http://localhost:8000"
    return f"{request.url.scheme}://{host}"
