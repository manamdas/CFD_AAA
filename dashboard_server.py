#!/usr/bin/env python3
"""Serve the bug dashboard and its dashboard.json data."""

import argparse
import errno
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen


PROJECT_DIR = Path(__file__).resolve().parent


def make_handler(dashboard_file: Path, html_file: Path):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = urlsplit(self.path).path
            if route in {"/", "/index.html"}:
                self._send_file(html_file, "text/html; charset=utf-8")
            elif route == "/dashboard.json":
                self._send_file(dashboard_file, "application/json; charset=utf-8")
            else:
                self.send_error(404, "Not found")

        def _send_file(self, path: Path, content_type: str) -> None:
            try:
                body = path.read_bytes()
            except OSError as error:
                self.send_error(500, str(error))
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            print("%s - %s" % (self.address_string(), format % args))

    return DashboardHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bug dashboard web server.")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=3030, help="port (default: 3030)")
    parser.add_argument(
        "--data",
        default=str(PROJECT_DIR / "dashboard.json"),
        help="dashboard JSON file to serve",
    )
    parser.add_argument(
        "--html",
        default=str(PROJECT_DIR / "index.html"),
        help="dashboard HTML file to serve",
    )
    args = parser.parse_args()

    dashboard_file = Path(args.data).resolve()
    html_file = Path(args.html).resolve()
    # Fail early with a useful message if the data file is missing or invalid.
    with dashboard_file.open(encoding="utf-8") as data_file:
        json.load(data_file)
    if not html_file.is_file():
        parser.error("dashboard HTML file does not exist: {}".format(html_file))

    try:
        server = ThreadingHTTPServer(
            (args.host, args.port), make_handler(dashboard_file, html_file)
        )
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        try:
            with urlopen(
                "http://127.0.0.1:{}/dashboard.json".format(args.port), timeout=2
            ) as response:
                active_data = json.load(response)
            if isinstance(active_data, dict) and isinstance(active_data.get("bugs"), list):
                print(
                    "Dashboard is already running at http://ads-784:{}".format(args.port)
                )
                return
        except Exception:
            pass
        parser.exit(
            1,
            "Port {} is already in use by another service.\n".format(args.port),
        )
    print("Dashboard available at http://{}:{}".format(args.host, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
