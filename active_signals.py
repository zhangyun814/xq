#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""近3个月活跃池 × 近5天「翻倍空间」信号
------------------------------------------------
池子：近 N 天（默认 90）被星球提到过的股票
信号：近 M 天（默认 5）帖子里的「翻倍 / 2倍空间 / 1.5倍空间」——且只算「目标价」语义

语义分层（见 semantics.py）：
  目标价  "当前位置至少翻倍以上空间" / "股价翻倍" / "2倍空间"  ← 买入信号，默认只留这层
  业绩    "净利同比翻倍" / "环比翻倍" / "营收翻倍增长"        ← 已兑现的基本面，非买入信号
  称谓    "十倍股下跌幅度60%-80%"                             ← 在讲一类股票，不是给目标
  模糊    上下文判不出来                                      ← 需人工瞄一眼

回测（2025-08~2026-10，1778 个事件，相对上证超额）：
  目标价口径 T+20 均值 +11.28%、中位 +3.53%、胜率 54.3%、t=2.67（N=46）
  业绩口径   T+20 均值  −1.44%、中位 −1.96%、胜率 41.8%（N=91）
  → 有效的是"目标价"这层，且窗口在 20 个交易日，不是 5 个

对外接口：
    screen(mode, win5, pool_days, log) -> dict   给 webapp 调用，返回结构化结果
    python3 active_signals.py                    命令行
"""
import json, os, sys, csv, collections
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("ZSXQ_OUT", BASE)          # 可写目录（容器里挂 volume）
sys.path.insert(0, BASE)

import resolve_v2 as rv
import scan as S
import trace as T
import semantics as M
import exit_rules as X

KEEP = {"目标价"}
HOLD, TP = 20, 0.10       # 出场规则：20 日到期 / 止盈 +10%（见 exit_rules.py 回测）

# 进程内缓存：webapp 每次点筛选都会进 screen()，别重复读 24M 的 klines
_C = {}


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except Exception:
        return 0


def _klines():
    """带 mtime 失效：宿主那边刷新完 klines.json，容器不重启也能拿到新数据。"""
    p = os.path.join(BASE, "klines.json")
    mt = _mtime(p)
    if _C.get("kl_mt") != mt:
        try:
            _C["kl"] = json.load(open(p))
        except Exception:
            _C["kl"] = {}
        _C["kl_mt"] = mt
    return _C.get("kl") or {}


def _resolver():
    p = os.path.join(BASE, "alias.json")
    mt = _mtime(p)
    if _C.get("rv_mt") != mt or "rv" not in _C:
        _C["rv"] = rv.build()
        _C["rv_mt"] = mt
    return _C["rv"]


def _universe():
    p = os.path.join(BASE, "universe.json")
    mt = _mtime(p)
    if _C.get("uni_mt") != mt or "uni" not in _C:
        _C["uni"] = json.load(open(p))
        _C["c2n"] = {u["code"]: u["name"] for u in _C["uni"]}
        _C["c2s"] = {u["code"]: u["symbol"] for u in _C["uni"]}
        # 券商名多出现在研报署名（"欢迎联系中信证券农业团队"），会被误当标的
        _C["brokers"] = {c for c, n in _C["c2n"].items() if n.endswith(("证券", "期货"))}
        _C["uni_mt"] = mt
    return _C["uni"], _C["c2n"], _C["c2s"], _C["brokers"]


def snap_path(mode):
    # 按口径分文件存，否则 all 口径会把默认口径的对比基线冲掉
    return os.path.join(OUT, f"last_screen_{mode}.json")


def load_snap(mode="target"):
    p = snap_path(mode)
    if not os.path.exists(p):
        return {}, ""
    try:
        o = json.load(open(p))
        return o.get("rows", {}), o.get("time", "")
    except Exception:
        return {}, ""


def save_snap(rows, mode="target"):
    o = {"time": datetime.now().strftime("%Y-%m-%d %H:%M"),
         "rows": {r["代码"]: {"名称": r["名称"], "信号日": r["最近信号日"],
                            "状态": r["状态"], "盈亏": r["浮动盈亏%"]} for r in rows}}
    try:
        os.makedirs(OUT, exist_ok=True)
        json.dump(o, open(snap_path(mode), "w"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def csv_path(mode, win5):
    tag = "" if mode == "target" else "_全口径"
    return os.path.join(OUT, f"活跃池_近{win5}天翻倍{tag}.csv")


def write_csv(rows, mode, win5):
    """命令行和 Web 都用这一份，别让导出格式两处维护。"""
    if not rows:
        return None
    f = csv_path(mode, win5)
    try:
        os.makedirs(OUT, exist_ok=True)
        with open(f, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        return f
    except Exception as e:
        print(f"!! CSV 保存失败（目录可能只读）：{e}")
        return None


def diff_of(rows, prev):
    """跟上次筛选比：谁新进来、谁掉出去、谁的状态变了。"""
    cur = {r["代码"]: r for r in rows}
    new = [c for c in cur if c not in prev]
    gone = [c for c in prev if c not in cur]
    moved = [c for c in cur if c in prev and cur[c]["状态"] != prev[c].get("状态")]
    return {
        "new": [{"代码": c, "名称": cur[c]["名称"], "信号日": cur[c]["最近信号日"],
                 "状态": cur[c]["状态"]} for c in new],
        "gone": [{"代码": c, "名称": prev[c].get("名称", ""),
                  "上次状态": prev[c].get("状态", "")} for c in gone],
        "moved": [{"代码": c, "名称": cur[c]["名称"], "from": prev[c].get("状态", ""),
                   "to": cur[c]["状态"], "盈亏": cur[c]["浮动盈亏%"]} for c in moved],
    }


def load_all_posts(days=5, log=print):
    """历史全量 + 最新抓取。会真正去星球拉一次（30 分钟内复用缓存），
    否则跑出来的只是上次留下的快照。容器内没有 zsxq-cli 时会说明后回落语料。"""
    posts = {}
    rp = os.path.join(BASE, "resolved.json")
    if os.path.exists(rp):
        for o in json.load(open(rp)):
            posts[(o["time"], o["text"][:40])] = (o["time"][:10], o["text"])
    refreshed = False
    source = "corpus"
    # 容器里没有 zsxq-cli（凭证是 macOS 钥匙串加密的），抓帖由宿主机代劳，
    # 它写完 scan_cache.json 后通过挂载传进来。这里不该报"失败"，只是换了个来源。
    import shutil as _sh
    if not _sh.which("zsxq-cli"):
        log("本机无星球连接，使用已同步的语料文件（抓帖请在终端跑 active_signals.py）")
        fresh = []
    else:
        try:
            fresh = S.fetch_posts(days)
            source = S.LAST_FETCH_SOURCE or "corpus"
            refreshed = source == "network"
            log(f"帖子 {len(fresh)} 条（来源："
                + {"network": "本次实时抓取", "cache": "30 分钟内的抓取缓存"}.get(source, "本地语料")
                + "）")
        except Exception as e:
            log(f"!! 抓取失败，仅用本地语料：{e}")
            fresh = []
    c = os.path.join(BASE, "scan_cache.json")
    src = [{"create_time": t["create_time"], "content": t.get("content")} for t in fresh]
    if not src and os.path.exists(c):
        src = json.load(open(c)).get("posts", [])
        source = "corpus"
    for t in src:
        txt = S.clean(t.get("content"))
        posts[(t["create_time"][:19], txt[:40])] = (t["create_time"][:10], txt)
    return sorted(posts.values()), refreshed, source


def screen(mode="target", win5=5, pool_days=90, log=print, save=True):
    """跑一次筛选，返回结构化结果。CLI 和 Web 共用这一份逻辑，避免两套实现漂移。"""
    keep = M.KEEP_ALL if mode == "all" else KEEP
    today = datetime.now()
    c5 = (today - timedelta(days=win5)).strftime("%Y-%m-%d")
    cp = (today - timedelta(days=pool_days)).strftime("%Y-%m-%d")

    log("载入股票名录与别名表…")
    d, idx = _resolver()
    uni, c2n, c2s, brokers = _universe()

    log(f"载入语料 + 尝试抓取近 {win5} 天新帖…")
    posts, refreshed, source = load_all_posts(win5, log)
    newest = max(p[0] for p in posts)
    rp = os.path.join(BASE, "resolved.json")
    rv_age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(rp))).days \
        if os.path.exists(rp) else 999

    pool = collections.Counter()
    for day, txt in posts:
        if day < cp:
            continue
        for c in rv.find_codes(txt, d, idx):
            pool[c] += 1
    log(f"活跃池（近 {pool_days} 天被提及）：{len(pool)} 只")

    hits = collections.defaultdict(lambda: {"dates": [], "ctx": [], "kw": set(), "kind": set()})
    dropped = collections.Counter()
    for day, txt in posts:
        if day < c5:
            continue
        occ = M.occurrences(txt)
        if not occ:
            continue
        want = [s for s, k in occ if k in keep]
        if not want:
            for _, k in occ:
                dropped[k] += 1
            continue
        codes = rv.find_codes(txt, d, idx)
        if not codes:
            continue
        codes = {c for c in codes if c not in brokers}
        if not codes:
            continue
        code, dist, ctx = S.attribute(txt, codes, c2n, want)
        if not code or code not in pool:
            continue
        h = hits[code]
        h["dates"].append(day)
        h["ctx"].append(ctx)
        h["kw"].update(want)
        h["kind"].update(k for s, k in occ if s in want)

    meta = {"mode": mode, "win5": win5, "pool_days": pool_days,
            "corpus_newest": newest, "corpus_age_days": rv_age,
            "refreshed": refreshed, "corpus_source": source,
            "pool_size": len(pool),
            "dropped": dict(dropped), "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "hold": HOLD, "tp": TP}

    if not hits:
        log(f"近 {win5} 天没有落在活跃池里的「目标价翻倍」信号。")
        prev, prev_time = load_snap(mode)
        meta["prev_time"] = prev_time
        meta["prev_count"] = len(prev)
        if save:
            save_snap([], mode)
        return {"rows": [], "meta": meta,
                "diff": {"new": [],
                         "gone": [{"代码": c, "名称": v.get("名称", ""),
                                   "上次状态": v.get("状态", "")}
                                  for c, v in prev.items()],
                         "moved": []}}

    codes = sorted(hits, key=lambda c: -pool[c])
    log(f"命中 {len(codes)} 只，拉取实时行情…")
    try:
        live = {s: v[1] for s, v in S.get_quotes(codes).items()}
    except Exception:
        live = {}
    kl = _klines()

    rows = []
    for i, c in enumerate(codes, 1):
        k = T.kline_qfq(c2s[c]) if c in c2s else None
        log(f"  [{i}/{len(codes)}] {c2n.get(c, c)} 拉日线…" if k
            else f"  [{i}/{len(codes)}] {c2n.get(c, c)} 日线失败，用缓存")
        px = live.get(c2s.get(c))
        last = px or (k[-1][1] if k else None)
        r5 = r90 = ""
        if k and last:
            e5 = [p for dt_, p in k if dt_ <= c5]
            e90 = [p for dt_, p in k if dt_ <= cp]
            if e5:
                r5 = round((last / e5[-1] - 1) * 100, 2)
            if e90:
                r90 = round((last / e90[-1] - 1) * 100, 2)
        pl = (X.plan_k(k, max(hits[c]["dates"]), HOLD, TP)
              or X.plan(kl, c2s, c, max(hits[c]["dates"]), HOLD, TP)
              or {"买入日": "", "买入价": "", "止盈价": "", "到期日": "", "已持有": "", "状态": ""})
        bp = pl.get("买入价")
        pnl = round((last / bp - 1) * 100, 2) if (bp and last) else ""
        rows.append({
            "代码": c, "名称": c2n.get(c, c),
            "语义": "/".join(sorted(hits[c]["kind"])),
            "近%d天提及" % pool_days: pool[c],
            "近%d天信号帖" % win5: len(hits[c]["dates"]),
            "最近信号日": max(hits[c]["dates"]),
            "命中词": "/".join(sorted(hits[c]["kw"])),
            **pl,
            "最新价": round(last, 2) if last else "",
            "浮动盈亏%": pnl,
            "近5日涨跌%": r5,
            "近90日涨跌%": r90,
            "原文": hits[c]["ctx"][-1][:72],
        })

    prev, prev_time = load_snap(mode)
    meta["prev_time"] = prev_time
    meta["prev_count"] = len(prev)
    dd = diff_of(rows, prev)
    if save:
        save_snap(rows, mode)
        meta["csv"] = write_csv(rows, mode, win5)
    return {"rows": rows, "meta": meta, "diff": dd}


# ---------------- 命令行 ----------------
def main():
    a = sys.argv[1:]
    win5 = int(a[a.index("--win5") + 1]) if "--win5" in a else 5
    pool_days = int(a[a.index("--pool") + 1]) if "--pool" in a else 90
    mode = a[a.index("--mode") + 1] if "--mode" in a else "target"

    r = screen(mode, win5, pool_days)
    rows, meta, dd = r["rows"], r["meta"], r["diff"]
    k90 = "近%d天提及" % pool_days

    print("=" * 112)
    print(f"活跃池（近{pool_days}天被提及）∩ 近{win5}天「翻倍空间」信号：{len(rows)} 只　口径={mode}")
    print("买入规则：信号次日买入，不等回调（回测见 entry_rules.py）")
    print(f"出场规则：暂不设止损；如需机械出场，止盈 +{TP*100:.0f}% 或 持满 {HOLD} 日")
    print(f"语料最新 {meta['corpus_newest']}（{meta['corpus_age_days']} 天前）"
          f"　帖子来源={ {'network': '本次实时抓取', 'cache': '抓取缓存'}.get(meta['corpus_source'], '本地语料') }")
    print("=" * 112)
    print(f"{'代码':<8}{'名称':<9}{'信号日':>12}{'买入日':>12}{'买入价':>8}"
          f"{'最新价':>8}{'浮动盈亏':>9}{'状态':>14}")
    print("-" * 112)
    for x in rows:
        pnl = f"{x['浮动盈亏%']:+.2f}%" if x["浮动盈亏%"] != "" else "—"
        print(f"{x['代码']:<8}{x['名称']:<9}{x['最近信号日']:>12}{str(x['买入日']):>12}"
              f"{str(x['买入价']):>8}{str(x['最新价']):>8}{pnl:>9}{x['状态']:>14}")
        print(f"        └ [{x['命中词']}] 提及{x[k90]}次 · 近5日{x['近5日涨跌%']}% · "
              f"近90日{x['近90日涨跌%']}%")
        print(f"          {x['原文']}")
    print("-" * 112)

    if not meta["prev_count"]:
        print("\n[首次运行，无历史可比。下次再跑就会显示变化]")
    else:
        print(f"\n[跟上次筛选（{meta['prev_time']}，{meta['prev_count']} 只）比]")
        if not (dd["new"] or dd["gone"] or dd["moved"]):
            print("  名单和状态都没变。")
        for x in dd["new"]:
            print(f"  + 新增 {x['代码']} {x['名称']}　信号日 {x['信号日']}　{x['状态']}")
        for x in dd["gone"]:
            print(f"  − 剔除 {x['代码']} {x['名称']}　（上次 {x['上次状态']}，"
                  f"这次已不在近{win5}天信号池里）")
        for x in dd["moved"]:
            p = x["盈亏"]
            p = f"{p:+.2f}%" if isinstance(p, (int, float)) else "—"
            print(f"  ~ 变化 {x['代码']} {x['名称']}　{x['from']} → {x['to']}　盈亏 {p}")

    hot = [x for x in rows if x[k90] >= 10]
    print(f"近{pool_days}天提及≥10（回测有效档）：{len(hot)} 只"
          + ("　→ " + "、".join(y["名称"] for y in hot) if hot else "　全在冷门档"))
    if meta["dropped"]:
        print("已剔除的其他口径：" + "、".join(f"{k} {v} 帖" for k, v in
                                    sorted(meta["dropped"].items(), key=lambda z: -z[1]))
              + "（--mode all 可一并查看）")

    if rows:
        print(f"\n已保存：{meta.get('csv') or '（未写入）'}")


if __name__ == "__main__":
    main()
