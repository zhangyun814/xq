#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建A股全域名称字典 + 简称别名，落盘 universe.json"""
import json, os, subprocess, time, re

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "universe.json")

def fetch_page(page):
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node=hs_a")
    r = subprocess.run(["curl", "-s", "--max-time", "25", "-H",
                        "Referer: https://finance.sina.com.cn", url],
                       capture_output=True, text=True)
    txt = r.stdout.strip()
    if not txt or txt in ("null", "[]"):
        return []
    try:
        return json.loads(txt)
    except Exception:
        return []

def main():
    rows, page = [], 1
    while page <= 80:
        d = fetch_page(page)
        if not d:
            break
        rows += d
        if page % 10 == 0:
            print("page", page, "total", len(rows), flush=True)
        page += 1
        time.sleep(0.25)

    seen, uni = set(), []
    for r in rows:
        code = r.get("code"); name = r.get("name"); sym = r.get("symbol", "")
        if not code or not name or code in seen:
            continue
        seen.add(code)
        uni.append({"code": code, "name": name, "symbol": sym,
                    "mktcap": float(r.get("mktcap") or 0)})
    print("universe", len(uni))
    json.dump(uni, open(OUT, "w"), ensure_ascii=False)

    # 别名：去地域/属性前缀 或 去行业后缀，且全局唯一才算
    PREFIX = ["中国", "中", "东方", "南方", "北方", "上海", "北京", "深圳", "广州", "江苏",
              "浙江", "山东", "四川", "湖南", "湖北", "河南", "河北", "安徽", "福建", "广东",
              "辽宁", "吉林", "陕西", "山西", "云南", "贵州", "江西", "甘肃", "新疆", "青海",
              "宁夏", "内蒙", "西藏", "海南", "天津", "重庆", "厦门", "青岛", "大连", "苏州",
              "无锡", "宁波", "杭州", "南京", "武汉", "成都", "西安", "合肥", "济南", "烟台"]
    SUFFIX = ["科技", "股份", "集团", "电器", "电子", "电气", "能源", "环保", "生物", "医药",
              "制药", "医疗", "材料", "新材", "光电", "通信", "软件", "网络", "传媒", "文化",
              "实业", "发展", "控股", "国际", "汽车", "机械", "重工", "化工", "化学", "食品",
              "酒业", "银行", "证券", "保险", "地产", "置业", "建设", "工程", "环境", "水务",
              "电力", "燃气", "物流", "交通", "航空", "钢铁", "矿业", "资源", "金属", "铝业",
              "铜业", "黄金", "稀土", "锂业", "储能", "新能源", "半导体", "微电子", "信息",
              "数据", "智能", "精密", "装备", "制造", "工业", "轻工", "纺织", "服装", "家居",
              "农业", "牧业", "种业", "渔业", "林业", "旅游", "酒店", "商业", "百货", "超市"]
    names = {u["name"]: u["code"] for u in uni}
    alias = {}
    for u in uni:
        nm = u["name"]
        cands = set()
        for p in PREFIX:
            if nm.startswith(p) and len(nm) - len(p) >= 2:
                cands.add(nm[len(p):])
        for s in SUFFIX:
            if nm.endswith(s) and len(nm) - len(s) >= 2:
                cands.add(nm[:-len(s)])
        # 4字名取后两字（如 中际旭创->旭创）
        if len(nm) == 4:
            cands.add(nm[2:])
        for c in cands:
            if len(c) < 2:
                continue
            if c in names:      # 与某只股票全名撞车，放弃
                continue
            if c in alias and alias[c] != u["code"]:
                alias[c] = None  # 冲突
            else:
                alias.setdefault(c, u["code"])
    alias = {k: v for k, v in alias.items() if v}
    print("alias", len(alias))
    json.dump(alias, open(os.path.join(BASE, "alias.json"), "w"), ensure_ascii=False)

if __name__ == "__main__":
    main()
