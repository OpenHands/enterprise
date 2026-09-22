from __future__ import annotations

import os

import uvicorn

from .logging_config import get_uvicorn_logging_config


def main() -> None:
    uvicorn.run(
        "integrations_hub.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_config=get_uvicorn_logging_config(),
    )


if __name__ == "__main__":
    main()
