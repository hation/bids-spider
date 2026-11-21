"""中标/废标/招标公告结果查询脚本（只查库，不爬网站）

按时间范围 + 公告类型/地区/关键词/中标单位从 ES 过滤，生成：
- 查询结果.md（分三组：招标中 / 已中标 / 已流标）
- 查询结果.xlsx（全字段明细）

输出：output/结果查询/<查询时间_YYYYMMDD_HHMMSS>_<起>_<止>/

用法：
    python scripts/query_results.py                          # 默认近 30 天，全类型
    python scripts/query_results.py 2026-11-01 2026-11-21    # 自定义起止
    python scripts/query_results.py 2026-11-01 2026-11-21 --regions beijing,shandong
    python scripts/query_results.py --types 中标,废标
    python scripts/query_results.py --kw 服务器,存储
    python scripts/query_results.py --company 华宇科技
"""
import os
import re
import sys
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from analyze_today import region_name, classify_type
from utils.es import ESConnection

OUTPUT_BASE = os.path.join("output", "结果查询")

# 公告类型归一（spec 定义的三类）
ANNOUNCE_TYPES = ['招标公告', '中标公告', '废标公告']


def _norm_type(t):
    """把用户传入的短名（中标/废标/招标）归一为 ES 存储的完整类型名。"""
    t = t.strip()
    if t == '中标':
        return '中标公告'
    if t == '废标':
        return '废标公告'
    if t == '招标':
        return '招标公告'
    return t


def load_range(start_date, end_date, regions=None, types=None, kw=None, company=None):
    """从 ES 按 release_date 范围 + 过滤条件拉取公告。"""
    es = ESConnection()
    client = es._client()
    must = [{"range": {"release_date": {"gte": start_date, "lte": end_date + " 23:59:59"}}}]
    if regions:
        must.append({"terms": {"region": regions}})
    if types:
        must.append({"terms": {"announce_type": [_norm_type(t) for t in types]}})
    if kw:
        must.append({"multi_match": {"query": kw, "fields": ["title", "html"]}})
    if company:
        must.append({"wildcard": {"win_company": f"*{company}*"}})
    query = {"query": {"bool": {"must": must}}, "sort": ["_doc"]}
    records = []
    try:
        resp = client.search(index="tenders", body=query, scroll="10m", size=1000, _source=True)
        sid = resp["_scroll_id"]
        hits = resp["hits"]["hits"]
        records.extend(hits)
        while hits:
            resp = client.scroll(scroll_id=sid, scroll="10m")
            hits = resp["hits"]["hits"]
            records.extend(hits)
        try:
            client.clear_scroll(scroll_id=sid)
        except Exception:
            pass
    except Exception as e:
        print(f"[query_results] ES 查询失败: {e}")
        return []
    return [h["_source"] for h in records]


def build_df(docs):
    from crawler.base_crawler import BaseCrawler
    bc = BaseCrawler.__new__(BaseCrawler)
    # openpyxl 不接受的 XML 非法控制字符（与 base_crawler 导出清洗一致），正文清洗避免整表写入失败
    _illegal = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
    rows = []
    for d in docs:
        html_text = bc._html_to_text(str(d.get("html", "") or ""))
        atype = d.get("announce_type", "") or classify_type(d.get("title", ""))
        if atype in ('中标/成交', '合同公告', '更正公告', '废标/终止'):
            # 旧数据回填前/漏分类的兜底：映射到三类
            atype = {'废标/终止': '废标公告'}.get(atype, '中标公告' if '中标' in atype or '成交' in atype else '招标公告')
        row = {
            "地区": region_name(d.get("region", "")),
            "公告类型": atype,
            "商机标题": d.get("title", ""),
            "中标单位": d.get("win_company", ""),
            "中标金额(万元)": d.get("win_amount_wan"),
            "公告链接": d.get("href", ""),
            "金额(万元)": d.get("amount_wan"),
            "投标截止": d.get("bid_deadline", ""),
            "发布日期": d.get("release_date", ""),
            "商机详情": html_text,
            "详情截断": "是" if len(html_text) > 32767 else "否",
        }
        for k, v in row.items():
            if isinstance(v, str):
                row[k] = _illegal.sub('', v)
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("发布日期", ascending=False).reset_index(drop=True)
    return df


def build_md(df, start_date, end_date):
    def table(sub, limit=50, extra_cols=()):
        if sub.empty:
            return "_（无）_"
        cols = ["地区", "商机标题"] + list(extra_cols) + ["发布日期"]
        lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for _, r in sub.head(limit).iterrows():
            cells = [r["地区"], str(r["商机标题"])[:44]]
            for c in extra_cols:
                v = r[c]
                if pd.isna(v) or v == '':
                    cells.append("—")
                elif c == "中标金额(万元)":
                    cells.append(f"{v:,.1f}")
                else:
                    cells.append(str(v))
            cells.append(str(r["发布日期"])[:10])
            lines.append("| " + " | ".join(cells) + " |")
        if len(sub) > limit:
            lines.append(f"| ... | 另有 {len(sub) - limit} 条 |")
        return "\n".join(lines)

    bidding = df[df["公告类型"] == "招标公告"]
    won = df[df["公告类型"] == "中标公告"].sort_values("中标金额(万元)", ascending=False)
    failed = df[df["公告类型"] == "废标公告"]

    report = f"""# 招标结果查询：{start_date} ~ {end_date}

## 一、总览

- 招标中（招标公告）：**{len(bidding)} 条**
- 已中标（中标公告）：**{len(won)} 条**
- 已流标（废标公告）：**{len(failed)} 条**

## 二、招标中（按发布日期倒序）

{table(bidding)}

## 三、已中标（按中标金额降序）

{table(won, extra_cols=("中标单位", "中标金额(万元)"))}

## 四、已流标（废标/终止公告）

{table(failed)}

---
生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。数据来源 ES（tenders 索引），按公告类型分组。
"""
    return report


def main():
    args = sys.argv[1:]
    non_flag = [a for a in args if not a.startswith("--")]
    regions = types = kw = company = None
    i = 0
    while i < len(args):
        if args[i] == "--regions" and i + 1 < len(args):
            regions = [r.strip() for r in args[i + 1].split(",") if r.strip()]
            i += 2
        elif args[i] == "--types" and i + 1 < len(args):
            types = [t.strip() for t in args[i + 1].split(",") if t.strip()]
            i += 2
        elif args[i] == "--kw" and i + 1 < len(args):
            kw = args[i + 1]
            i += 2
        elif args[i] == "--company" and i + 1 < len(args):
            company = args[i + 1]
            i += 2
        else:
            i += 1

    if len(non_flag) >= 2:
        start_date, end_date = non_flag[0], non_flag[1]
    else:
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=30)).isoformat()

    docs = load_range(start_date, end_date, regions, types, kw, company)
    print(f"[query_results] {start_date} ~ {end_date} 命中 {len(docs)} 条")
    if not docs:
        print("[query_results] 无数据")
        return

    df = build_df(docs)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUTPUT_BASE, f"{stamp}_{start_date}_{end_date}")
    os.makedirs(out_dir, exist_ok=True)

    xlsx = os.path.join(out_dir, "查询结果.xlsx")
    df.to_excel(xlsx, index=False)
    print(f"[query_results] Excel 已生成: {xlsx}")

    md = build_md(df, start_date, end_date)
    md_path = os.path.join(out_dir, "查询结果.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[query_results] MD 已生成: {md_path}", flush=True)


if __name__ == "__main__":
    main()
