#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取日线（双源：腾讯 → 新浪 兜底）。失败不覆盖既有缓存。"""
import json, os, subprocess, re, sys, time
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "klines.json")
START, END = "2025-07-20", "2026-10-19"

def curl(url, timeout=20):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout), url],
                       capture_output=True, text=True)
    return r.stdout.strip()

def via_tencent(sym):
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={sym},day,{START},{END},400,qfq")
    t = curl(url)
    if not t.startswith("{"):
        return None                       # WAF 拦截或异常
    try:
        node = json.loads(t)["data"][sym]
        arr = node.get("qfqday") or node.get("day")
        return [[x[0], float(x[2])] for x in arr] if arr else None
    except Exception:
        return None

def via_sina(sym):
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/"
           f"CN_MarketDataService.getKLineData?symbol={sym}&scale=240&ma=no&datalen=400")
    t = curl(url)
    m = re.search(r"\((\[.*\])\)", t, re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
        return [[x["day"], float(x["close"])] for x in arr if "day" in x]
    except Exception:
        return None

def fetch(sym):
    for fn in (via_tencent, via_sina):
        for _ in range(2):
            d = fn(sym)
            if d and len(d) > 100:
                return sym, d
            time.sleep(0.3)
    return sym, None

def main():
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    code2sym = {u["code"]: u["symbol"] for u in uni}
    codes = set()
    for o in json.load(open(os.path.join(BASE, "resolved.json"))):
        codes.update(o["codes"])
    targets = [code2sym[c] for c in codes if c in code2sym] + ["sh000001"]
    print("待抓取", len(targets), flush=True)

    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT))
        except Exception:
            old = {}

    res, fail = {}, 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (sym, d) in enumerate(ex.map(fetch, targets)):
            if d:
                res[sym] = d
            else:
                fail += 1
            if i % 300 == 0:
                print(f"{i}/{len(targets)} ok={len(res)} fail={fail}", flush=True)

    rate = len(res) / max(len(targets), 1)
    print(f"成功 {len(res)}/{len(targets)}  ({rate*100:.0f}%)")
    if rate < 0.5 and old:
        print("!! 成功率过低，保留旧缓存，不覆盖")
        return 1
    old.update(res)
    json.dump(old, open(OUT, "w"), ensure_ascii=False)
    print("已写入 klines.json，共", len(old))
    return 0

if __name__ == "__main__":
    sys.exit(main())
