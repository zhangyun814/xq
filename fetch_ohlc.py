#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓目标价口径标的的开盘价（klines.json 只存了收盘，算不了"次日开盘买入"）
输出 ohlc.json：{symbol: [[日期, 开盘, 收盘], ...]}  前复权
"""
import json, os, subprocess, time

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "ohlc.json")


def fetch(sym):
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={sym},day,2025-01-01,2026-10-19,600,qfq")
    r = subprocess.run(["curl", "-s", "--max-time", "20", url],
                       capture_output=True, text=True)
    t = r.stdout.strip()
    if not t.startswith("{"):
        return None
    try:
        node = json.loads(t)["data"][sym]
        arr = node.get("qfqday") or node.get("day")
        return [[x[0], float(x[1]), float(x[2])] for x in arr] if arr else None
    except Exception:
        return None


def main():
    ev = json.load(open(os.path.join(BASE, "events.json")))
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    c2s = {u["code"]: u["symbol"] for u in uni}
    codes = sorted({e["code"] for e in ev if "目标价" in e.get("kinds", [])})
    print(f"目标价口径标的 {len(codes)} 只")

    old = json.load(open(OUT)) if os.path.exists(OUT) else {}
    res, ok = {}, 0
    for i, c in enumerate(codes, 1):
        sym = c2s.get(c)
        if not sym:
            continue
        d = fetch(sym)
        if d and len(d) > 100:
            res[sym] = d
            ok += 1
        elif sym in old:
            res[sym] = old[sym]          # 失败保留旧数据
        if i % 10 == 0:
            print(f"  {i}/{len(codes)} … 成功 {ok}")
        time.sleep(0.15)

    rate = ok / max(1, len(codes))
    print(f"成功 {ok}/{len(codes)}（{rate*100:.0f}%）")
    if rate < 0.5 and old:
        print("!! 成功率过低，保留旧缓存，不覆盖")
        return
    old.update(res)
    json.dump(old, open(OUT, "w"))
    print(f"已保存 {OUT}（{len(old)} 个标的）")


if __name__ == "__main__":
    main()
