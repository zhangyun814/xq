#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语料 + 回测底稿刷新（不是日常筛选，日常只需要 active_signals.py）

用法：
    python3 run_all.py            # 抓近 7 天帖子，合并进语料，重跑回测
    python3 run_all.py --days 30  # 回溯 30 天

流程：抓帖（scan.fetch_posts）→ 实体识别（增量合并）→ 补抓行情 → 事件研究 → summary.json
产物：resolved.json（合并，不覆盖）、klines.json、events.json、summary.json

注意：resolve_v2.py 现在是合并模式，历史语料不会被这次抓到的几页冲掉。
"""
import json, os, subprocess, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


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


def harvest(days=7):
    """统一走 scan.fetch_posts，别再自己维护一套 raw/ 分页逻辑。"""
    sys.path.insert(0, BASE)
    try:
        import scan as S
    except Exception as e:
        print(f"!! 无法载入 scan.py：{e}")
        return False
    try:
        posts = S.fetch_posts(days)
    except Exception as e:
        print(f"!! 抓帖失败（{e}）——本次只重算已有语料")
        return False
    print(f"  抓取 {len(posts)} 条（近 {days} 天，来源 {S.LAST_FETCH_SOURCE or '?'}）")
    return len(posts) > 0


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
    order = ["翻倍(目标价)", "翻倍(业绩)", "翻倍(模糊)", "强call/推荐", "低估/错杀",
             "拐点", "涨价/提价", "超预期", "反转", "无强信号"]
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
    days = 7
    if "--days" in args:
        days = int(args[args.index("--days") + 1])
    if "--full" in args and days == 7:
        days = 30

    print("=" * 66)
    print("星辰财经星球 · 语料与回测底稿刷新")
    print("=" * 66)
    if not harvest(days):
        print("没有新帖，直接用已有语料重算。")

    ok = run("resolve_v2.py", "实体识别（增量合并进 resolved.json）")
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
