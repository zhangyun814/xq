#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选股执行脚本（2026-10-27 收盘数据）
A 组：近 20 日涨幅 >30% 的全部名单（31 只）→ 按趋势健康度分层排序
B 组：全库扫描「起涨前形态」＝低位 + 缩量蓄势 + 当日放量突破
数据：新浪日 K（含成交量，收盘后截至当日）
"""
import json, os, statistics as st
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "out")   # 生成物统一放 out/

CACHE = "/tmp/kvol_sina"
TMP = "/tmp/kvol_sina"
CODES_CSV = os.path.expanduser("~/Documents/wealth/gp/近20日涨幅超30_按板块_20261027.csv")

def sym_of(code):
    """'603278.SH' -> 'sh603278'"""
    c = code.split(".")[0]
    p = code.split(".")[1]
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}[p] + c

def load(sym):
    p = os.path.join(TMP, sym + ".json")
    if not os.path.exists(p):
        return None
    arr = json.load(open(p))
    return [dict(d=x["day"], o=float(x["open"]), h=float(x["high"]),
                 l=float(x["low"]), c=float(x["close"]), v=float(x["volume"]))
            for x in arr]

def ma(vals, n):
    out = []
    for i in range(len(vals)):
        seg = vals[max(0, i - n + 1):i + 1]
        out.append(sum(seg) / len(seg))
    return out

def analyse(bars):
    cl = [b["c"] for b in bars]
    vl = [b["v"] for b in bars]
    hi = [b["h"] for b in bars]
    n = len(cl)
    i = n - 1
    m5, m10, m20, m60 = ma(cl, 5), ma(cl, 10), ma(cl, 20), ma(cl, 60)
    r20 = (cl[-1] / cl[-21] - 1) * 100
    peak20 = max(hi[-20:])
    drawdown = (cl[i] / peak20 - 1) * 100
    lo250, hi250 = min(cl[-250:]), max(cl[-250:])
    pos = (cl[i] - lo250) / (hi250 - lo250) * 100 if hi250 > lo250 else 50.0
    v5 = sum(vl[-5:]) / 5
    v20 = sum(vl[-20:]) / 20
    volmult = v5 / v20 if v20 else 0
    big = sum(1 for k in range(max(1, n - 20), n) if (cl[k] / cl[k - 1] - 1) * 100 >= 9.3)
    burst = vl[i] / v20 if v20 else 0
    # 近一年量加权成本重心（≈全市场持有者平均成本）
    w = vl[-250:]
    c = cl[-250:]
    wcost = sum(a * b for a, b in zip(c, w)) / sum(w) if sum(w) > 0 else 0
    # 分位判读：大不是好也不是坏，看你在做哪件事
    if pos >= 90:
        pos_read = "上方无套牢盘，卖压只剩获利兑现；追高无参照位"
    elif pos >= 50:
        pos_read = "上方有前高套牢区，压力位看一年内峰值"
    else:
        pos_read = "头顶全是套牢盘，反弹易被砸；但启动时上方是空的"
    if cl[i] >= m5[i] and drawdown > -10:
        state = "健康"
    elif cl[i] >= m20[i]:
        state = "警戒"
    else:
        state = "破位"
    return dict(r20=r20, m5=m5[i], m10=m10[i], m20=m20[i],
                d5=(cl[i] / m5[i] - 1) * 100, d20=(cl[i] / m20[i] - 1) * 100,
                dd=drawdown, pos=pos, volmult=volmult, big=big,
                burst=burst, last=cl[i], chg=(cl[i] / cl[i - 1] - 1) * 100,
                state=state, newhigh=cl[i] >= max(cl[-250:]),
                wcost=wcost, pos_read=pos_read,
                dw=(cl[i] / wcost - 1) * 100 if wcost else 0)

def group_a():
    import csv
    rows = list(csv.DictReader(open(CODES_CSV, encoding="utf-8-sig")))
    out = []
    for r in rows:
        s = sym_of(r["代码"])
        bars = load(s)
        if not bars or len(bars) < 60:
            continue
        a = analyse(bars)
        a.update(code=r["代码"].split(".")[0], name=r["名称"],
                 ind=r["一级行业"], theme=r.get("题材推断", ""))
        out.append(a)
    return out

def group_b(minchg=3.0, pos_max=30.0, sq=0.9, bu=2.0):
    res = []
    for f in os.listdir(TMP):
        if not f.endswith(".json"):
            continue
        bars = load(f[:-5])
        if not bars or len(bars) < 260:
            continue
        cl = [b["c"] for b in bars]; vl = [b["v"] for b in bars]
        i = len(cl) - 1
        chg = (cl[i] / cl[i - 1] - 1) * 100
        if chg < minchg or chg > 11:
            continue
        v20 = st.mean(vl[-20:])
        b100 = st.mean([x for x in vl[-120:-20] if x > 0])
        if v20 <= 0 or b100 <= 0 or v20 / b100 > sq:
            continue
        if vl[i] / v20 < bu:
            continue
        lo250, hi250 = min(cl[-250:]), max(cl[-250:])
        pos = (cl[i] - lo250) / (hi250 - lo250) * 100 if hi250 > lo250 else 50
        if pos > pos_max:
            continue
        m5, m20 = ma(cl, 5), ma(cl, 20)
        res.append(dict(code=f[:-5][2:], sym=f[:-5], name="", last=cl[i], chg=chg,
                        pos=pos, burst=vl[i] / v20, squeeze=v20 / b100,
                        d5=(cl[i] / m5[i] - 1) * 100, d20=(cl[i] / m20[i] - 1) * 100))
    return res

def ensure_fresh():
    """检查本地缓存是否为最新交易日；不是则清空重抓（约 2 分钟）。"""
    import datetime, glob
    today = datetime.date.today().strftime("%Y-%m-%d")
    os.makedirs(TMP, exist_ok=True)
    files = [f for f in os.listdir(TMP) if f.endswith(".json")]
    if not files:
        stale = True
    else:
        # 抽 5 个文件看最后日期
        import random
        sample = random.sample(files, min(5, len(files)))
        lasts = []
        for f in sample:
            try:
                arr = json.load(open(os.path.join(TMP, f)))
                lasts.append(arr[-1]["day"])
            except Exception:
                lasts.append("")
        stale = any(d < today for d in lasts)
    if stale:
        print("缓存不是最新（%s），重新抓取全市场…" % (lasts[0] if files else "空"))
        for f in files:
            os.remove(os.path.join(TMP, f))
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        import fetch_vol
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import json as _json
        syms = list(_json.load(open(fetch_vol.SRC)))
        fail = 0
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(fetch_vol.fetch, s) for s in syms]
            for i, fu in enumerate(as_completed(futs), 1):
                _, ok = fu.result()
                fail += 0 if ok else 1
                if i % 500 == 0:
                    print("  %d/%d" % (i, len(syms)))
        print("抓取完成，失败 %d" % fail)
    else:
        print("缓存已是最新（%s），直接使用" % today)

if __name__ == "__main__":
    # 历史归档：把不是今天的旧选股 CSV 移进「历史选股」文件夹，绝不删除
    import shutil, datetime as _dt
    _today = _dt.date.today().strftime("%Y%m%d")
    _hist = os.path.join(OUT, "历史选股")
    os.makedirs(_hist, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    for _f in os.listdir(OUT):
        if _f.startswith("选股_") and _f.endswith(".csv") and _today not in _f:
            shutil.move(os.path.join(OUT, _f), os.path.join(_hist, _f))

    ensure_fresh()
    A = group_a()
    A.sort(key=lambda x: {"健康": 0, "警戒": 1, "破位": 2}[x["state"]])
    print("=" * 96)
    print("A 组 · 近 20 日涨幅 >30%%（今日 %s 收盘）共 %d 只，按趋势健康度分层" % (
        load("sh600825")[-1]["d"], len(A)))
    print("%-7s %-7s %-5s %7s %7s %7s %7s %7s %6s %5s %6s %s"
          % ("代码", "名称", "状态", "20日", "今日", "距5日", "距20日", "距顶", "量能", "新高", "分位", "行业"))
    for a in A:
        print("%-7s %-7s %-5s %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %5.1fx %4s %+5.0f%% %s"
              % (a["code"], a["name"], a["state"], a["r20"], a["chg"], a["d5"],
                 a["d20"], a["dd"], a["volmult"], "是" if a["newhigh"] else "", a["pos"], a["ind"]))

    # ---- 写 CSV（按当日日期命名，重跑覆盖）----
    import csv as _csv, subprocess
    day = load("sh600825")[-1]["d"].replace("-", "")
    with open(os.path.join(OUT, "选股_强势名单_%s.csv" % day), "w", newline="", encoding="utf-8-sig") as fp:
        w = _csv.writer(fp)
        w.writerow(["代码", "名称", "状态", "近20日涨幅%(百分比)", "今日涨跌%(百分比)", "距5日线%(百分比)",
                    "距20日线%(百分比)", "距20日顶%(百分比)", "近5日量能倍(倍数)", "创250日新高", "250日分位%(百分比)",
                    "分位判读", "一年成本重心(元)", "现价距重心%(百分比)", "今日收盘价(元)", "一级行业"])
        w.writerow(["【算法说明】",
                    "",
                    "健康=收盘>5日线且距20日顶回撤<10%；警戒=仅守住20日线；破位=跌破20日线",
                    "越大=涨得越猛；只看不动手",
                    "越大=越强；单日无好坏",
                    "越大=越强；负数=跌破5日线",
                    "越大=越强；负数=跌破20日线（警戒）",
                    "越大越好（越接近前高越强）；负得越多=回撤越深",
                    "越大=越放量；1.3~2温和放量好，>3警惕冲高出货",
                    "是=强势确认",
                    "分方向：持股确认好，追高坏（见判读列）",
                    "同上，直接告诉你该怎么用",
                    "全场平均成本参考价",
                    "越大=追高风险越大；负=现价低于全场成本（相对便宜）",
                    ""])
        for a in A:
            w.writerow([a["code"], a["name"], a["state"], "%.1f" % a["r20"], "%.1f" % a["chg"],
                        "%.1f" % a["d5"], "%.1f" % a["d20"], "%.1f" % a["dd"], "%.1f" % a["volmult"],
                        "是" if a["newhigh"] else "", "%.0f" % a["pos"], a["pos_read"],
                        "%.2f" % a["wcost"], "%+.1f" % a["dw"], "%.2f" % a["last"], a["ind"]])
    B = group_b()
    B.sort(key=lambda x: -x["burst"])
    names = {}
    if B:
        r = subprocess.run(["curl", "-s", "--noproxy", "*", "--max-time", "15",
                            "https://qt.gtimg.cn/q=" + ",".join(b["sym"] for b in B)],
                           capture_output=True)
        for line in r.stdout.decode("gbk", "ignore").split(";"):
            if "=" not in line:
                continue
            f = line.split("=", 1)[1].strip('"').split("~")
            if len(f) > 45:
                names[f[2]] = (f[1], f[38], f[44])
    with open(os.path.join(OUT, "选股_起涨前形态_%s.csv" % day), "w", newline="", encoding="utf-8-sig") as fp:
        w = _csv.writer(fp)
        w.writerow(["代码", "名称", "今日涨跌%(百分比)", "今日量比20日均(倍数)", "缩量度(比值)", "250日分位%(百分比)",
                    "分位判读", "一年成本重心(元)", "现价距重心%(百分比)", "今日收盘价(元)",
                    "距5日线%(百分比)", "距20日线%(百分比)"])
        w.writerow(["【算法说明】",
                    "",
                    "越大=越强；单日无好坏",
                    "越大=越放量；2~5最佳，>8警惕一字板后炸板",
                    "越小越好（越缩越干净，蓄势越充分）；>0.9被过滤",
                    "分方向：小=适合埋伏启动，大=适合持股、忌追高",
                    "同上，直接告诉你该怎么用",
                    "全场平均成本参考价",
                    "越小=越便宜、越贴近底部；越大=追高离场成本越远",
                    "当天收盘价（元）",
                    "越大=越强；负=还在5日线下",
                    "越大=越强；负=还在20日线下"])
        for b in B:
            nm, to, mv = names.get(b["code"], ("", "", ""))
            bars = load(b["sym"])
            a = analyse(bars) if bars else {}
            w.writerow([b["code"], nm, "%.1f" % b["chg"], "%.1f" % b["burst"], "%.2f" % b["squeeze"],
                        "%.0f" % b["pos"], a.get("pos_read", ""), "%.2f" % a.get("wcost", 0),
                        "%+.1f" % a.get("dw", 0), "%.2f" % b["last"], "%.1f" % b["d5"], "%.1f" % b["d20"]])
            print("%s %-6s 今日%+5.1f%%  量%.1fx  缩量%.2f  分位%3.0f%%  换手%s%%  流通%s亿"
                  % (b["code"], nm or "?", b["chg"], b["burst"], b["squeeze"], b["pos"], to, mv))
    print("已写出：选股_强势名单_%s.csv / 选股_起涨前形态_%s.csv" % (day, day))
