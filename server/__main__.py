"""Lokalne uruchomienie: `python -m server` (domyślnie http://localhost:8000, widoczne też w sieci domowej)."""
import os
import socket

from server.app import app


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"\n  XTB Analytics działa:\n    na tym komputerze:  http://localhost:{port}\n"
          f"    z telefonu w tej samej sieci Wi-Fi:  http://{_lan_ip()}:{port}\n")
    app.run(host=host, port=port, threaded=True, debug=False)
