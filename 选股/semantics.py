#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信号词语义消歧
------------------------------------------------
问题：「翻倍」在星球语料里至少有三层意思，混在一起统计会互相抵消。

  A 目标价  —— "当前位置至少翻倍以上空间""股价翻倍""2倍空间""市值翻倍"
  B 业绩    —— "净利润同比翻倍""环比翻倍""营收翻倍增长""翻倍指引"
  C 称谓    —— "十倍股下跌幅度普遍60%-80%"（在讲一类股票的回撤，不是给目标）

只有 A 是「买入信号」；B 是基本面事实（往往已经反映在股价里）；C 是噪音。
本模块按「信号词前后 ±窗口内，目标词与业绩词谁更近」判定归属。
"""
import re

# 目标价语义词
PRICE = ("空间", "股价", "目标价", "市值", "弹性", "涨幅", "上行", "赔率",
         "上看", "至少", "位置")
# 业绩语义词
EARN = ("净利", "扣非", "归母", "营收", "收入", "毛利", "业绩", "利润",
        "同比", "环比", "增长", "高增", "增速", "指引", "销量", "销售",
        "出货", "订单", "单季", "装机", "产量", "季度", "前三季", "全年",
        "交付", "预增", "中值", "上限", "翻番")

WIN_L, WIN_R = 18, 22

KEEP_ALL = {"目标价", "业绩", "称谓", "模糊"}

# 已完成的事实："股价已接近翻倍""年内累计涨了翻倍"——描述过去，不是给目标
PAST = ("已", "接近", "涨了", "过去", "年内", "累计", "涨幅达", "走出", "实现")
# 未来指向："有望翻倍""看翻倍空间""至少翻倍"
FUTURE = ("有望", "看", "目标", "空间", "至少", "将", "预计", "还能", "冲击")


def _nearest(txt, i, words, lo, hi):
    """词表里距离位置 i 最近的词的距离；无则 None"""
    best = None
    for w in words:
        for m in re.finditer(re.escape(w), txt[lo:hi]):
            d = abs(lo + m.start() + len(w) // 2 - i)
            if best is None or d < best:
                best = d
    return best


def kind_of(txt, i, span=2):
    """判定位置 i 处（长度 span）的信号词语义：目标价 / 业绩 / 称谓 / 模糊"""
    tail = txt[i + span:i + span + 2]
    head = txt[max(0, i - 3):i]
    # 称谓：十倍股 / 五倍股 / 3倍股
    if tail.startswith("股") or "倍股" in head + txt[i:i + span] + tail:
        return "称谓"
    lo, hi = max(0, i - WIN_L), min(len(txt), i + span + WIN_R)
    win = txt[lo:hi]
    pre = txt[max(0, i - 8):i]
    # 「股价已接近翻倍」这类是复述涨幅，不是目标价
    if any(x in pre for x in PAST) and not any(x in win for x in FUTURE):
        return "业绩"
    dp = _nearest(txt, i, PRICE, lo, hi)
    de = _nearest(txt, i, EARN, lo, hi)
    if dp is None and de is None:
        return "模糊"
    if de is None:
        return "目标价"
    if dp is None:
        return "业绩"
    return "目标价" if dp <= de else "业绩"


# 数字倍数："2倍空间""1.5倍空间""5倍涨幅""十倍股"
# 必须后接 空间/股/涨幅/弹性，否则 9倍PE、13倍EBITDA、提升4倍 会被大量误收
MULT_RE = re.compile(
    r"([0-9]{1,2}(?:\.[0-9])?|[一二两三四五六七八九十]{1,2})\s*倍"
    r"(?:以上)?(?:的)?\s*(?:空间|上涨空间|涨幅|弹性|股)"
)
VAL_BAD = ("PE", "PB", "PS", "EBITDA", "市盈", "市净", "PEG", "EV/", "ROE")


def occurrences(txt):
    """返回 [(片段, 语义)]，已剔除估值倍数语境"""
    out = []
    for m in re.finditer("翻倍", txt):
        out.append((m.group(0), kind_of(txt, m.start())))
    for m in MULT_RE.finditer(txt):
        # 排除 9倍PE / 13倍EBITDA
        if any(b in txt[max(0, m.start() - 8):m.start()] for b in VAL_BAD):
            continue
        out.append((m.group(0).strip(), kind_of(txt, m.start(), len(m.group(0)) - 2)))
    # 去重（同一位置不重复计）
    seen, uniq = set(), []
    for s, k in out:
        if (s, k) in seen:
            continue
        seen.add((s, k))
        uniq.append((s, k))
    return uniq


def kinds(txt):
    """该帖命中的语义集合"""
    return {k for _, k in occurrences(txt)}


def snippets(txt, want):
    """只要指定语义的片段，供距离归属用"""
    return [s for s, k in occurrences(txt) if k == want]
