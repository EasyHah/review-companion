#!/usr/bin/env python3
"""Session-bound loopback bridge. No model calls, shell endpoint or credential storage."""
import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from review_companion import Service, SPECS, ROOT
from companion_store import StoreError
from git_snapshot import SnapshotError
from runtime import load_config, apply_tools, data_directory

MAX_BODY = 192_000
BROWSER_TOOLS = {"read_review", "activate_review", "list_review_files", "set_file_review", "open_editor", "open_project"}


def embed_origin(value):
    """Opt in to one exact local Pi Web origin; never accept a wildcard or URL path."""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
                and parsed.port and 0 < parsed.port <= 65535 and not parsed.username and not parsed.password
                and value == 'http://' + parsed.netloc):
            return value
    except (ValueError, TypeError):
        pass
    raise StoreError('invalid_embed_origin', '嵌入地址必须是带端口的精确本机 HTTP origin')


def repository_root(path):
    path = Path(path).expanduser().resolve()
    result = subprocess.run([os.environ.get("REVIEW_COMPANION_GIT", "git"), "-C", str(path), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, timeout=5)
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else path


class Bridge:
    def __init__(self, data_dir, repository, host="pi", frame_origin=None):
        self.repository = repository_root(repository)
        self.service = Service(data_dir, self.repository)
        self.host = host
        self.frame_origin = embed_origin(frame_origin) if host == 'pi_web' else None
        self.agent_token = secrets.token_urlsafe(32)
        self.browser_token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.session = None
        self.label = "尚未连接会话"
        self.draft = None

    def switch(self, label):
        with self.lock:
            self.session = secrets.token_urlsafe(18)
            self.label = str(label)[:100]
            self.draft = None
            return self.session_info()

    def session_info(self):
        return {"session": self.session, "label": self.label, "repository": str(self.repository), "host": self.host, "input_mode": "draft" if self.host == "pi" else "copy"}

    def require_session(self, value):
        if not self.session or value != self.session:
            raise StoreError("session_changed", "会话已变化，请刷新面板后重新选择操作")

    def dispatch(self, name, args, browser=False):
        if browser:
            if name not in BROWSER_TOOLS:
                raise StoreError("forbidden", "面板不支持此操作")
            if name == "set_file_review" and set(args) - {"review_id", "file_id", "expected_version", "reviewed"}:
                raise StoreError("forbidden", "面板只能手工调整回顾标记")
        if name == "start_review":
            if repository_root(args.get("repository_path", "")) != self.repository:
                raise StoreError("wrong_repository", "请在对应项目的会话中建立回顾")
        elif name in SPECS:
            identifier = args.get("review_id")
            if identifier is None and name in {"read_review", "open_review"}:
                try:
                    identifier = self.service._overview()["review_id"]
                except StoreError as exc:
                    if exc.code != "no_review": raise
                    return self.service.rpc("tools/call", {"name": name, "arguments": args})
            try:
                record = self.service.store.raw_review(identifier)
            except StoreError as exc:
                if exc.code != "no_review":
                    raise
            else:
                if Path(record["comparison"]["repository"]).resolve() != self.repository:
                    raise StoreError("wrong_repository", "这份回顾属于另一项目，请使用该项目的数据目录")
        return self.service.rpc("tools/call", {"name": name, "arguments": args})

    def route(self, path, body, agent=False):
        with self.lock:
            if agent and path == "/agent/session":
                return self.switch(body.get("label", "Pi"))
            self.require_session(body.get("session"))
            if path in ("/api/tool", "/agent/tool"):
                if not isinstance(body.get("arguments", {}), dict):
                    raise StoreError("invalid_argument", "arguments 必须是对象")
                return self.dispatch(body.get("name"), body.get("arguments", {}), not agent)
            if self.host != "pi" and path in {"/api/draft", "/api/draft-status", "/agent/poll", "/agent/ack"}:
                raise StoreError("unsupported", "当前宿主只支持复制问题后手动发送")
            if path == "/api/draft":
                text = body.get("text")
                if not isinstance(text, str) or not text.strip() or len(text) > 16000 or "\x00" in text:
                    raise StoreError("invalid_argument", "问题内容无效")
                if self.draft and self.draft["status"] == "pending" and time.monotonic() < self.draft["deadline"]:
                    raise StoreError("draft_pending", "上一份问题仍在处理中，请稍后重试")
                self.draft = {"id": secrets.token_urlsafe(12), "text": text, "status": "pending",
                              "claimed": False, "deadline": time.monotonic() + 8}
                return {"id": self.draft["id"]}
            if path == "/agent/poll":
                if self.draft and self.draft["status"] == "pending" and not self.draft["claimed"] and time.monotonic() < self.draft["deadline"]:
                    self.draft["claimed"] = True
                    return {"draft": {"id": self.draft["id"], "text": self.draft["text"]}}
                return {"draft": None}
            if path == "/agent/ack":
                if not self.draft or body.get("id") != self.draft["id"] or self.draft["status"] != "pending":
                    raise StoreError("draft_expired", "问题请求已过期")
                status = body.get("status")
                if status not in {"filled", "busy", "occupied", "unsupported", "failed"}:
                    raise StoreError("invalid_argument", "不支持的输入状态")
                self.draft["status"] = status
                return {"ok": True}
            if path == "/api/draft-status":
                if not self.draft or body.get("id") != self.draft["id"]:
                    return {"status": "expired"}
                if self.draft["status"] == "pending" and time.monotonic() >= self.draft["deadline"]:
                    self.draft["status"] = "expired"
                return {"status": self.draft["status"]}
            raise StoreError("not_found", "没有找到请求入口")


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass  # No prompts, local capability tokens or request bodies in logs.

    def reply(self, status, payload, mime="application/json; charset=utf-8"):
        raw = json.dumps(payload, ensure_ascii=False).encode() if mime.startswith("application/json") else payload
        self.send_response(status)
        for key, value in {"Content-Type": mime, "Content-Length": str(len(raw)), "Cache-Control": "no-store",
                           "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                           "Content-Security-Policy": "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors " + (self.server.bridge.frame_origin or "'none'") + "; base-uri 'none'; form-action 'none'"}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def allowed_host(self):
        return self.headers.get_all("Host") == [self.server.address]

    def authenticated(self, agent=False):
        token = self.server.bridge.agent_token if agent else self.server.bridge.browser_token
        if len(self.headers.get_all("Authorization", [])) != 1: return False
        return hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode())

    def do_GET(self):
        if not self.allowed_host():
            return self.reply(403, {"error": "host_denied"})
        if self.path in ("/", "/browser-bridge.js"):
            file = "app.html" if self.path == "/" else self.path[1:]
            mime = "text/html; charset=utf-8" if self.path == "/" else "application/javascript; charset=utf-8"
            return self.reply(200, (ROOT / "assets" / file).read_bytes(), mime)
        agent = self.path == "/agent/specs"
        if not self.authenticated(agent):
            return self.reply(401, {"error": "unauthorized"})
        if self.path == "/api/session":
            return self.reply(200, self.server.bridge.session_info())
        if agent:
            return self.reply(200, [{"name": n, "description": s[0], "parameters": s[1]} for n, s in SPECS.items() if n not in ("open_editor", "open_project")])
        self.reply(404, {"error": "not_found"})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if len(self.headers.get_all("Origin", [])) > 1 or self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            return self.reply(400, {"error": "invalid_headers"})
        if not self.allowed_host() or (origin is not None and origin != "http://" + self.server.address):
            return self.reply(403, {"error": "origin_denied"})
        agent = self.path.startswith("/agent/")
        if not self.authenticated(agent):
            return self.reply(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get_content_type() != "application/json":
                return self.reply(400, {"error": "invalid_body"})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                return self.reply(400, {"error": "invalid_body"})
            result = self.server.bridge.route(self.path, body, agent)
            self.reply(200, result)
        except (ValueError, UnicodeError) as exc:
            if isinstance(exc, (StoreError, SnapshotError)):
                self.reply(409, {"error": {"code": exc.code, "message": str(exc)}})
            else:
                self.reply(400, {"error": "invalid_body"})
        except Exception:
            self.reply(500, {"error": {"code": "internal_error", "message": "回顾服务失败，原记录已保留"}})


def create_server(bridge):
    from local_http import LimitedServer
    server = LimitedServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.bridge = bridge
    server.address = "127.0.0.1:" + str(server.server_port)
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--host", choices=["pi","pi_web","claude","codex"], default="pi")
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    try:
        config=load_config();apply_tools(config)
        repository=repository_root(args.repository)
        server = create_server(Bridge(data_directory(repository,args.data_dir,config),repository,args.host,os.environ.get('REVIEW_COMPANION_EMBED_ORIGIN')))
    except (StoreError,SnapshotError) as error:
        print(json.dumps({'error':{'code':error.code,'message':str(error)}},ensure_ascii=False),flush=True)
        return
    print(json.dumps({"baseUrl": "http://" + server.address, "agentToken": server.bridge.agent_token,
                      "browserToken": server.bridge.browser_token, "embedOrigin": server.bridge.frame_origin}), flush=True)
    def parent_closed():
        sys.stdin.buffer.read()
        server.shutdown()
    threading.Thread(target=parent_closed, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
