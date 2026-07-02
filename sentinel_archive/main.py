from __future__ import annotations

import uvicorn

from .api import create_app

app = create_app()


def main() -> None:
    uvicorn.run("sentinel_archive.main:app", host="127.0.0.1", port=9200, reload=False)


if __name__ == "__main__":
    main()
