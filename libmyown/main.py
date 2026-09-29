from __future__ import annotations

import logging
import sys

import uvicorn

from libmyown.app import create_app
from libmyown.config import ConfigurationError, load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"libmyown: {exc}", file=sys.stderr)
        sys.exit(2)
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
        server_header=False,
    )


if __name__ == "__main__":
    main()
