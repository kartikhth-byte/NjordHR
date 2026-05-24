"""Module entrypoint for the NjordHR cloud API."""

from __future__ import annotations

import os

from .app import create_app


def main() -> None:
    app = create_app()
    settings = app.config["NJORDHR_CLOUD_API_SETTINGS"]
    host = os.getenv("NJORDHR_CLOUD_API_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("NJORDHR_CLOUD_API_PORT", os.getenv("PORT", "5050")))

    print(f"NjordHR Cloud API listening on http://{host}:{port}")
    print(f"Cloud API readiness: {settings.ready_reason} ({'ready' if settings.ready else 'not ready'})")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
