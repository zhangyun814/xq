#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰财经星球 · 信号规律一键流水线

用法：
    python3 run_all.py            # 增量更新（只抓上次之后的新帖）
    python3 run_all.py --full     # 忽略缓存，重抓最近 7 天
    python3 run_all.py --days 30  # 本次额外回溯 30 天

流程：增量抓帖 → 实体识别 → 补抓行情 → 事件研究 → 输出 summary.json
产物：summary.json（结论数字）、events.json（事件明细）、report.html 模板待填
"""
import json, os, subprocess, sys, time, glob

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
GROUP = "28888114545551"
STATE = os.path.join(BASE, "state.json")


def run(script, label):
    print(f"\n▶ {label} ...", flush=True)
    t0 = time.time()
    r = subprocess.run([PY, os.path.join(BASE, script)], cwd=BASE,
                       capture_output=True, text=True)
    print(r.stdout.strip()[-1500:] if r.stdout.strip() else "(无输出)")
    if r.returncode != 0:
        print("!! 失败:", r.stderr.strip()[-800:])
        return False
    print(f"  耗时 {time.time()-t0:.0f}s")
    return True


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"last_topic_time": "2026-01-01T00:00:00.000+0800"}


def harvest(full=False, days=0):
    """从最新往前翻，直到碰到上次的游标"""
    st = load_state()
    stop_at = st["last_topic_time"]
    if full or days:
        from datetime import datetime, timedelta
        dt = datetime.now() - timedelta(days=max(days, 7))
        stop_at = dt.strftime("%Y-%m-%dT%H:%M:%S.000+0800")

    outdir = os.path.join(BASE, "raw")
    os.makedirs(outdir, exist_ok=True)
    end_time, pages, got, newest = None, 0, 0, None

    while pages < 120:
        cmd = ["zsxq-cli", "group", "+topics", "--group-id", GROUP,
               "--limit", "30", "--json"]
        if end_time:
            cmd += ["--end-time", end_time]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip().startswith("{"):
            break
        try:
            d = json.loads(r.stdout)
        except Exception:
            break
        topics = d.get("topics_brief") or []
        if not topics:
            break
        pages += 1
        got += len(topics)
        if newest is None:
            newest = topics[0]["create_time"]
        oldest = topics[-1]["create_time"]
        # 落盘
        fname = os.path.join(outdir, f"inc_{oldest[:19].replace(':','').replace('-','').replace('T','')}.json")
        json.dump(d, open(fname, "w"), ensure_ascii=False)
        if oldest <= stop_at or not d.get("has_more"):
            break
        end_time = d.get("next_end_time")
        time.sleep(0.3)

    if newest:
        json.dump({"last_topic_time": newest}, open(STATE, "w"))
    print(f"  抓取 {got} 条 / {pages} 页，最新 {newest}")
    return got > 0


def summarize():
    ev = json.load(open(os.path.join(BASE, "events.json")))
    res = json.load(open(os.path.join(BASE, "resolved.json")))
    import statistics, collections, math

    mention = collections.Counter()
    for p in res:
        for c in p["codes"]:
            mention[c] += 1

    def st(v):
        n = len(v)
        if n < 3:
            return None
        mu = statistics.mean(v); s = statistics.pstdev(v)
        return {"n": n, "mean": round(mu * 100, 2),
                "median": round(statistics.median(v) * 100, 2),
                "win": round(sum(1 for x in v if x > 0) / n * 100, 1),
                "t": round(mu / (s / math.sqrt(n)), 2) if s else 0}

    sig = {}
    for e in ev:
        sig.setdefault(e["signal"], []).append(e)
    table = {}
    for k, arr in sig.items():
        r5 = st([x["ex5"] for x in arr]); r20 = st([x["ex20"] for x in arr])
        if r5 and r20:
            table[k] = {"t5": r5, "t20": r20}

    heat = {}
    for lo, hi, lb in [(1, 1, "1次"), (2, 2, "2次"), (3, 5, "3-5次"),
                       (6, 10, "6-10次"), (11, 20, "11-20次"), (21, 999, ">20次")]:
        v = [e["ex20"] for e in ev if lo <= mention[e["code"]] <= hi]
        s = st(v)
        if s:
            heat[lb] = s

    allv = st([e["ex20"] for e in ev])
    summary = {
        "生成时间": time.strftime("%Y-%m-%d %H:%M"),
        "语料帖数": len(res),
        "提及次数": sum(len(p["codes"]) for p in res),
        "覆盖股票": len(mention),
        "事件数": len(ev),
        "全样本T20": allv,
        "分信号": table,
        "热度分层": heat,
    }
    json.dump(summary, open(os.path.join(BASE, "summary.json"), "w"),
              ensure_ascii=False, indent=2)

    print("\n" + "=" * 66)
    print(f"事件 {len(ev)} 个 · 语料 {len(res)} 帖 · 覆盖 {len(mention)} 只")
    print("=" * 66)
    print(f"{'信号':12s}{'N':>5s}{'T+5均值':>9s}{'中位':>8s}{'胜率':>7s}{'t':>6s}{'T+20中位':>9s}")
    order = ["翻倍/空间", "强call/推荐", "低估/错杀", "拐点", "涨价/提价",
             "超预期", "反转", "无强信号"]
    for k in order:
        if k not in table:
            continue
        a, b = table[k]["t5"], table[k]["t20"]
        print(f"{k:12s}{a['n']:5d}{a['mean']:8.2f}%{a['median']:7.2f}%"
              f"{a['win']:6.1f}%{a['t']:6.2f}{b['median']:8.2f}%")
    print("-" * 66)
    print("热度分层（T+20 超额）：", "  ".join(
        f"{k} {v['mean']:+.2f}%(t={v['t']})" for k, v in heat.items()))
    return summary


def main():
    args = sys.argv[1:]
    full = "--full" in args
    days = 0
    if "--days" in args:
        days = int(args[args.index("--days") + 1])

    print("=" * 66)
    print("星辰财经星球 · 信号规律流水线")
    print("=" * 66)
    if not harvest(full, days):
        print("没有新帖，直接用缓存重算。")

    ok = run("resolve_v2.py", "实体识别（全名+简称+俗称）")
    if not ok:
        return
    ok = run("fetch_klines.py", "抓取/刷新日线（约 40s）")
    if not ok:
        return
    ok = run("event_study.py", "事件研究（T+1/5/20 超额收益）")
    if not ok:
        return
    summarize()
    print("\n完成。结论数字已写入 summary.json")


if __name__ == "__main__":
    main()
