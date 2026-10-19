#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追溯：扫描结果里的股票，第一次被提到是什么时候，到今天涨了多少
用法：
    python3 trace.py            # 读最新的 signals_*.csv
    python3 trace.py 300750 ... # 指定代码
"""
import json, os, sys, glob, re, csv, subprocess
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))


def load_codes():
    args = [a for a in sys.argv[1:] if re.fullmatch(r"\d{6}", a)]
    if args:
        return args
    files = sorted(glob.glob(os.path.join(BASE, "signals_*.csv")))
    if not files:
        print("没有找到 signals_*.csv，先跑 scan.py")
        return []
    rows = list(csv.DictReader(open(files[-1], encoding="utf-8-sig")))
    print(f"读取：{os.path.basename(files[-1])}（{len(rows)} 只）")
    return [r["代码"] for r in rows]


def mentions(code):
    """该股票在语料里被提及的所有日期（升序）"""
    ds = []
    for p in json.load(open(os.path.join(BASE, "resolved.json"))):
        if code in p["codes"]:
            ds.append(p["time"][:10])
    return sorted(set(ds))


def kline_qfq(sym):
    end = datetime.now().strftime("%Y-%m-%d")   # 别写死日期，否则天天拿旧数据
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={sym},day,2025-01-01,{end},600,qfq")
    r = subprocess.run(["curl", "-s", "--max-time", "20", url],
                       capture_output=True, text=True)
    t = r.stdout.strip()
    if t.startswith("{"):
        try:
            node = json.loads(t)["data"][sym]
            arr = node.get("qfqday") or node.get("day")
            if arr:
                return [[x[0], float(x[2])] for x in arr]
        except Exception:
            pass
    # 新浪兜底（不复权）
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/"
           f"CN_MarketDataService.getKLineData?symbol={sym}&scale=240&ma=no&datalen=500")
    r = subprocess.run(["curl", "-s", "--max-time", "20", url],
                       capture_output=True, text=True)
    m = re.search(r"\((\[.*\])\)", r.stdout, re.S)
    if m:
        try:
            arr = json.loads(m.group(1))
            return [[x["day"], float(x["close"])] for x in arr if "day" in x]
        except Exception:
            pass
    return None


def ret_from(k, day, live=None):
    """首提当日（或之后第一个交易日）收盘 → 最新价（盘中用现价）"""
    hit = [d for d, _ in k if d >= day]
    if not hit:
        if live:                        # 首提日还没收盘（如周末发帖），以现价起算
            return live, live, day, 0.0
        return None
    entry = k[[d for d, _ in k].index(hit[0])][1]
    last = live if live else k[-1][1]
    return entry, last, hit[0], last / entry - 1


def main():
    codes = load_codes()
    if not codes:
        return
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    c2n = {u["code"]: u["name"] for u in uni}
    c2s = {u["code"]: u["symbol"] for u in uni}

    try:
        from scan import get_quotes
        live = {sym: v[1] for sym, v in get_quotes(codes).items()}
    except Exception:
        live = {}

    out = []
    for c in codes:
        ds = mentions(c)
        if not ds:
            continue
        first = ds[0]
        recent = [d for d in ds if d >= "2026-10-14"]
        k = kline_qfq(c2s[c])
        if not k:
            out.append({"代码": c, "名称": c2n.get(c, c), "首提": first,
                        "提及次数": len(ds), "行情": "获取失败"})
            continue
        px = live.get(c2s[c])
        r_all = ret_from(k, first, px)
        r_rec = ret_from(k, recent[0], px) if recent else None
        out.append({
            "代码": c, "名称": c2n.get(c, c),
            "首次提及": first, "提及次数": len(ds),
            "首提买入日": r_all[2] if r_all else "",
            "首提当日收盘": round(r_all[0], 2) if r_all else "",
            "最新价": round(r_all[1], 2) if r_all else "",
            "首提至今涨幅%": round(r_all[3] * 100, 2) if r_all else "",
            "近5日首提": recent[0] if recent else "",
            "近5日买入日": r_rec[2] if r_rec else "",
            "近5日至今涨幅%": round(r_rec[3] * 100, 2) if r_rec else "",
            "持有天数": (datetime.strptime(k[-1][0], "%Y-%m-%d") -
                     datetime.strptime(r_all[2], "%Y-%m-%d")).days if r_all else "",
            "盘中价": "是" if px else "否",
        })

    print("\n" + "=" * 104)
    print(f"{'代码':<8}{'名称':<9}{'首提日期':<12}{'提及':>4}{'首提收盘':>9}{'最新价':>9}{'首提至今':>10}{'近5日至今':>11}{'天数':>6}")
    print("-" * 104)
    for r in out:
        if "行情" in r:
            print(f"{r['代码']:<8}{r['名称']:<9}{r['首提']:<12}{r['提及次数']:>4}  {r['行情']}")
            continue
        rec = f"{r['近5日至今涨幅%']:>9}%" if r["近5日至今涨幅%"] != "" else "        —"
        print(f"{r['代码']:<8}{r['名称']:<9}{r['首次提及']:<12}{r['提及次数']:>4}"
              f"{r['首提当日收盘']:>9}{r['最新价']:>9}{r['首提至今涨幅%']:>9}%{rec}{r['持有天数']:>6}")
    print("-" * 104)

    vals = [r["首提至今涨幅%"] for r in out if isinstance(r.get("首提至今涨幅%"), (int, float))]
    if vals:
        print(f"首提至今：平均 {sum(vals)/len(vals):+.1f}%　中位 {sorted(vals)[len(vals)//2]:+.1f}%"
              f"　上涨 {sum(1 for v in vals if v>0)}/{len(vals)} 只")
    json.dump(out, open(os.path.join(BASE, "trace.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
