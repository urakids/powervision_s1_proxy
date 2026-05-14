#!/usr/bin/env python3
#
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""
PowerVision S1 スタンドアロンプロキシ
Docker不要。このスクリプト単体でアクティベーションサーバーとプロキシを兼ねる。

使い方:
  python powervision_proxy.py

Android WiFiプロキシ設定:
  ホスト: <このPCのIPアドレス>
  ポート: 8888
"""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.request

TARGET_HOST = "pvmg10.sae.powervision.me"
API_PORT    = 18081   # 内部APIサーバー（ループバックのみ）
PROXY_PORT  = 8888    # Androidから接続するプロキシポート
BUFFER_SIZE = 65536

ACTIVATION_RESPONSE = json.dumps({
    "code": 0,
    "msg": "Success",
    "data": {
        "expire": "2055-12-31T23:59:59Z",
        "token": "abcdef123456",
        "user": {
            "id": 12345,
            "userId": "user12345",
            "username": "User",
            "nickname": "User",
            "useremail": "user@example.com",
            "userphone": "09000000000",
            "sex": "Male",
            "birthday": "1990-01-01",
            "city": "",
            "country": "",
            "signature": "",
            "headImage": "",
            "storeUid": "",
            "storeToken": "",
            "addtime": "2020-01-01T00:00:00Z",
            "ipCity": "",
        }
    }
}).encode()


# ─── 内部APIサーバー ────────────────────────────────────────────────────────

class ActivationHandler(BaseHTTPRequestHandler):
    def do_GET(self):  self._respond()
    def do_POST(self): self._respond()

    def _respond(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(ACTIVATION_RESPONSE)))
        self.end_headers()
        self.wfile.write(ACTIVATION_RESPONSE)
        print(f"[API] {self.command} {self.path}")

    def log_message(self, *args): pass  # 標準ログを抑制


def run_api_server():
    server = HTTPServer(("127.0.0.1", API_PORT), ActivationHandler)
    server.serve_forever()


# ─── HTTPプロキシ ────────────────────────────────────────────────────────────

def parse_request(data: bytes):
    header_end = data.find(b"\r\n\r\n")
    if header_end == -1:
        return None, {}, b""
    lines = data[:header_end].split(b"\r\n")
    request_line = lines[0].decode(errors="replace")
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.decode(errors="replace").strip().lower()] = v.decode(errors="replace").strip()
    return request_line, headers, data[header_end + 4:]


def forward(conn, url, method, headers, body):
    content_length = int(headers.get("content-length", 0))
    send_body = body[:content_length] if content_length > 0 else None
    req = urllib.request.Request(url, data=send_body, method=method)
    req.add_header("Content-Type", headers.get("content-type", "application/json"))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read()
            response = f"HTTP/1.1 {resp.status} OK\r\nContent-Length: {len(resp_body)}\r\n"
            for k, v in resp.headers.items():
                if k.lower() not in ("content-length", "transfer-encoding"):
                    response += f"{k}: {v}\r\n"
            response += "Connection: close\r\n\r\n"
            conn.sendall(response.encode() + resp_body)
    except Exception as e:
        conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")


def handle_connect(conn, target):
    """CONNECTメソッド（HTTPS）のトンネリング"""
    host, _, port = target.partition(":")
    try:
        remote = socket.create_connection((host, int(port or 443)), timeout=10)
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        def tunnel(src, dst):
            try:
                while data := src.recv(BUFFER_SIZE):
                    dst.sendall(data)
            except: pass
            finally:
                try: dst.close()
                except: pass
        t1 = threading.Thread(target=tunnel, args=(conn, remote), daemon=True)
        t2 = threading.Thread(target=tunnel, args=(remote, conn), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()
    except:
        conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")


def handle_client(conn, addr):
    try:
        data = b""
        conn.settimeout(10)
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(BUFFER_SIZE)
            if not chunk: return
            data += chunk

        request_line, headers, body = parse_request(data)
        if not request_line: return

        method = request_line.split(" ")[0]
        target = request_line.split(" ")[1] if len(request_line.split(" ")) > 1 else ""
        host   = headers.get("host", "")

        if method == "CONNECT":
            print(f"[PROXY] CONNECT {target}")
            handle_connect(conn, target)
        elif TARGET_HOST in host:
            path = target if not target.startswith("http") else ("/" + target.split("/", 3)[-1] if target.count("/") >= 3 else "/")
            url  = f"http://127.0.0.1:{API_PORT}{path}"
            print(f"[PROXY] {method} {host}{path} -> API")
            forward(conn, url, method, headers, body)
        else:
            url = target if target.startswith("http") else f"http://{host}{target}"
            print(f"[PROXY] {method} {url}")
            forward(conn, url, method, headers, body)
    except: pass
    finally:
        try: conn.close()
        except: pass


def run_proxy_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PROXY_PORT))
    server.listen(100)
    while True:
        try:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except: break


# ─── エントリーポイント ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # ローカルIPを取得して表示
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        local_ip = "不明"

    threading.Thread(target=run_api_server, daemon=True).start()

    print("=" * 50)
    print("PowerVision S1 アクティベーションプロキシ")
    print("=" * 50)
    print(f"Android WiFiプロキシ設定:")
    print(f"  ホスト: {local_ip}")
    print(f"  ポート: {PROXY_PORT}")
    print(f"ルーティング: {TARGET_HOST} -> 内蔵APIサーバー")
    print("Ctrl+C で停止")
    print("=" * 50)

    try:
        run_proxy_server()
    except KeyboardInterrupt:
        print("\n停止しました")
