#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实体识别 v2：剔除机构名干扰、只保留高置信别名，并做精度抽检"""
import json, os, re, glob, collections, random

BASE = os.path.dirname(os.path.abspath(__file__))
TAG_RE = re.compile(r"<e[^>]*/>|<[^>]+>")
BRACKET = re.compile(r"【[^】]{0,20}】")

def clean(s):
    s = TAG_RE.sub(" ", s or "").replace("\u200b", "")
    s = BRACKET.sub(" ", s)          # 去掉【机构/栏目】标记，避免"天风""东吴"被当股票
    return re.sub(r"[ \t]+", " ", s).strip()

BLACK = set("""石油 石化 煤炭 钢铁 有色 化工 银行 证券 保险 地产 电力 医药 汽车 机械 电子
通信 科技 食品 农业 材料 能源 环保 传媒 软件 数据 智能 信息 工业 装备 资源 黄金 稀土
锂电 光伏 风电 储能 半导体 芯片 军工 旅游 酒店 商业 百货 超市 纺织 服装 家居 种业 渔业
生物 制药 医疗 新材 光电 网络 文化 实业 发展 控股 国际 重工 化学 酒业 置业 建设 工程
环境 水务 燃气 物流 交通 航空 矿业 金属 铝业 铜业 微电 精密 制造 轻工 牧业 林业 发电
趋势 龙头 驱动 第一 深度 存储 修复 机器 领先 先进 稳健 一致 认证 复合 制程 光纤 线下
矩阵 电商 金融 开源 南亚 完美 中国 美国 全球 市场 公司 行业 赛道 板块 龙头 标的
机器人 驱动力 值得买 好想你 我爱我家 天地在线 线上线下 每日互动 大智慧 三人行 万事利 新经典""".split())

NICK = {
    "宁王": "300750", "迪王": "002594", "茅台": "600519", "招行": "600036",
    "工行": "601398", "建行": "601939", "农行": "601288", "中免": "601888",
    "万科": "000002", "隆基": "601012", "通威": "600438", "亿纬": "300014",
    "药明": "603259", "恒瑞": "600276", "迈瑞": "300760", "爱尔": "300015",
    "立讯": "002475", "歌尔": "002241", "蓝思": "300433", "京东方": "000725",
    "海康": "002415", "大华": "002236", "浪潮": "000977", "中际": "300308",
    "旭创": "300308", "新易盛": "300502", "天孚": "300394", "中微": "688012",
    "拓荆": "688072", "华海": "688200", "寒武纪": "688256", "海光": "688041",
    "中芯": "688981", "华虹": "688347", "兆易": "603986", "韦尔": "603501",
    "闻泰": "600745", "三安": "600703", "宁德": "300750", "比亚迪": "002594",
    "格力": "000651", "美的": "000333", "海尔": "600690", "潍柴": "000338",
    "三一": "600031", "徐工": "000425", "中车": "601766", "中石油": "601857",
    "中石化": "600028", "神华": "601088", "陕煤": "601225", "兖矿": "600188",
    "紫金": "601899", "北方稀土": "600111", "赣锋": "002460", "天齐": "002466",
    "恩捷": "002812", "当升": "300073", "汇川": "300124", "中控": "688777",
    "埃斯顿": "002747", "中信证券": "600030", "东方财富": "300059",
    "同花顺": "300033", "金山办公": "688111", "用友": "600588", "广联达": "002410",
    "分众": "002027", "芒果": "300413", "光线": "300251", "三七": "002555",
    "吉比特": "603444", "长鑫": "688825", "芯朋微": "688508", "峰岹": "688279",
    "涛涛": "301345", "双元": "688623", "马应龙": "600993", "飞荣达": "300602",
}

def build():
    uni = json.load(open(os.path.join(BASE, "universe.json")))
    alias = json.load(open(os.path.join(BASE, "alias.json")))
    d = {}
    for u in uni:
        if u["name"] in BLACK:      # 全名本身就是常用词的（机器人、驱动力…）直接剔除
            continue
        d[u["name"]] = u["code"]
    # 仅接受 3 字及以上别名（2字别名歧义率过高，由 NICK 人工兜底）
    for k, v in alias.items():
        if len(k) >= 3 and k not in BLACK and k not in d:
            d[k] = v
    d.update(NICK)
    idx = collections.defaultdict(list)
    for k in d:
        idx[k[0]].append(k)
    for k in idx:
        idx[k].sort(key=len, reverse=True)
    return d, idx

def find(txt, d, idx):
    hit = set()
    for i, ch in enumerate(txt):
        for key in idx.get(ch, ()):
            if txt.startswith(key, i):
                hit.add(d[key])
                break_ = False
    return hit

def find_codes(txt, d, idx):
    hit = set()
    for i, ch in enumerate(txt):
        for key in idx.get(ch, ()):
            if txt.startswith(key, i):
                hit.add(d[key])
    return hit

def main():
    d, idx = build()
    print("字典条目", len(d))
    posts = {}
    for pat in ("raw/*.json", "samples/*.json"):
        for f in glob.glob(os.path.join(BASE, pat)):
            j = json.load(open(f))
            for t in j.get("topics_brief", []) or []:
                posts[t["topic_id"]] = t
    rows = [(t["topic_id"], t["create_time"][:19], clean(t.get("content"))) for t in posts.values()]

    cnt = collections.Counter(); out = []
    for tid, ct, txt in rows:
        cs = find_codes(txt, d, idx)
        out.append({"id": tid, "time": ct, "codes": sorted(cs), "text": txt})
        cnt.update(cs)
    json.dump(out, open(os.path.join(BASE, "resolved.json"), "w"), ensure_ascii=False)

    nm = {}
    for k, v in d.items():
        nm.setdefault(v, k)
    print(f"帖子 {len(rows)}，提及 {sum(len(o['codes']) for o in out)} 次，覆盖 {len(cnt)} 只")
    print(f"含标的的帖子 {sum(1 for o in out if o['codes'])} ({sum(1 for o in out if o['codes'])/len(rows)*100:.1f}%)")
    print("\n== top25 ==")
    for c, k in cnt.most_common(25):
        print(f"  {nm.get(c,c):10s} {c}  {k}")

    print("\n== 精度抽检：随机 12 篇，看识别是否合理 ==")
    random.seed(7)
    for o in random.sample(out, 12):
        names = [nm.get(c, c) for c in o["codes"]][:6]
        print(f"[{o['time'][:10]}] {o['text'][:58]}")
        print(f"    → {names}")

if __name__ == "__main__":
    main()
