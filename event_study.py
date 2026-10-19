#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事件研究：帖子出现信号 → 次日收盘买入 → T+1/5/20 超额收益"""
import json, os, re, statistics, collections, math, sys
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import semantics as M

SIGNALS = {
    "超预期": ["超预期"],
    "拐点": ["拐点"],
    "反转": ["反转"],
    "涨价/提价": ["涨价", "提价"],
    "强call/推荐": ["强推", "强call", "重点推荐", "继续推荐", "首次覆盖", "买入评级", "维持买入", "强烈推荐"],
    "低估/错杀": ["低估", "错杀"],
}
HEAD = ("超预期", "拐点", "反转")
TAIL = ("涨价/提价", "强call/推荐", "低估/错杀")


def detect(txt):
    """按语义拆分「翻倍」，并保持原有信号优先级"""
    ks = M.kinds(txt)
    fb = []
    if "目标价" in ks:
        fb.append("翻倍(目标价)")
    if "业绩" in ks:
        fb.append("翻倍(业绩)")
    if "称谓" in ks:
        fb.append("翻倍(称谓)")
    if not fb and "模糊" in ks:
        fb.append("翻倍(模糊)")
    out = [g for g in HEAD if any(k in txt for k in SIGNALS[g])]
    out += fb
    out += [g for g in TAIL if any(k in txt for k in SIGNALS[g])]
    return out

def load():
    kl = json.load(open(os.path.join(BASE, "klines.json")))
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    code2sym = {u["code"]: u["symbol"] for u in uni}
    resolved = json.load(open(os.path.join(BASE, "resolved.json")))
    return kl, code2sym, resolved

def to_idx_map(k):
    return {d: i for i, (d, _) in enumerate(k)}

def fwd(k, idxmap, day, n):
    """day 之后第 1 个交易日收盘买入，持有 n 日"""
    # 找到严格晚于 day 的第一个交易日
    later = [d for d, _ in k if d > day]
    if len(later) < n + 1:
        return None
    entry_d = later[0]
    e = idxmap[entry_d]
    if e + n >= len(k):
        return None
    return k[e][1], k[e + n][1], entry_d

def main():
    kl, code2sym, posts = load()
    idx_sh = kl.get("sh000001")
    if not idx_sh:
        print("缺基准 sh000001，行情文件不可用")
        raise SystemExit(1)
    im_sh = to_idx_map(idx_sh)

    rows = []          # (code, signal, postdate, r1, r5, r20, ex1, ex5, ex20)
    seen = set()
    unmatched = 0

    for p in posts:
        txt = p["text"]; day = p["time"][:10]
        sigs = detect(txt)
        if not p["codes"]:
            continue
        # 主标的：文本中最先出现的那个（近似视为标题主角）
        pos = {}
        for c in p["codes"]:
            nm = NAME.get(c)
            if not nm: 
                continue
            i = txt.find(nm)
            if i >= 0:
                pos[c] = i
        order = sorted(pos, key=lambda c: pos[c])
        prim = order[0] if order else p["codes"][0]

        targets = [prim]
        for c in targets:
            sym = code2sym.get(c)
            k = kl.get(sym)
            if not k:
                unmatched += 1
                continue
            im = to_idx_map(k)
            f = fwd(k, im, day, 20)
            if not f:
                continue
            e_close, c20, entry_d = f
            # 指数同窗口
            later_sh = [d for d, _ in idx_sh if d > day]
            if len(later_sh) < 21:
                continue
            se = im_sh[later_sh[0]]
            b20 = idx_sh[se][1]
            e_idx = idx_sh[se][1]
            # 各期
            def ret(kk, imk, base_i, n):
                j = imk.get(entry_d)
                if j is None or j + n >= len(kk):
                    return None
                return kk[j + n][1] / kk[j][1] - 1
            def bret(n):
                j = se
                if j + n >= len(idx_sh):
                    return None
                return idx_sh[j + n][1] / idx_sh[j][1] - 1

            r = {n: ret(k, im, None, n) for n in (1, 5, 20)}
            b = {n: bret(n) for n in (1, 5, 20)}
            if any(r[n] is None or b[n] is None for n in r):
                continue

            tag = sigs[0] if sigs else "无强信号"
            key = (c, tag, day[:7])
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "code": c, "signal": tag, "date": day, "prim": c == prim,
                "kinds": sorted(M.kinds(txt)),
                "ex1": r[1] - b[1], "ex5": r[5] - b[5], "ex20": r[20] - b[20],
                "r20": r[20],
            })

    print(f"有效事件 {len(rows)}，未匹配行情 {unmatched}")
    json.dump(rows, open(os.path.join(BASE, "events.json"), "w"), ensure_ascii=False)

    def summarize(sub, label):
        if len(sub) < 8:
            return None
        out = [label, len(sub)]
        for k in ("ex1", "ex5", "ex20"):
            v = [x[k] for x in sub]
            m = statistics.mean(v)
            sd = statistics.pstdev(v)
            t = m / (sd / math.sqrt(len(v))) if sd > 0 else 0.0
            out.append(m * 100)
            out.append(statistics.median(v) * 100)
            out.append(sum(1 for x in v if x > 0) / len(v) * 100)
            out.append(t)
        return out

    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["signal"]].append(r)

    print("\n信号             N    超额1日均 中位  胜率  t   超额5日均 中位  胜率   t  超额20日均 中位  胜率   t")
    order = ["超预期", "拐点", "反转",
             "翻倍(目标价)", "翻倍(业绩)", "翻倍(称谓)", "翻倍(模糊)",
             "涨价/提价", "强call/推荐", "低估/错杀", "无强信号"]
    for g in order:
        if g not in groups: continue
        s = summarize(groups[g], g)
        if not s: continue
        print(f"{s[0]:14s}{s[1]:5d} " + "  ".join(f"{x:6.2f}" for x in s[2:]))

    # ---- 重叠分层：同一事件可同时命中多种语义，用于检验上面互斥分组是否稳健 ----
    print("\n[重叠分层] 命中的帖子含该类语义即计入（不互斥）")
    print(f"{'语义':14s}{'N':>5}  超额5日均 中位  胜率   t   超额20日均 中位  胜率   t")
    for kd in ("目标价", "业绩", "称谓", "模糊"):
        sub = [r for r in rows if kd in r["kinds"]]
        s = summarize(sub, kd)
        if not s:
            continue
        print(f"{kd:14s}{s[1]:5d} " + "  ".join(f"{x:6.2f}" for x in
              (s[6], s[7], s[8], s[9], s[10], s[11], s[12], s[13])))

if __name__ == "__main__":
    # 名称映射（全名优先）
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    NAME = {u["code"]: u["name"] for u in uni}
    main()
