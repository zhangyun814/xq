#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买入时点回测
------------------------------------------------
问题：信号出现后，什么时候扣扳机？
      A 次日收盘（当前做法）　B 等几天　C 等回调 x% 挂单　D 等回踩均线

一个必须算进去的东西：**等回调会买不到**。
所以每家规则都给两个数：
  条件均值  = 只算成交了的样本          ← 多数人看这个，会自我欺骗
  整体均值  = 未成交按 0 收益计入        ← 这才是"策略层面"的真实收益
两个数一起看，才知道"等到便宜价"是赚了还是白等。

口径：信号日之后的走势，持有统一 20 个交易日（出场规则已在 exit_rules.py 定过），
      收益为相对上证指数的超额。所有规则跑同一批样本。

用法：
    python3 entry_rules.py            # 目标价口径
    python3 entry_rules.py --all      # 全部信号
    python3 entry_rules.py --hold 30
"""
import json, os, sys, statistics, math

BASE = os.path.dirname(os.path.abspath(__file__))
WAIT = 10          # 等回调/等均线的有效期（交易日）
NEED = WAIT + 40   # 需要的后续数据长度


def load():
    ev = json.load(open(os.path.join(BASE, "events.json")))
    kl = json.load(open(os.path.join(BASE, "klines.json")))
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    ol = {}
    p = os.path.join(BASE, "ohlc.json")
    if os.path.exists(p):
        # 开盘/收盘必须取自同一复权源，否则会造出假的隔夜缺口
        ol = {s: ({"o": {x[0]: x[1] for x in rows},
                   "c": {x[0]: x[2] for x in rows}})
              for s, rows in json.load(open(p)).items()}
    return ev, kl, {u["code"]: u["symbol"] for u in uni}, ol


def build(ev, kl, c2s, kind, need, ol=None):
    bench = dict(kl.get("sh000001") or [])
    if not bench:
        raise SystemExit("缺基准 sh000001")
    out = []
    for e in ev:
        if kind and kind not in e.get("kinds", []):
            continue
        sym = c2s.get(e["code"], "")
        k = kl.get(sym)
        if not k or len(k) < 70:
            continue
        dates = [d for d, _ in k]
        closes = [c for _, c in k]
        later = [d for d in dates if d > e["date"]]
        if not later:
            continue
        i0 = dates.index(later[0])
        if i0 + need >= len(k):
            continue
        seg_d = dates[i0:i0 + need + 1]
        if any(d not in bench for d in seg_d):
            continue
        o = ol.get(sym) if ol else None
        hasq = bool(o) and all(d in o["o"] and d in o["c"] for d in seg_d) \
            and dates[i0 - 1] in o["c"] if i0 >= 1 else False
        out.append({
            "code": e["code"], "date": e["date"],
            "px": closes[i0:i0 + need + 1],
            "bm": [bench[d] for d in seg_d],
            "hist": closes[:i0],
            "op": [o["o"][d] for d in seg_d] if hasq else None,
            "pxq": [o["c"][d] for d in seg_d] if hasq else None,
            "pxq_prev": o["c"][dates[i0 - 1]] if hasq else None,
            "bm_prev": bench.get(dates[i0 - 1]) if i0 >= 1 else None,
        })
    return out


def ma_at(hist, px, j, m):
    seq = hist + px[:j + 1]
    return sum(seq[-m:]) / m if len(seq) >= m else None


def fill_index(s, rule):
    """返回成交的数组下标；None = 没买到"""
    px, hist = s["px"], s["hist"]
    kind, arg = rule
    if kind == "at":                      # 第 arg 个交易日收盘买（1=次日）
        return arg
    if kind == "open":                    # 次日开盘买
        return arg if s.get("pxq") else None
    if kind == "qclose":                  # 次日收盘买（同源）
        return arg if s.get("pxq") else None
    ref = px[1]                           # 以"次日收盘"为基准价，衡量便不便宜
    if kind == "pullback":
        for j in range(1, min(WAIT, len(px) - 1) + 1):
            if px[j] <= ref * (1 - arg):
                return j
        return None
    if kind == "ma":
        for j in range(1, min(WAIT, len(px) - 1) + 1):
            mv = ma_at(hist, px, j, arg)
            if mv and px[j] <= mv:
                return j
        return None
    return None


def result(s, ie, hold, mode="close"):
    """从下标 ie 买入，持有 hold 日，相对上证的超额。
    mode: close=sina收盘序列（默认）；open/qclose=同一腾讯前复权源，
          保证"开盘 vs 收盘"的对比不被复权基准污染。"""
    if ie is None or ie + hold >= len(s["px"]):
        return None
    if mode in ("open", "qclose"):
        if not s.get("pxq"):
            return None
        if mode == "open":
            if s.get("bm_prev") is None:
                return None
            buy, b0 = s["op"][ie], s["bm_prev"]
        else:
            buy, b0 = s["pxq"][ie], s["bm"][ie]
        sell = s["pxq"][ie + hold]
    else:
        buy, b0, sell = s["px"][ie], s["bm"][ie], s["px"][ie + hold]
    return (sell / buy - 1) - (s["bm"][ie + hold] / b0 - 1)


RULES = [
    (("open", 1), "次日开盘（同源口径）"),
    (("qclose", 1), "次日收盘（同源口径）"),
    (("at", 1), "次日收盘（原基准）"),
    (("at", 2), "等 2 个交易日"),
    (("at", 3), "等 3 个交易日"),
    (("at", 5), "等 5 个交易日"),
    (("at", 10), "等 10 个交易日"),
    (("pullback", 0.03), "回调 −3% 挂单"),
    (("pullback", 0.05), "回调 −5% 挂单"),
    (("pullback", 0.08), "回调 −8% 挂单"),
    (("ma", 10), "回踩 10 日线"),
    (("ma", 20), "回踩 20 日线"),
]


def tstat(vals):
    n = len(vals)
    m = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    return m, statistics.median(vals), sum(1 for v in vals if v > 0) / n, \
        (m / (sd / math.sqrt(n)) if sd else 0)


def main():
    a = sys.argv[1:]
    hold = int(a[a.index("--hold") + 1]) if "--hold" in a else 20
    kind = None if "--all" in a else "目标价"
    need = WAIT + hold + 5

    ev, kl, c2s, ol = load()
    ss = build(ev, kl, c2s, kind, need, ol)
    if not ss:
        print("样本为空")
        return
    ss.sort(key=lambda x: x["date"])
    label = "全部信号" if kind is None else f"「{kind}」口径"

    cut = ss[int(len(ss) * 0.7)]["date"]
    print("=" * 104)
    print(f"买入时点回测　{label}　N={len(ss)}　持有 {hold} 日后卖出　"
          f"区间 {ss[0]['date']} ~ {ss[-1]['date']}")
    print(f"等回调/等均线的有效期：{WAIT} 个交易日，过期不候")
    print("=" * 104)
    print(f"{'买入时点':22s}{'成交率':>7}{'条件均值':>10}{'条件中位':>10}{'条件胜率':>9}"
          f"{'整体均值':>10}{'平均等待':>9}")
    print("-" * 104)

    base_cond = None
    rows = []
    for rule, name in RULES:
        cond, filled, waits = [], [], []
        for s in ss:
            ie = fill_index(s, rule)
            r = result(s, ie, hold, rule[0] if rule[0] in ("open", "qclose") else "close")
            if r is None:
                continue
            cond.append(r)
            filled.append(s)
            waits.append(ie - 1)
        if not cond:
            continue
        m, med, win, tt = tstat(cond)
        fill_rate = len(cond) / len(ss)
        overall = m * fill_rate          # 未成交按 0 收益（现金）
        if base_cond is None:
            base_cond = m
        rows.append({"name": name, "rate": fill_rate, "mean": m * 100,
                     "med": med * 100, "win": win * 100, "overall": overall * 100,
                     "wait": statistics.mean(waits), "t": tt})
        print(f"{name:22s}{fill_rate*100:6.1f}%{m*100:+9.2f}%{med*100:+9.2f}%"
              f"{win*100:8.1f}%{overall*100:+9.2f}%{statistics.mean(waits):8.1f}日")
    print("-" * 104)
    print("条件均值=只算买到了的；整体均值=买不到按 0 算（策略真实产出）。")

    # 隔夜缺口：必须在同一复权源上算，否则会造出假的 ±30%
    gaps = [(s["op"][1] / s["pxq_prev"] - 1) * 100 for s in ss if s.get("pxq")]
    intra = [(s["pxq"][1] / s["op"][1] - 1) * 100 for s in ss if s.get("pxq")]
    if gaps:
        print(f"\n[隔夜缺口] N={len(gaps)}（腾讯前复权，开/收同源）")
        print(f"    信号日收盘 → 次日开盘：均值 {statistics.mean(gaps):+.2f}%、"
              f"中位 {statistics.median(gaps):+.2f}%、低开占比 "
              f"{sum(1 for g in gaps if g < 0)/len(gaps)*100:.0f}%")
        print(f"    次日开盘 → 次日收盘：均值 {statistics.mean(intra):+.2f}%、"
              f"中位 {statistics.median(intra):+.2f}%")
        print("    → 两条相加 ≈ 收盘到收盘，可与「次日收盘」口径对上")

    # 分时段
    print(f"\n[分时段] 整体均值（含未成交），阈值切点 {cut}")
    print(f"{'买入时点':22s}{'前70%':>12}{'后30%':>12}")
    print("-" * 50)
    for rule, name in RULES:
        cells = []
        for sub in (ss[:int(len(ss) * 0.7)], ss[int(len(ss) * 0.7):]):
            vals = []
            for s in sub:
                ie = fill_index(s, rule)
                r = result(s, ie, hold,
                           rule[0] if rule[0] in ("open", "qclose") else "close")
                vals.append(r if r is not None else 0.0)
            cells.append(statistics.mean(vals) * 100 if vals else 0)
        print(f"{name:22s}{cells[0]:+11.2f}%{cells[1]:+11.2f}%")

    # 只在"全部成交"的规则里比最优——否则不同 N 之间比均值是耍赖
    full = [r for r in rows if r["rate"] > 0.999]
    best = max(full, key=lambda r: r["overall"]) if full else rows[0]
    print(f"\n全部成交的规则里最优：{best['name']}（整体均值 {best['overall']:+.2f}%）")
    waits = [r for r in rows if r["rate"] <= 0.999]
    if waits:
        bw = max(waits, key=lambda r: r["overall"])
        print(f"需要等待的规则里最优：{bw['name']}（整体均值 {bw['overall']:+.2f}%，"
              f"成交率 {bw['rate']*100:.0f}%）")
    json.dump(rows, open(os.path.join(BASE, "entry_rules.json"), "w"),
              ensure_ascii=False, indent=1)
    print("已保存：entry_rules.json")


if __name__ == "__main__":
    main()
