#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宿主机执行器（Host Runner）
------------------------------------------------
为什么需要它：
    星球的登录凭证由 zsxq-cli 存在 macOS 钥匙串里（.enc + keyring 密钥），
    Linux 容器解不开，所以「抓新帖」这一步只能在宿主机上跑。
    这个常驻小服务就是给容器留的一扇门：容器点按钮 → 调这里 → 这里跑
    `python3 active_signals.py` → 抓到的 scan_cache.json 落在脚本目录，
    容器通过 /data 只读挂载立刻能读到。

接口（只绑 0.0.0.0:8012，靠 token + 网段白名单限制）：
    GET  /health  → {"ok":true,"zsxq_cli":true,"corpus_newest":"2026-10-19",...}
    POST /run     → {"token":"...","mode":"target","win5":5,"pool":90}
                    → {"ok":true,"code":0,"seconds":12.3,"stdout":"..."}

只用标准库；由 launchd 常驻（见 install_host_runner.sh），开机自启，不用手动敲命令。
"""
import json, os, re, shlex, socket, subprocess, sys, threading, time, traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE, ".runner_token")
PORT = int(os.environ.get("RUNNER_PORT", "8012"))
SCRIPT = os.path.join(BASE, "active_signals.py")
TIMEOUT = int(os.environ.get("RUNNER_TIMEOUT", "600"))
# 宿主那次只负责"刷新语料"，CSV/对比基线交给容器写，避免两边抢同一个文件
HOST_OUT = os.environ.get("RUNNER_OUT", "/tmp/zsxq_host_out")

_LOCK = threading.Lock()
_STATE = {"running": False, "last": None, "last_code": None}


def token():
    """读写同一个 token 文件：容器通过 /data 只读挂载读到它，才能调进来。"""
    if not os.path.exists(TOKEN_FILE):
        import secrets
        with open(TOKEN_FILE, "w") as f:
            f.write(secrets.token_hex(16))
        os.chmod(TOKEN_FILE, 0o600)
    return open(TOKEN_FILE).read().strip()


def corpus_newest():
    try:
        posts = json.load(open(os.path.join(BASE, "resolved.json")))
        return max(p["time"][:10] for p in posts)
    except Exception:
        return ""


def zsxq_ok():
    from shutil import which
    return bool(which("zsxq-cli"))


def _allowed(ip):
    """只放行本机与 docker 网段，避免同一局域网里别人调用。"""
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    for pre in ("172.", "192.168.", "10.", "0.250."):
        if ip.startswith(pre):
            return True
    return False


def run_screen(mode, win5, pool_days):
    """执行 active_signals.py，把它当成唯一可信的计算入口——
    容器和命令行跑的是同一套代码，不另写一份逻辑。"""
    env = os.environ.copy()
    env["ZSXQ_OUT"] = HOST_OUT
    # launchd 的 PATH 很干净，zsxq-cli 在 /usr/local/bin，必须显式带上
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:" + env.get("PATH", "/usr/bin:/bin")
    env.pop("http_proxy", None)
    env.pop("https_proxy", None)
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)

    cmd = [sys.executable, SCRIPT,
           "--mode", mode, "--win5", str(win5), "--pool", str(pool_days)]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                       timeout=TIMEOUT, env=env)
    out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")
    return {
        "ok": p.returncode == 0,
        "code": p.returncode,
        "seconds": round(time.time() - t0, 1),
        "stdout": out[-8000:],
        "cmd": " ".join(shlex.quote(c) for c in cmd[1:]),
    }


class H(BaseHTTPRequestHandler):
    server_version = "FanbeiHostRunner/1.0"

    def log_message(self, fmt, *a):
        print(f"{datetime.now():%H:%M:%S}  " + (fmt % a), flush=True)

    def _send(self, code, body):
        b = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not _allowed(self.client_address[0]):
            return self._send(403, {"error": "ip not allowed"})
        p = self.path.split("?")[0]
        if p == "/health":
            return self._send(200, {
                "ok": True,
                "zsxq_cli": zsxq_ok(),
                "corpus_newest": corpus_newest(),
                "running": _STATE["running"],
                "last": _STATE["last"],
                "last_code": _STATE["last_code"],
                "python": sys.executable,
                "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not _allowed(self.client_address[0]):
            return self._send(403, {"error": "ip not allowed"})
        if self.path.split("?")[0] != "/run":
            return self._send(404, {"error": "not found"})

        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}

        if (body.get("token") or "").strip() != token():
            return self._send(401, {"error": "bad token"})

        mode = body.get("mode") or "target"
        if mode not in ("target", "all"):
            mode = "target"
        try:
            win5 = max(1, min(30, int(body.get("win5") or 5)))
            pool_days = max(10, min(365, int(body.get("pool") or 90)))
        except Exception:
            win5, pool_days = 5, 90

        if not _LOCK.acquire(blocking=False):
            return self._send(409, {"error": "已经有一次刷新在跑", "running": True})
        try:
            _STATE["running"] = True
            r = run_screen(mode, win5, pool_days)
            _STATE["last"] = datetime.now().strftime("%H:%M:%S")
            _STATE["last_code"] = r["code"]
            r["corpus_newest"] = corpus_newest()
            return self._send(200 if r["ok"] else 500, r)
        except subprocess.TimeoutExpired:
            return self._send(504, {"error": f"超时（>{TIMEOUT}s）"})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        finally:
            _STATE["running"] = False
            _LOCK.release()


def main():
    t = token()
    print(f"Host Runner 启动：0.0.0.0:{PORT}", flush=True)
    print(f"  脚本   {SCRIPT}", flush=True)
    print(f"  Python {sys.executable}", flush=True)
    print(f"  星球   {'OK' if zsxq_ok() else '!! 找不到 zsxq-cli'}", flush=True)
    print(f"  token  {t[:6]}…（{TOKEN_FILE}）", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
