"""Restricted Responses forwarding process. Never log headers, bodies or errors."""

import hmac
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 8 * 1024 * 1024
MAX_CALLS = 64
MAX_OUTPUT_TOKENS = 8192


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    calls = 0
    lock = threading.Lock()

    def log_message(self, *_args):
        pass

    def reject(self, status):
        self.send_response(status)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.reject(404)

    def do_POST(self):
        ticket = "Bearer " + os.environ["INFERENCE_TICKET"]
        if self.path != "/v1/responses" or not hmac.compare_digest(
            self.headers.get("Authorization", ""), ticket
        ):
            return self.reject(403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.reject(413)
            body = json.loads(self.rfile.read(length))
            if body.get("model") != os.environ["INFERENCE_MODEL"]:
                return self.reject(403)
            if any(t.get("type") not in {"function", "custom"} for t in body.get("tools", [])):
                return self.reject(403)
            requested = body.get("max_output_tokens", MAX_OUTPUT_TOKENS)
            if type(requested) is not int or requested <= 0:
                return self.reject(400)
            body["max_output_tokens"] = min(requested, MAX_OUTPUT_TOKENS)
            with self.lock:
                if self.calls >= MAX_CALLS:
                    return self.reject(429)
                type(self).calls += 1
            request = urllib.request.Request(
                "https://api.tokenfactory.nebius.com/v1/responses",
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + os.environ["AI_AUTOMATION_TF_API_KEY"],
                },
            )
            with urllib.request.build_opener(NoRedirect).open(request, timeout=180) as response:
                self.send_response(200)
                self.send_header(
                    "Content-Type", response.headers.get("Content-Type", "application/json")
                )
                self.end_headers()
                count = 0
                while chunk := response.read1(8192):
                    count += len(chunk)
                    if count > 16 * MAX_BODY:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (ValueError, OSError, urllib.error.URLError):
            self.close_connection = True


def main():
    for name in ("AI_AUTOMATION_TF_API_KEY", "INFERENCE_TICKET", "INFERENCE_MODEL"):
        if not os.environ.get(name) or os.environ[name].lower() in {
            "placeholder",
            "changeme",
            "todo",
        }:
            raise SystemExit("MODEL_NOT_CONFIGURED")
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
