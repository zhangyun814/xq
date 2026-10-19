#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
翻倍信号扫描器
------------------------------------------------
扫描星辰财经星球最近 N 天（默认 5 天）的帖子，找出出现「翻倍」字样的标的，
并附上热度（近 90 天被提及次数）与实时行情。

用法：
    python3 scan.py               # 近 5 天
    python3 scan.py --days 10     # 近 10 天
    python3 scan.py --all         # 不过滤，把所有信号词都列出来
    python3 scan.py --keyword 拐点 # 换一个信号词扫

输出：
    终端表格 + signals_YYYY-MM-DD.csv（可用 Excel 打开）

回测依据（2025-08~2026-10，1776 个事件）：
    「翻倍」信号 T+5 超额 +2.98%、中位 +0.66%、胜率 55.3%（t=2.51）
    但仅在"被反复提及（≥10 次）"的标的上成立；只被提 1 次的是 −5.47%
    持有期约 5 个交易日，第 20 日中位转负
"""
import json, os, re, sys, csv, time, subprocess, collections, shutil
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
GROUP = "28888114545551"
TAG_RE = re.compile(r"<e[^>]*/>|<[^>]+>")
BRACKET = re.compile(r"【[^】]{0,20}】")

# 所有可扫的信号词（--all 时全用）
SIGNALS = {
    "翻倍": ["翻倍"],
    "超预期": ["超预期"],
    "拐点": ["拐点"],
    "反转": ["反转"],
    "涨价": ["涨价", "提价"],
    "低估": ["低估", "错杀"],
}


def clean(s):
    s = TAG_RE.sub(" ", s or "").replace("\u200b", "")
    s = BRACKET.sub(" ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


# ---------- 星球抓取 ----------
CACHE = os.path.join(BASE, "scan_cache.json")
OUT = os.environ.get("ZSXQ_OUT", BASE)
# 上一次 fetch_posts 的数据来源："network"=真去星球拉了；"cache"=读了缓存的帖子
LAST_FETCH_SOURCE = None


def fetch_posts(days=5):
    global LAST_FETCH_SOURCE
    # 30 分钟内重复运行直接读缓存
    if os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE))
            if c.get("days") == days and time.time() - c.get("ts", 0) < 1800:
                print(f"抓取近 {days} 天帖子 {len(c['posts'])} 条（缓存）")
                LAST_FETCH_SOURCE = "cache"
                return c["posts"]
        except Exception:
            pass

    # 抓取依赖 zsxq-cli；容器里通常没有，且它的登录取自 macOS 钥匙串，
    # 无法直接跨进 Linux。这里明确报错，让上层如实标注"未刷新"。
    cli = shutil.which("zsxq-cli")
    if not cli:
        raise RuntimeError("找不到 zsxq-cli（容器内无星球连接），跳过抓取")

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000+0800")
    out, end_time, pages = [], None, 0
    while pages < 80:
        cmd = ["zsxq-cli", "group", "+topics", "--group-id", GROUP,
               "--limit", "30", "--json"]
        if end_time:
            cmd += ["--end-time", end_time]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip().startswith("{"):
            if pages == 0:
                raise RuntimeError("星球抓取失败：" + (r.stderr.strip()[:200] or "返回非 JSON"))
            print("!! 抓取中断，返回已取得的", len(out), "条：", r.stderr.strip()[:200])
            break
        try:
            d = json.loads(r.stdout)
        except Exception:
            break
        topics = d.get("topics_brief") or []
        if not topics:
            break
        pages += 1
        hit_old = False
        for t in topics:
            if t["create_time"] < cutoff:
                hit_old = True
                continue
            out.append(t)
        if hit_old or not d.get("has_more"):
            break
        end_time = d.get("next_end_time")
    print(f"抓取近 {days} 天帖子 {len(out)} 条（{pages} 页）")
    LAST_FETCH_SOURCE = "network"
    try:
        os.makedirs(OUT, exist_ok=True)
        json.dump({"ts": time.time(), "days": days, "posts": out},
                  open(os.path.join(OUT, "scan_cache.json"), "w"), ensure_ascii=False)
    except Exception:
        pass
    return out


# ---------- 实体识别 ----------
def load_resolver():
    sys.path.insert(0, BASE)
    import resolve_v2 as rv
    d, idx = rv.build()

    def find(text):
        return rv.find_codes(text, d, idx)

    return find, d


def heat_90d():
    """近 90 天各标的被提及次数（热度）"""
    from datetime import datetime as dt, timedelta as td
    cutoff = (dt.now() - td(days=90)).strftime("%Y-%m-%d")
    path = os.path.join(BASE, "resolved.json")
    cnt = collections.Counter()
    if os.path.exists(path):
        for p in json.load(open(path)):
            if p["time"][:10] >= cutoff:
                for c in p["codes"]:
                    cnt[c] += 1
    return cnt


# ---------- 实时行情 ----------
def get_quotes(codes):
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    c2s = {u["code"]: u["symbol"] for u in uni}
    syms = [c2s[c] for c in codes if c in c2s]
    if not syms:
        return {}
    res = {}
    for i in range(0, len(syms), 60):
        batch = syms[i:i + 60]
        r = subprocess.run(["curl", "-s", "--max-time", "12",
                            "-H", "Referer: https://finance.sina.com.cn",
                            "https://hq.sinajs.cn/list=" + ",".join(batch)],
                           capture_output=True)
        try:
            txt = r.stdout.decode("gbk", errors="ignore")
        except Exception:
            continue
        for line in txt.split(";"):
            m = re.search(r'hq_str_(\w{2}\d{6})="([^"]*)"', line)
            if not m:
                continue
            sym, body = m.group(1), m.group(2)
            f = body.split(",")
            if len(f) < 6 or not f[3]:
                continue
            try:
                price, prev = float(f[3]), float(f[2])
                chg = (price - prev) / prev * 100 if prev else 0
                res[sym] = (f[0], price, chg)
            except Exception:
                continue
    return res


def attribute(txt, codes, code2name, kws, max_dist=60):
    """按"信号词 → 最近股票名"的距离归属，避免张冠李戴。
    距离越远越可能是同一篇研报里的其他票，用阈值卡掉。"""
    occ = []
    for k in kws:
        occ += [m.start() for m in re.finditer(re.escape(k), txt)]
    if not occ:
        return None, 10 ** 9, ""
    best_code, best_d, best_ctx = None, 10 ** 9, ""
    for c in codes:
        nm = code2name.get(c)
        if not nm or len(nm) < 2:
            continue
        for m in re.finditer(re.escape(nm), txt):
            # 名字后面紧接"研究/认为/指出"的，多半是研报署名而非标的
            tail = txt[m.end():m.end() + 4]
            if tail.startswith(("研究", "认为", "指出", "表示", "团队", "策略", "分析")):
                continue
            for o in occ:
                d = abs(m.start() - o)
                if d < best_d:
                    best_d, best_code = d, c
                    lo = max(0, o - 24)
                    best_ctx = txt[lo:o + 24].replace("\n", " ")
    if best_d > max_dist:
        return None, best_d, best_ctx
    return best_code, best_d, best_ctx


def main():
    args = sys.argv[1:]
    days = 5
    if "--days" in args:
        days = int(args[args.index("--days") + 1])
    scan_all = "--all" in args
    max_dist = 60
    if "--maxdist" in args:
        max_dist = int(args[args.index("--maxdist") + 1])
    kw = None
    if "--keyword" in args:
        kw = args[args.index("--keyword") + 1]

    keywords = {kw: SIGNALS.get(kw, [kw])} if kw else \
               (SIGNALS if scan_all else {"翻倍": ["翻倍"]})

    find_codes, dic = load_resolver()
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    code2name = {u["code"]: u["name"] for u in uni}
    code2sym = {u["code"]: u["symbol"] for u in uni}
    posts = fetch_posts(days)
    heat = heat_90d()

    hits = collections.defaultdict(lambda: {
        "sig": set(), "dates": [], "titles": [], "count": 0, "ctx": [], "dist": 999})
    for t in posts:
        txt = clean(t.get("content"))
        matched = [s for s, kws in keywords.items() if any(k in txt for k in kws)]
        if not matched:
            continue
        codes = find_codes(txt)
        if not codes:
            continue
        kws = []
        for s in matched:
            kws += keywords[s]
        primary, dist, ctx = attribute(txt, codes, code2name, kws, max_dist)
        if not primary:
            continue
        h = hits[primary]
        h["sig"].update(matched)
        h["count"] += 1
        h["dist"] = min(h["dist"], dist)
        h["dates"].append(t["create_time"][:10])
        h["titles"].append(txt[:46].replace("\n", " "))
        h["ctx"].append(ctx)

    if not hits:
        print(f"\n近 {days} 天没有出现这些信号的标的。")
        return

    codes = sorted(hits, key=lambda c: (-heat[c], -hits[c]["count"]))
    quotes = get_quotes(codes)

    rows = []
    for c in codes:
        h = hits[c]
        d0 = min(h["dates"])
        q = quotes.get(code2sym.get(c, ""), ("", 0, 0))
        rows.append({
            "代码": c, "名称": code2name.get(c, c),
            "信号": "/".join(sorted(h["sig"])) or "-",
            "首次出现": d0, "5日内出现次数": h["count"],
            "近90天热度": heat[c],
            "热度判定": "✅ 热门" if heat[c] >= 10 else "⚠️ 冷门",
            "距信号词": f"{h['dist']}字",
            "置信": "高" if h["dist"] <= 15 else ("中" if h["dist"] <= 30 else "低"),
            "最新价": round(q[1], 2) if q[1] else "",
            "今日涨跌%": round(q[2], 2) if q[1] else "",
            "内容摘录": h["ctx"][0][:80],
        })

    # 终端输出
    print("\n" + "=" * 108)
    print(f"信号词：{'/'.join(keywords)}　窗口：近 {days} 天　命中标的：{len(rows)} 只")
    print("=" * 108)
    head = (f"{'代码':<8}{'名称':<9}{'信号':<9}{'首次':<12}{'次数':>4}"
            f"{'热度':>5}{'判定':<9}{'距':>4}{'信':>3}{'最新价':>10}{'今日%':>8}")
    print(head)
    print("-" * 108)
    for r in rows:
        print(f"{r['代码']:<8}{r['名称']:<9}{r['信号']:<9}{r['首次出现']:<12}"
              f"{r['5日内出现次数']:>4}{r['近90天热度']:>5}  {r['热度判定']:<7}"
              f"{r['距信号词']:>5}{r['置信']:>3}{r['最新价']:>10}{r['今日涨跌%']:>8}")
        if r["内容摘录"]:
            print(f"        └ {r['内容摘录']}")
    print("-" * 108)

    hot = [r for r in rows if r["近90天热度"] >= 10]
    print(f"\n符合回测条件（热度≥10）：{len(hot)} 只"
          + ("　→ " + "、".join(r["名称"] for r in hot[:8]) if hot else ""))
    print("提示：回测中该信号有效期为 5 个交易日，T+20 中位转负；冷门标的为负超额。")

    # 落盘
    out_csv = os.path.join(BASE, f"signals_{datetime.now():%Y%m%d_%H%M}.csv")
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n明细已保存：{out_csv}")


if __name__ == "__main__":
    main()
