"""Windows packaged entrypoint for Sentinel Archive."""
from __future__ import annotations

import os

import uvicorn

from sentinel_archive.main import app


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "9200"))
    uvicorn.run(app, host=host, port=port)
