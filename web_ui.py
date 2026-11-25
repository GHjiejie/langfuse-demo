"""Serve the demo interface and its same-origin API on loopback."""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx

from local_setup import LOCAL, ROOT, settings
from web_service import UIError, Workbench, ui_message
from workorder import local_langfuse


PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
SERVICE = "langfuse-workorder-ui"
PID_FILE = LOCAL / "ui.pid"
FILES = {"index.html": "text/html", "styles.css": "text/css", "app.js": "text/javascript",
         "common.js": "text/javascript", "experiments.js": "text/javascript", "manuals.js": "text/javascript",
         "icons.svg": "image/svg+xml", "favicon.svg": "image/svg+xml", "plex-mono.woff2": "font/woff2"}
LOCAL_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}


class LocalServer(ThreadingHTTPServer):
    request_queue_size = 32


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, content, kind="application/json"):
        body = json.dumps(content, ensure_ascii=False).encode() if kind == "application/json" else content
        self.send_response(status)
        self.send_header("Content-Type", kind + ("; charset=utf-8" if kind.startswith("text/") or kind == "application/json" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if kind == "application/json" else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, method):
        host = self.headers.get("Host")
        if host not in LOCAL_HOSTS:
            raise UIError(403, "请通过本机地址访问。")
        path = urlsplit(self.path).path
        app = self.server.workbench
        if method == "GET":
            if path == "/api/service":
                return {"service": SERVICE, "pid": os.getpid()}
            if path == "/api/catalog":
                return app.catalog()
            if path == "/api/history":
                return app.history()
            if path == "/api/experiments":
                return app.experiments()
            if path == "/api/experiments/job":
                return app.job_state()
            match = re.fullmatch(r"/api/traces/([a-f0-9]{32})", path)
            if match:
                return app.trace(match[1])
            filename = "index.html" if path == "/" else path[1:]
            if filename in FILES:
                self.respond(200, (ROOT / "ui" / filename).read_bytes(), FILES[filename])
                return None
        if method == "POST":
            if self.headers.get("Origin") not in (None, f"http://{host}"):
                raise UIError(403, "该操作需要从本机工作台发起。")
            if self.headers.get_content_type() != "application/json":
                raise UIError(415, "请发送 JSON 请求。")
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 32_768:
                    raise UIError(413, "请求长度超出范围。")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise UIError(400, "请求内容无效。") from None
            if path == "/api/ask":
                return app.ask(body)
            if path == "/api/experiments/run":
                return app.start_experiment()
            match = re.fullmatch(r"/api/traces/([a-f0-9]{32})/(feedback|sample)", path)
            if match:
                return app.vote(match[1], body) if match[2] == "feedback" else app.link_sample(match[1])
        raise UIError(404, "页面或记录不存在。")

    def handle_method(self):
        try:
            result = self.dispatch(self.command)
            if result is not None:
                self.respond(200, result)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (Exception, SystemExit) as error:
            status = error.status if isinstance(error, UIError) else 503
            if not isinstance(error, UIError):
                self.log_error("Request failed (%s)", type(error).__name__)
            try:
                self.respond(status, {"error": ui_message(error)})
            except (BrokenPipeError, ConnectionResetError):
                pass

    do_GET = do_POST = handle_method


def serve():
    config = settings()
    with local_langfuse(config) as (client, api), LocalServer(("127.0.0.1", PORT), Handler) as server:
        server.workbench = Workbench(client, api, config["LANGFUSE_INIT_PROJECT_ID"])
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
        print("工单知识库工作台", URL, flush=True)
        server.serve_forever()


def active_pid(http):
    try:
        response = http.get(URL + "/api/service")
        if response.is_success:
            data = response.json()
            if data.get("service") == SERVICE:
                return data["pid"]
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return None


def manage(command):
    with httpx.Client(trust_env=False, timeout=2) as http:
        pid = active_pid(http)
        if command == "stop":
            if pid and PID_FILE.exists() and PID_FILE.read_text().strip() == str(pid):
                os.kill(pid, signal.SIGTERM)
                for _ in range(30):
                    if not active_pid(http):
                        break
                    time.sleep(0.1)
                PID_FILE.unlink(missing_ok=True)
            return
        if command == "status" or pid:
            print("工单知识库工作台", URL if pid else "未启动")
            return
        settings()
        with (LOCAL / "ui.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, __file__, "serve"], cwd=ROOT, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True)
        for _ in range(100):
            pid = active_pid(http)
            if pid:
                PID_FILE.write_text(str(pid))
                print("工单知识库工作台", URL)
                return
            if process.poll() is not None:
                break
            time.sleep(0.12)
        raise SystemExit("工作台未就绪，请检查 .local/ui.log 和本机 8765 端口。")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "serve":
        serve()
    elif command in ("start", "stop", "status"):
        manage(command)
    else:
        raise SystemExit("用法 web_ui.py [start|stop|status|serve]")
