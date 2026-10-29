#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从新浪抓成交量，与 klines.json 的前复权收盘价按日期合并 → klines_vol.json
   {sym: [[date, close(qfq), volume(股)], ...]}
"""
import json, os, subprocess, time, re, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "klines.json")
OUT = os.path.join(HERE, "klines_vol.json")
TMP = "/tmp/kvol_sina"
os.makedirs(TMP, exist_ok=True)
LEN = 500

def fetch(sym):
    cache = os.path.join(TMP, sym + ".json")
    if os.path.exists(cache) and os.path.getsize(cache) > 500:
        return sym, True
    u = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%%20_x=/CN_MarketDataService.getKLineData"
         "?symbol=%s&scale=240&ma=no&datalen=%d" % (sym, LEN))
    for attempt in range(3):
        r = subprocess.run(["curl", "-s", "--noproxy", "*", "--max-time", "20",
                            "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 Chrome/120 Safari/537.36",
                            "-H", "Referer: https://finance.sina.com.cn", u], capture_output=True)
        t = r.stdout.decode("utf-8", "ignore")
        m = re.search(r"\[.*\]", t, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                if arr and "volume" in arr[0]:
                    with open(cache, "w") as f:
                        json.dump(arr, f)
                    return sym, True
            except Exception:
                pass
        time.sleep(0.8 * (attempt + 1))
    return sym, False

def main():
    syms = list(json.load(open(SRC)))
    print("待抓取 %d 只" % len(syms), flush=True)
    fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(fetch, s) for s in syms]
        for i, f in enumerate(as_completed(futs), 1):
            _, ok = f.result()
            fail += 0 if ok else 1
            if i % 150 == 0:
                print("  %d/%d  失败 %d  %.0fs" % (i, len(syms), fail, time.time() - t0), flush=True)
    print("抓取完成，失败 %d" % fail, flush=True)

    closes = json.load(open(SRC))
    out, miss = {}, 0
    for sym, bars in closes.items():
        p = os.path.join(TMP, sym + ".json")
        if not os.path.exists(p):
            miss += 1
            continue
        try:
            sarr = json.load(open(p))
        except Exception:
            miss += 1
            continue
        volmap = {x["day"]: float(x["volume"]) for x in sarr}
        merged = [[d, c, volmap.get(d, 0.0)] for d, c in bars]
        hit = sum(1 for x in merged if x[2] > 0)
        if hit < len(merged) * 0.7:
            miss += 1
            continue
        out[sym] = merged
    json.dump(out, open(OUT, "w"), ensure_ascii=False)
    print("合并完成：%d 只写入 %s（缺量剔除 %d 只）" % (len(out), OUT, miss))

if __name__ == "__main__":
    main()
