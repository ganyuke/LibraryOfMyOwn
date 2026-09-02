from __future__ import annotations

from starlette.requests import Request

from libmyown.site_config import SiteConfig


def forwarded_proto(request: Request) -> str:
    header = request.headers.get("x-forwarded-proto", "")
    if header:
        return header.split(",")[0].strip().lower()
    return request.url.scheme


def request_is_secure(request: Request, *, env_https_enabled: bool | None = None) -> bool:
    if env_https_enabled is not None:
        return env_https_enabled
    return forwarded_proto(request) == "https"


def request_origin(request: Request, site: SiteConfig) -> str:
    configured = site.public_url.strip().rstrip("/")
    if configured:
        return configured
    scheme = forwarded_proto(request) or "http"
    host = request.headers.get("host", request.url.netloc)
    if not host:
        return "http://localhost:8000"
    return f"{scheme}://{host}"


def normalize_public_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    return value
