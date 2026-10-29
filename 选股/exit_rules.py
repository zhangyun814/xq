#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出场规则回测
------------------------------------------------
问题：入场规则已经有了（目标价翻倍 → T+20 超额 +10.88%），但"什么时候走"没定。
做法：对同一批入场事件，逐日模拟 8 种出场规则，比较相对上证的超额收益、持有天数、胜率。

关键设计（避免自我欺骗）：
1. 只用 klines.json 的历史日线，不联网、不看未来。
2. 所有规则在**同一个样本**上比——要求入场后有 >= MIN_DAYS 个交易日，
   否则规则 A 因为数据长、规则 B 因为数据短，比出来的是数据长度不是规则优劣。
3. 样本按时间切成前 70% / 后 30%，分别看。只在前段赢、后段崩的规则=过拟合。
4. 先测"解剖结构"（入场后最大浮盈/最大浮亏分布），再让这个分布决定止损位，
   而不是先拍一个 8% 再去找理由。

用法：
    python3 exit_rules.py              # 目标价口径
    python3 exit_rules.py --all        # 全部信号（样本更大，看规则一般行为）
    python3 exit_rules.py --horizon 40
"""
import json, os, sys, statistics, math, collections

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)


def load():
    ev = json.load(open(os.path.join(BASE, "events.json")))
    kl = json.load(open(os.path.join(BASE, "klines.json")))
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    c2s = {u["code"]: u["symbol"] for u in uni}
    return ev, kl, c2s


def prep(ev, kl, c2s, want_kind, min_days):
    """把事件整理成可模拟的序列"""
    bench = dict(kl.get("sh000001") or [])
    if not bench:
        raise SystemExit("缺基准 sh000001")
    out = []
    for e in ev:
        if want_kind and want_kind not in e.get("kinds", []):
            continue
        k = kl.get(c2s.get(e["code"], ""))
        if not k or len(k) < 60:
            continue
        dates = [d for d, _ in k]
        later = [d for d in dates if d > e["date"]]
        if not later:
            continue
        entry = later[0]
        i0 = dates.index(entry)
        closes = [c for _, c in k]
        # 对齐基准：入场后 n 个交易日，两边都必须有数据
        seg, bseg = [], []
        for j in range(i0, min(i0 + min_days + 1, len(k))):
            d = dates[j]
            if d not in bench:
                break
            seg.append(closes[j])
            bseg.append(bench[d])
        if len(seg) < min_days + 1:
            continue
        out.append({
            "code": e["code"], "date": e["date"], "entry": entry,
            "px": seg, "bm": bseg,
            "hist": closes[:i0],        # 入场前的历史，供均线用
        })
    return out


def ma_before(hist_seg, m):
    """给定序列末尾的 m 日均线"""
    return sum(hist_seg[-m:]) / m if len(hist_seg) >= m else None


def simulate(s, rule, horizon):
    """返回 (excess, hold_days)。rule 见 RULES。留头不留尾：
    第 i 日收盘判断，收盘成交，不使用第 i 日之后的信息。"""
    px, bm = s["px"], s["bm"]
    entry, b_entry = px[0], bm[0]
    kind, arg = rule
    peak = entry
    for i in range(1, horizon + 1):
        if i >= len(px):
            break
        p = px[i]
        peak = max(peak, p)
        ex = (p / entry - 1) - (bm[i] / b_entry - 1)
        if kind == "hold":
            if i == arg:
                return ex, i
        elif kind == "stop":
            if p <= entry * (1 - arg):
                return ex, i
        elif kind == "tp":
            if p >= entry * (1 + arg):
                return ex, i
        elif kind == "tp_time":
            tp, hd = arg
            if p >= entry * (1 + tp) or i == hd:
                return ex, i
        elif kind == "trail":
            if p <= peak * (1 - arg):
                return ex, i
        elif kind == "ma":
            seq = s["hist"] + px[:i + 1]
            mv = ma_before(seq, arg)
            if mv and p < mv:
                return ex, i
        elif kind == "stop_trail":
            st, tr = arg
            if p <= entry * (1 - st):
                return ex, i
            if p <= peak * (1 - tr):
                return ex, i
    # 未触发：到期强制平仓
    i = min(horizon, len(px) - 1)
    return (px[i] / entry - 1) - (bm[i] / b_entry - 1), i


RULES = [
    (("hold", 5), "固定持有 5 日"),
    (("hold", 10), "固定持有 10 日"),
    (("hold", 20), "固定持有 20 日"),
    (("hold", 30), "固定持有 30 日"),
    (("hold", 40), "固定持有 40 日"),
    (("tp", 0.10), "止盈 +10%"),
    (("tp", 0.20), "止盈 +20%"),
    (("tp", 0.30), "止盈 +30%"),
    (("tp_time", (0.10, 20)), "止盈10% 或 20日到期"),
    (("tp_time", (0.20, 20)), "止盈20% 或 20日到期"),
    (("stop", 0.20), "硬止损 20%（灾难位）"),
    (("stop", 0.05), "硬止损 5%"),
    (("stop", 0.08), "硬止损 8%"),
    (("stop", 0.12), "硬止损 12%"),
    (("trail", 0.08), "移动止盈 8%"),
    (("trail", 0.12), "移动止盈 12%"),
    (("trail", 0.20), "移动止盈 20%"),
    (("ma", 10), "跌破 10 日线"),
    (("ma", 20), "跌破 20 日线"),
    (("stop_trail", (0.08, 0.12)), "止损 8% + 移动 12%"),
    (("stop_trail", (0.10, 0.15)), "止损 10% + 移动 15%"),
]


def plan_k(k, signal_date, hold=20, tp=0.10):
    """给定一条日线序列 k=[[日期,收盘],...] 算出出场计划。
    可直接喂实时抓取的行情，避免用到冻结的 klines.json。"""
    if not k:
        return None
    dates = [d for d, _ in k]
    later = [d for d in dates if d > signal_date]
    if not later:
        # 帖子是今天（或停牌后）发的，交易时段还没走完，只能等下一个交易日
        return {"买入日": "待次日", "买入价": "", "止盈价": "", "到期日": "",
                "已持有": 0, "状态": "待买入"}
    entry = later[0]
    i0 = dates.index(entry)
    px = k[i0][1]
    tp_px = round(px * (1 + tp), 2)
    hold_d = dates[i0 + hold] if i0 + hold < len(dates) else None
    elapsed = len(dates) - 1 - i0
    seg = [c for _, c in k[i0 + 1:]]
    hit_tp = bool(seg) and max(seg) >= px * (1 + tp)
    exit_d = hold_d
    if exit_d is None:
        # 行情还没走到，按工作日粗估（不含法定节假日，标注约）
        from datetime import datetime, timedelta
        d = datetime.strptime(dates[-1], "%Y-%m-%d")
        left = hold - elapsed
        while left > 0:
            d += timedelta(days=1)
            if d.weekday() < 5:
                left -= 1
        exit_d = "≈" + d.strftime("%Y-%m-%d")
    if hit_tp:
        status = "已触发止盈"
    elif hold_d is None:
        status = f"持有中 剩{hold - elapsed}日"
    else:
        status = "已到期"
    return {"买入日": entry, "买入价": round(px, 2), "止盈价": tp_px,
            "到期日": exit_d, "已持有": elapsed, "状态": status}


def plan(kl, code2sym, code, signal_date, hold=20, tp=0.10):
    """按代码从 klines.json 取序列（离线兜底用）"""
    return plan_k(kl.get(code2sym.get(code, "")), signal_date, hold, tp)


def stats(vals):
    n = len(vals)
    m = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    return {
        "n": n, "mean": m * 100, "med": statistics.median(vals) * 100,
        "win": sum(1 for v in vals if v > 0) / n * 100,
        "t": m / (sd / math.sqrt(n)) if sd > 0 else 0,
    }


def anatomy(s, horizon):
    """入场后 horizon 内的最大浮盈 / 最大浮亏（相对入场价，未扣基准）"""
    px = s["px"][:horizon + 1]
    hi, lo = max(px), min(px)
    return (hi / px[0] - 1) * 100, (lo / px[0] - 1) * 100


def main():
    a = sys.argv[1:]
    horizon = int(a[a.index("--horizon") + 1]) if "--horizon" in a else 40
    want = None if "--all" in a else "目标价"
    min_days = horizon

    ev, kl, c2s = load()
    ss = prep(ev, kl, c2s, want, min_days)
    if not ss:
        print("样本为空")
        return
    ss.sort(key=lambda x: x["date"])

    label = "全部信号" if want is None else f"「{want}」口径"
    print("=" * 96)
    print(f"出场规则回测　{label}　N={len(ss)}　窗口={horizon} 交易日　"
          f"区间 {ss[0]['date']} ~ {ss[-1]['date']}")
    print("=" * 96)

    # ---- 1. 解剖结构：决定止损该设在哪 ----
    hi, lo = [], []
    for s in ss:
        a_, b_ = anatomy(s, horizon)
        hi.append(a_)
        lo.append(b_)
    lo_sorted = sorted(lo)
    q = lambda xs, p: xs[int(len(xs) * p)] if xs else 0
    print(f"\n[1] 入场后的形态（相对入场价，未扣基准）")
    print(f"    最大浮盈：中位 {statistics.median(hi):+.1f}%　均值 {statistics.mean(hi):+.1f}%")
    print(f"    最大浮亏：中位 {statistics.median(lo):+.1f}%　均值 {statistics.mean(lo):+.1f}%")
    print(f"    最大浮亏分位：25% {q(lo_sorted,0.25):+.1f}%　50% {q(lo_sorted,0.5):+.1f}%　"
          f"75% {q(lo_sorted,0.75):+.1f}%　最差 {min(lo):+.1f}%")
    for lv in (5, 8, 12, 15):
        hit = sum(1 for x in lo if x <= -lv) / len(lo) * 100
        print(f"    → 若设 {lv:>2}% 硬止损，会被打掉 {hit:5.1f}% 的仓位")
    print("    （这是选止损位的依据：止损设在多数正常波动之内，等于自己把仓位摇出去）")

    # ---- 2. 规则对比 ----
    splits = {"全样本": ss,
              "前70%": ss[:int(len(ss) * 0.7)],
              "后30%": ss[int(len(ss) * 0.7):]}
    print(f"\n[2] 规则对比（超额收益，相对上证）")
    head = f"{'规则':22s}" + "".join(f"{k:>16}" for k in splits)
    print(head)
    print(f"{'':22s}" + "".join(f"{'均值/中位/胜率':>16}" for _ in splits))
    print("-" * 96)
    results = {}
    for rule, name in RULES:
        cells, rec = [], []
        for key, sub in splits.items():
            vals, holds = [], []
            for s in sub:
                ex, h = simulate(s, rule, horizon)
                vals.append(ex)
                holds.append(h)
            st = stats(vals)
            rec.append((st, statistics.mean(holds)))
            cells.append(f"{st['mean']:+6.1f}/{st['med']:+5.1f}/{st['win']:4.0f}%")
        results[name] = rec
        hd = statistics.mean([r[1] for r in rec])
        t = rec[0][0]["t"]
        print(f"{name:22s}" + "".join(f"{c:>16}" for c in cells) + f"   t={t:+.2f} 持{hd:.0f}日")
    print("-" * 96)
    print("格式：均值/中位/胜率%（相对上证）。前70%/后30%按发帖时间切，"
          "只在前段赢、后段崩 = 过拟合。")

    # ---- 3. 与"固定20日"的差值 ----
    base = results.get("固定持有 20 日")
    if base:
        print(f"\n[3] 相对「固定持有 20 日」的改进（全样本均值，百分点）")
        ranked = sorted(results.items(), key=lambda kv: -kv[1][0][0]["mean"])
        for name, rec in ranked[:6]:
            d = rec[0][0]["mean"] - base[0][0]["mean"]
            d2 = rec[2][0]["mean"] - base[2][0]["mean"]
            flag = "  ← 后段也改善" if d2 > 0 else ""
            print(f"    {name:22s}{d:+6.2f}　（后段 {d2:+.2f}）{flag}")

    json.dump({k: [{"mean": v[0]["mean"], "med": v[0]["med"], "win": v[0]["win"],
                    "t": v[0]["t"], "n": v[0]["n"], "hold": v[1]} for v in rec]
               for k, rec in results.items()},
              open(os.path.join(BASE, "exit_rules.json"), "w"), ensure_ascii=False, indent=1)
    print(f"\n已保存：exit_rules.json")


if __name__ == "__main__":
    main()
