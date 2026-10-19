#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""翻倍信号筛选 · Web 服务
------------------------------------------------
点一下按钮，跑一次 screen()，把结果和"跟上一次的变化"推回页面。

接口：
    GET  /                  页面
    GET  /api/meta          语料/环境状态（不开跑，先让页面知道数据新不新）
    POST /api/screen        起一次筛选任务，立刻返回 job id
    GET  /api/job/<id>      查进度：status / log / result
    GET  /api/csv?mode=..   下载本次 CSV

只依赖标准库，容器里不用装第三方包。
"""
import json, os, sys, threading, time, traceback, uuid
import urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import active_signals as A

PORT = int(os.environ.get("PORT", "8000"))
WEB = os.path.join(BASE, "web")
JOBS = {}
_LOCK = threading.Lock()
_RUNNING = {"id": None}

# 宿主机执行器：容器自己抓不到帖子（钥匙串解不开），
# 所以"抓新帖"这一步交给宿主机上的 host_runner.py，
# 它跑完把 scan_cache.json 写在脚本目录，容器通过 /data 只读挂载读到。
HOST_RUNNER = os.environ.get("HOST_RUNNER", "http://host.docker.internal:8012")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def host_token():
    for p in ("/data/.runner_token", os.path.join(BASE, ".runner_token")):
        try:
            t = open(p).read().strip()
            if t:
                return t
        except Exception:
            pass
    return ""


def host_health(timeout=4):
    """桥通不通，页面首屏就要知道，别等点了才失败。"""
    if not host_token():
        return {"ok": False, "reason": "宿主机执行器还没生成 token（未安装或未启动）"}
    try:
        with _OPENER.open(HOST_RUNNER + "/health", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def host_run(mode, win5, pool_days, log):
    """让宿主机真的跑一次 python3 active_signals.py（含抓帖）。"""
    tok = host_token()
    if not tok:
        raise RuntimeError("缺 .runner_token")
    body = json.dumps({"token": tok, "mode": mode, "win5": win5,
                       "pool": pool_days}).encode("utf-8")
    req = urllib.request.Request(HOST_RUNNER + "/run", data=body,
                                 headers={"Content-Type": "application/json"})
    log("宿主机上执行 active_signals.py（这一步才抓得到新帖）…")
    with _OPENER.open(req, timeout=660) as r:
        d = json.loads(r.read())
    # 把宿主机脚本的输出转进页面日志：去掉装饰性分隔线，只留有信息的行
    for line in (d.get("stdout") or "").splitlines():
        t = line.strip()
        if not t or set(t) <= set("=-─ "):
            continue
        # 宿主机写入的是它自己的临时目录，报给用户没意义，容器会重写一份
        if t.startswith("已保存"):
            continue
        log("│ " + t)
    if not d.get("ok"):
        raise RuntimeError(f"宿主机脚本返回 {d.get('code')}，见上面的日志")
    return d


def _log_into(job, msg):
    line = f"{datetime.now():%H:%M:%S}  {msg}"
    with _LOCK:
        job["log"].append(line)
        job["log"] = job["log"][-400:]


def do_screen(mode, win5, pool_days, log, refresh=False):
    """完整流程：先让宿主机抓帖（可选），再在容器里重算。"""
    host = None
    if refresh:
        try:
            host = host_run(mode, win5, pool_days, log)
            log(f"宿主机执行完成：{host['seconds']}s，语料最新 {host.get('corpus_newest') or '?'}")
        except Exception as e:
            log(f"!! 宿主机执行器不可用（{e}）→ 本次只重算，不抓新帖")
    else:
        log("用现有语料重算（要抓新帖请在终端跑 python3 active_signals.py）")
    res = A.screen(mode, win5, pool_days, log=log)
    if host:
        # 容器读到的就是宿主刚写下的缓存，标成"宿主抓的"，别显示成旧料
        res["meta"]["corpus_source"] = "host"
        res["meta"]["host_seconds"] = host["seconds"]
    return res


def _run(job_id, mode, win5, pool_days, refresh=False):
    job = JOBS[job_id]
    t0 = time.time()
    try:
        res = do_screen(mode, win5, pool_days,
                        lambda m: _log_into(job, m), refresh=refresh)
        with _LOCK:
            job["result"] = res
            job["status"] = "done"
            job["ms"] = int((time.time() - t0) * 1000)
            _RUNNING["id"] = None
        _log_into(job, f"完成，用时 {job['ms'] / 1000:.1f}s")
    except Exception as e:
        tb = traceback.format_exc(limit=6)
        with _LOCK:
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
            _RUNNING["id"] = None
        _log_into(job, "!! 出错：" + job["error"])
        print(tb, file=sys.stderr)


def env_meta():
    """页面首屏用得上的状态：语料多新、有没有星球连接、行情源。"""
    rp = os.path.join(BASE, "resolved.json")
    newest, age, missing = "", None, True
    if os.path.exists(rp):
        try:
            posts = json.load(open(rp))
            newest = max(p["time"][:10] for p in posts)
            age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(rp))).days
            missing = False
        except Exception:
            pass
    return {
        "corpus_newest": newest,
        "corpus_age_days": age,
        "corpus_missing": missing,
        "out_dir": A.OUT,
        "hold": A.HOLD,
        "tp": A.TP,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class H(BaseHTTPRequestHandler):
    server_version = "FanbeiScreen/1.0"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            f = os.path.join(WEB, "index.html")
            if not os.path.exists(f):
                return self._send(500, "缺少 web/index.html", "text/plain; charset=utf-8")
            return self._send(200, open(f, encoding="utf-8").read(), "text/html; charset=utf-8")

        if p == "/api/meta":
            return self._send(200, env_meta())

        if p == "/healthz":
            return self._send(200, {"ok": True})

        if p.startswith("/api/job/"):
            jid = p.rstrip("/").split("/")[-1]
            job = JOBS.get(jid)
            if not job:
                return self._send(404, {"error": "job 不存在"})
            with _LOCK:
                out = {"id": jid, "status": job["status"], "log": job["log"][-200:]}
                if job.get("result") is not None and job["status"] == "done":
                    out["result"] = job["result"]
                if job.get("error"):
                    out["error"] = job["error"]
                if job.get("ms"):
                    out["ms"] = job["ms"]
            return self._send(200, out)

        if p == "/api/csv":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            mode = (q.get("mode") or ["target"])[0]
            win5 = int((q.get("win5") or ["5"])[0])
            f = A.csv_path(mode, win5)
            if not os.path.exists(f):
                return self._send(404, {"error": "还没生成，先跑一次"})
            data = open(f, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="screen_{mode}_{win5}d.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        if p not in ("/api/screen", "/api/screen-sync"):
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        mode = body.get("mode") or "target"
        if mode not in ("target", "all"):
            mode = "target"
        try:
            win5 = max(1, min(30, int(body.get("win5") or 5)))
            pool_days = max(10, min(365, int(body.get("pool") or 90)))
        except Exception:
            win5, pool_days = 5, 90
        # 默认就走"先抓帖再算"；只有显式传 false 才跳过
        refresh = body.get("refresh")
        refresh = False if refresh is None else bool(refresh)

        # 同步版：一次请求把结果带回来。给 curl / 无头浏览器 / 书签用，
        # 省掉轮询。ThreadingHTTPServer，堵这一个连接不影响别的。
        if p == "/api/screen-sync":
            log, lock = [], threading.Lock()

            def _l(m):
                with lock:
                    log.append(f"{datetime.now():%H:%M:%S}  {m}")
            try:
                res = do_screen(mode, win5, pool_days, _l, refresh=refresh)
                return self._send(200, {"result": res, "log": log})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}", "log": log})

        with _LOCK:
            running = _RUNNING["id"]
            if running and JOBS.get(running, {}).get("status") == "running":
                return self._send(200, {"busy": True, "job": running,
                                        "log": JOBS[running]["log"][-50:]})
            jid = uuid.uuid4().hex[:12]
            JOBS[jid] = {"id": jid, "status": "running", "log": [], "result": None,
                         "params": {"mode": mode, "win5": win5, "pool": pool_days, "refresh": refresh},
                         "started": datetime.now().strftime("%H:%M:%S")}
            _RUNNING["id"] = jid
        # 只留最近 20 个任务，别让内存一路涨
        if len(JOBS) > 20:
            for k in sorted(JOBS, key=lambda x: JOBS[x].get("started", ""))[:-20]:
                JOBS.pop(k, None)
        threading.Thread(target=_run, args=(jid, mode, win5, pool_days, refresh), daemon=True).start()
        return self._send(200, {"job": jid, "mode": mode, "win5": win5, "pool": pool_days})


def main():
    # 预热：第一次点击不用等 24M 的 klines 与名录解析
    try:
        A._resolver()
        A._universe()
        print("已预热名录/别名表", flush=True)
    except Exception as e:
        print("预热失败（不影响服务启动）：", e, flush=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"服务已启动：http://0.0.0.0:{PORT}　输出目录 {A.OUT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
