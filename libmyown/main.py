from __future__ import annotations

import uvicorn

from libmyown.app import create_app
from libmyown.config import load_settings


def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
