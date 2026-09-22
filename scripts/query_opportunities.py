"""窗口期商机查询与商机分析脚本

按「投标截止时间范围 + 业务关键词规则」从 ES 筛选相关项目，生成：
- 商机分析 MD（分级清单 + 金额/紧迫度 + LLM 精读洞察）
- 全部信息 Excel（含商机详情全文）

输出统一放到：output/商机整理/<查询时间_YYYYMMDD_HHMMSS>_<分析起>_<分析止>/

用法：
    python scripts/query_opportunities.py                    # 默认分析 10 月整月（10-01~10-31）
    python scripts/query_opportunities.py 2026-10-01 2026-10-31   # 自定义起止
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from elasticsearch import helpers

import analyze_opportunities as ao
from analyze_today import region_name, classify_type, extract_timeline
from utils.es import ESConnection

OUTPUT_BASE = os.path.join("output", "商机整理")
TIER_DIRECT = ao.TIER_DIRECT
TIER_RELATED = ao.TIER_RELATED
TIER_LEAD = ao.TIER_LEAD


def load_window_tenders(start_date, end_date):
    """从 ES 按 bid_deadline 范围拉取公告（结构化字段已在入库时提取）。"""
    es = ESConnection()
    client = es._client()
    query = {"query": {"range": {"bid_deadline": {"gte": start_date, "lte": end_date}}}, "sort": ["_doc"]}
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
        print(f"[query] ES 查询失败: {e}")
        return []
    return [h["_source"] for h in records]


def enrich_tender_docs(docs):
    """补充 html2text 纯文本、开标时间等字段（ES 未存的），并去重（同标题保留金额最大）。"""
    from crawler.base_crawler import BaseCrawler
    bc = BaseCrawler.__new__(BaseCrawler)
    for d in docs:
        html = str(d.get("html", "") or "")
        d["html_text"] = bc._html_to_text(html)
        d["truncated"] = "是" if len(d["html_text"]) > 32767 else "否"
        tl = extract_timeline(d["html_text"])
        d.setdefault("开标时间", tl.get("开标") or "")
        d.setdefault("获取文件截止", tl.get("获取文件截止") or "")
    # 按标题前缀去重（同公告多标段保留金额最大）
    seen = {}
    for d in docs:
        k = str(d.get("title", ""))[:45]
        if k not in seen or (d.get("amount_wan") or 0) > (seen[k].get("amount_wan") or 0):
            seen[k] = d
    return list(seen.values())


def screen_window(docs, cfg):
    """规则引擎筛选（复用 analyze_opportunities.match_keywords）。"""
    core = cfg.get("核心关键词", [])
    sec = cfg.get("次要关键词", [])
    excl = cfg.get("排除关键词", [])
    rows = []
    for d in docs:
        title = str(d.get("title", ""))
        tier, hits = ao.match_keywords(title, core, sec, excl)
        if tier is None:
            continue
        rows.append({
            "地区": region_name(d.get("region", "")),
            "公告链接": d.get("href", ""),
            "商机标题": title,
            "公告类型": classify_type(title),
            "相关度": tier,
            "匹配关键词": "、".join(hits),
            "金额(万元)": d.get("amount_wan"),
            "发布日期": d.get("release_date", ""),
            "投标截止": d.get("bid_deadline", ""),
            "开标时间": d.get("开标时间", ""),
            "获取文件截止": d.get("获取文件截止", ""),
            "商机详情": d.get("html_text", ""),
            "详情截断": d.get("truncated", ""),
        })
    return pd.DataFrame(rows)


def add_urgency(df):
    """按投标截止计算距今天数与紧迫度（🔴≤7天 / 🟡8-14天 / 🟢>14天 / ⛔过期）。"""
    today = date.today()
    days = []
    urgs = []
    for v in df["投标截止"]:
        if pd.isna(v) or not str(v)[:10]:
            days.append(None)
            urgs.append("")
            continue
        try:
            d = (datetime.fromisoformat(str(v)[:10]).date() - today).days
        except ValueError:
            d = None
        days.append(d)
        if d is None:
            urgs.append("")
        elif d < 0:
            urgs.append("⛔已过期")
        elif d <= 7:
            urgs.append("🔴立刻处理")
        elif d <= 14:
            urgs.append("🟡抓紧准备")
        else:
            urgs.append("🟢从容跟进")
    df["距投标截止(天)"] = days
    df["紧迫度"] = urgs
    return df


def llm_read(cfg, df, window_label, refresh=False):
    """对筛选结果调用 LLM 精读（复用 load_llm_config 与提示词模板）。

    与每日机会分析共用 llm_reads 缓存：key = window_<起>_<止>。
    命中且 input_hash 一致 → 复用；数据变化或 refresh=True → 重新精读并更新缓存。
    """
    llm = ao.load_llm_config()
    api_key = llm["api_key"]
    if not llm["启用"] or not api_key:
        print("[query] LLM 精读未启用或未配置 api_key，跳过")
        return None
    sub = ao.select_for_llm(df, llm["limit"])
    print(f"[query] LLM 精读 {len(sub)} 条")
    items = []
    for _, r in sub.iterrows():
        amt = r["金额(万元)"]
        amt_s = f"{amt:,.0f}" if pd.notna(amt) else "未披露"
        items.append(f"- [{r['地区']}][{r['相关度']}] {r['商机标题']} (金额{amt_s}万元, 投标截止{r['投标截止']}, 链接{r['公告链接']})")
    input_hash = ao._items_hash(items)

    # 缓存：命中且 hash 一致则复用
    from utils.es import ESConnection
    es = ESConnection()
    cache_key = f"window_{window_label.replace(' ~ ', '_')}"
    if not refresh:
        cached = es.get_llm_read(cache_key)
        if cached and cached.get("input_hash") == input_hash:
            print(f"[query] LLM 精读命中缓存（{cache_key}），复用 {cached.get('updated_at')}")
            return cached.get("result")
        if cached:
            print(f"[query] 数据有变化（{cache_key}），重新精读")

    prompts = cfg.get("LLM提示词") or {}
    system = (prompts.get("system") or "").strip()
    user_tpl = prompts.get("user") or "以下是 {date_key} 的候选商机，请识别高价值机会。\n\n{items}"
    user_content = user_tpl.format(date_key=window_label, items="\n".join(items))
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": user_content}]
    url = (llm["api_base"] or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": llm["model"] or "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        result = data["choices"][0]["message"]["content"].strip()
        es.save_llm_read(cache_key, f"窗口期商机分析 {window_label}", input_hash,
                         llm.get("model") or "gpt-4o-mini", result)
        return result
    except Exception as e:
        print(f"[query] LLM 精读失败，降级为纯规则引擎: {e}")
        return None


def build_md(cfg, df, window_label, llm_text):
    n = len(df)
    biz = cfg.get("业务名称", "业务")
    counts = {t: int((df["相关度"] == t).sum()) for t in [TIER_DIRECT, TIER_RELATED, TIER_LEAD]}

    def table(sub, limit=40):
        if sub.empty:
            return "_（无）_"
        lines = ["| 地区 | 商机标题 | 投标截止 | 紧迫度 | 金额(万) |", "|---|---|---|---|---|"]
        for _, r in sub.head(limit).iterrows():
            amt = r["金额(万元)"]
            amt_s = f"{amt:,.0f}" if pd.notna(amt) else "—"
            bid = str(r["投标截止"])[:16] if pd.notna(r["投标截止"]) and r["投标截止"] else "—"
            lines.append(f"| {r['地区']} | {str(r['商机标题'])[:44]} | {bid} | {r['紧迫度']} | {amt_s} |")
        if len(sub) > limit:
            lines.append(f"| ... | 另有 {len(sub) - limit} 条 | | | |")
        return "\n".join(lines)

    direct = df[df["相关度"] == TIER_DIRECT].sort_values("距投标截止(天)")
    related = df[df["相关度"] == TIER_RELATED].sort_values("距投标截止(天)")
    amt = df.dropna(subset=["金额(万元)"])
    top_amt = amt.nlargest(10, "金额(万元)") if not amt.empty else pd.DataFrame()
    urgent = df[df["紧迫度"].isin(["🔴立刻处理", "🟡抓紧准备"])].sort_values("距投标截止(天)")

    llm_section = ""
    if llm_text:
        llm_section = f"\n## 四、LLM 深度洞察\n\n{llm_text}\n"
    else:
        llm_section = "\n## 四、LLM 深度洞察\n\n未启用（`.env` 中 LLM_ENABLED=true 且配置 LLM_API_KEY 后生效）。\n"

    report = f"""# {biz} 商机分析：投标截止 {window_label}

## 一、摘要

窗口期内共 **{n} 条**与业务相关（直接相关 {counts[TIER_DIRECT]} / 相关 {counts[TIER_RELATED]} / 意向线索 {counts[TIER_LEAD]}）。
紧迫度口径：🔴=7天内截止（立刻处理）、🟡=8-14天（抓紧准备）、🟢=14天以上、⛔=已过期。
其中需立即行动（7 天内）：**{(df['紧迫度'] == '🔴立刻处理').sum()} 条**；抓紧准备（8-14 天）：**{(df['紧迫度'] == '🟡抓紧准备').sum()} 条**。

## 二、直接相关机会（按截止时间排序）

{table(direct)}

## 三、相关机会（按截止时间排序）

{table(related)}
{llm_section}
## 五、金额 TOP10

{('| 地区 | 商机标题 | 投标截止 | 金额(万) |\n|---|---|---|---|\n' + '\n'.join(
    f"| {r['地区']} | {str(r['商机标题'])[:44]} | {str(r['投标截止'])[:16]} | {r['金额(万元)']:,.0f} |"
    for _, r in top_amt.iterrows())) if not top_amt.empty else '_（无披露金额项目）_'}

## 六、判断建议

- **立刻处理（7 天内截止）**：以上清单中 🔴 项优先，标书未备的直接评估放弃。
- **有金额项目**：金额越明确越值得投入（见金额 TOP10）。
- **无金额但标题含 服务器/算力/机房/大模型**：电话向代理确认预算与资格门槛。
- 本窗口共 {len(urgent)} 条需在两周内行动，建议按「金额明确 > 核心关键词贴合」排序逐条跟进。

---
生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。清单按业务关键词筛出去重（同公告多标段保留金额最大者）。
"""
    return report


def main():
    args = sys.argv[1:]
    non_flag = [a for a in args if not a.startswith("--")]
    refresh = "--refresh" in args
    if len(non_flag) >= 2:
        start_date, end_date = non_flag[0], non_flag[1]
    else:
        start_date, end_date = "2026-10-01", "2026-10-31"
    window_label = f"{start_date} ~ {end_date}"

    cfg = ao.load_config()
    if cfg is None:
        print("[query] 找不到 config/opportunities.json")
        return

    # 输出目录：output/商机整理/<查询时间_YYYYMMDD_HHMMSS>_<分析起>_<分析止>/
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUTPUT_BASE, f"{stamp}_{start_date}_{end_date}")
    os.makedirs(out_dir, exist_ok=True)
    print(f"[query] 输出目录: {out_dir}")

    docs = load_window_tenders(start_date, end_date)
    print(f"[query] 窗口期({window_label}) 公告: {len(docs)} 条")
    if not docs:
        print("[query] 无数据")
        return
    docs = enrich_tender_docs(docs)
    df = screen_window(docs, cfg)
    if df.empty:
        print("[query] 无相关商机")
        return
    df = add_urgency(df)

    # Excel：全部信息（含商机详情全文）
    xlsx = os.path.join(out_dir, f"相关商机_{start_date}_{end_date}.xlsx")
    df.to_excel(xlsx, index=False)
    print(f"[query] Excel 已生成: {xlsx}")

    # MD：商机分析
    llm_text = llm_read(cfg, df, window_label, refresh=refresh)
    md = build_md(cfg, df, window_label, llm_text)
    md_path = os.path.join(out_dir, f"商机分析_{start_date}_{end_date}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[query] MD 已生成: {md_path}")


if __name__ == "__main__":
    main()
