import os
from pathlib import Path

from waitress import serve

from .web import create_app


if __name__ == "__main__":
    root = Path(os.environ.get("PRESSDESK_DATA", "runtime-data")).resolve()
    serve(create_app(root), host=os.environ.get("HOST", "127.0.0.1"),
          port=int(os.environ.get("PORT", "8000")), threads=4)
