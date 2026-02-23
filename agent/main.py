import os

from .service import create_agent_app


def main():
    app = create_agent_app()
    port = int(os.getenv("NJORDHR_AGENT_PORT", "5051"))
    host = os.getenv("NJORDHR_AGENT_HOST", "127.0.0.1")
    print(f"NjordHR Local Agent listening on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

