"""个人业务机会筛选器（规则引擎 + 可选 LLM 精读）

读取 config/opportunities.json 配置，从当日商机中筛选与业务方向相关的机会：
- 规则引擎：标题命中「排除关键词」即剔除；标题命中「核心关键词」→ 直接相关；
  标题命中「次要关键词」→ 相关；仅正文命中「核心关键词」→ 意向线索。
- LLM 精读（可选）：配置中 LLM精读.启用=true 时，对规则引擎筛出的机会调用大模型
  精读，识别隐性机会并输出深度洞察。未启用时仅用规则引擎（免费、稳定）。

用法：
    python analyze_opportunities.py                # 分析今天
    python analyze_opportunities.py 2026-09-21     # 分析指定日期

数据来源：output/date_<date>.xlsx（由 fast_run.py 汇总生成）
输出：output/机会清单_<date>.xlsx + output/机会分析_<date>.md
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

import pandas as pd

from analyze_today import load_and_featurize, classify_type, region_name, extract_money

OUTPUT_DIR = "output"
CN_TZ = timezone(timedelta(hours=8))
CONFIG_PATH = "config/opportunities.json"

# 相关度三档
TIER_DIRECT = "直接相关"
TIER_RELATED = "相关"
TIER_LEAD = "意向线索"


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"[opportunities] 找不到配置文件 {CONFIG_PATH}，跳过机会分析")
        return None
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def match_keywords(title, core, sec, excl):
    """标题匹配：命中排除词返回 (None, [])；否则返回 (相关度, 匹配词列表)。"""
    title = str(title)
    excl_hits = [k for k in excl if k in title]
    if excl_hits:
        return None, excl_hits
    core_hits = [k for k in core if k in title]
    if core_hits:
        return TIER_DIRECT, core_hits
    sec_hits = [k for k in sec if k in title]
    if sec_hits:
        return TIER_RELATED, sec_hits
    return None, []


def body_core_leads(title, html, core, excl, lead_limit=3):
    """意向线索：标题未命中关键词，但正文反复命中核心词且无强排除噪声。"""
    h = str(html)[:4000]
    excl_cnt = sum(h.count(k) for k in excl)
    if excl_cnt >= 2:
        return None, []
    core_hits = [k for k in core if k in h]
    if len(core_hits) >= lead_limit:
        return TIER_LEAD, core_hits
    return None, []


def screen(df, cfg):
    """规则引擎筛选，返回标注相关度/匹配词/金额的 DataFrame 子集。"""
    core = cfg.get("核心关键词", [])
    sec = cfg.get("次要关键词", [])
    excl = cfg.get("排除关键词", [])
    rows = []
    for _, r in df.iterrows():
        title = str(r.get("title", ""))
        html = str(r.get("html", ""))
        tier, hits = match_keywords(title, core, sec, excl)
        if tier is None and hits:  # 命中排除词
            continue
        if tier is None:
            tier, hits = body_core_leads(title, html, core, excl)
        if tier is None:
            continue
        rows.append({
            "地区": region_name(r.get("region", "")),
            "公告链接": r.get("href", ""),
            "商机标题": title,
            "公告类型": r.get("type", classify_type(title)),
            "相关度": tier,
            "匹配关键词": "、".join(hits),
            "金额(万元)": r.get("amount_wan", None),
            "发布日期": r.get("release_date", ""),
            "商机详情": html,
            "详情截断": r.get("truncated", ""),
        })
    if not rows:
        return pd.DataFrame(columns=["地区", "公告链接", "商机标题", "公告类型",
                                     "相关度", "匹配关键词", "金额(万元)", "发布日期",
                                     "商机详情", "详情截断"])
    return pd.DataFrame(rows)


def _load_env():
    """读取项目根 .env 文件为 dict；同名环境变量（os.environ）优先。"""
    env = {}
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            pass
    # 进程环境变量优先
    for k in list(env):
        if k in os.environ and os.environ[k].strip():
            env[k] = os.environ[k].strip()
    return env


def load_llm_config():
    """LLM 精读配置统一从 .env 读取：LLM_ENABLED / LLM_API_BASE / LLM_API_KEY / LLM_MODEL / LLM_LIMIT。"""
    env = _load_env()

    def _bool(v, default=False):
        return str(v).strip().lower() in ("1", "true", "yes", "on") if v not in (None, "") else default

    return {
        "启用": _bool(env.get("LLM_ENABLED"), False),
        "api_base": env.get("LLM_API_BASE", "").rstrip("/"),
        "api_key": env.get("LLM_API_KEY", ""),
        "model": env.get("LLM_MODEL", ""),
        "limit": int(float(env.get("LLM_LIMIT", 15) or 15)),
    }


def llm_deep_read(df, date_key):
    """对规则引擎筛出的机会调用大模型精读，返回洞察文本。失败时返回 None 降级。"""
    llm = load_llm_config()
    api_key = llm["api_key"]
    if not llm["启用"] or not api_key:
        print("[opportunities] LLM 精读未启用或未配置 api_key，跳过（可在 .env 中配置 LLM_ENABLED/LLM_API_KEY）")
        return None
    limit = llm["limit"]
    # 优先精读直接相关 + 金额大的
    order = {TIER_DIRECT: 0, TIER_RELATED: 1, TIER_LEAD: 2}
    sub = df.assign(_o=df["相关度"].map(order)).sort_values(
        ["_o", "金额(万元)"], ascending=[True, False], na_position="last").head(limit)
    items = []
    for _, r in sub.iterrows():
        items.append(f"- [{r['地区']}][{r['相关度']}] {r['商机标题']} "
                     f"(金额{r['金额(万元)'] if pd.notna(r['金额(万元)']) else '未披露'}万元, 链接{r['公告链接']})")
    prompt = (
        f"你是服务器/IT/AI 方向的销售顾问。以下是 {date_key} 全国政府采购平台中按关键词筛出的候选商机，"
        f"请从中识别：1) 最值得跟进的 3-5 个高价值机会及理由（客户是谁、预算量级、切入建议）；"
        f"2) 哪些是噪声或低价值；3) 对整体商机的一句话判断。\n\n" + "\n".join(items)
    )
    url = (llm["api_base"] or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": llm["model"] or "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[opportunities] LLM 精读失败，降级为纯规则引擎: {e}")
        return None


def build_report(cfg, df, date_key, llm_text):
    """生成 Markdown 机会分析报告。"""
    n = len(df)
    biz = cfg.get("业务名称", "业务")
    tiers = [TIER_DIRECT, TIER_RELATED, TIER_LEAD]
    counts = {t: int((df["相关度"] == t).sum()) for t in tiers}

    def table(sub):
        if sub.empty:
            return "_（无）_"
        rows = []
        for _, r in sub.iterrows():
            amt = r["金额(万元)"]
            amt_s = f"{amt:,.0f}" if pd.notna(amt) else "未披露"
            rows.append(f"| {r['地区']} | {str(r['商机标题'])[:50]} | {r['公告类型']} | {r['匹配关键词']} | {amt_s} |")
        return "| 地区 | 商机标题 | 类型 | 匹配关键词 | 金额(万元) |\n|---|---|---|---|---|\n" + "\n".join(rows)

    region_top = df["地区"].value_counts().head(5)
    region_str = "、".join(f"{k}（{v}）" for k, v in region_top.items())

    # 大金额机会
    amt_df = df.dropna(subset=["金额(万元)"])
    big = amt_df.nlargest(5, "金额(万元)") if not amt_df.empty else pd.DataFrame()

    llm_section = ""
    if llm_text:
        llm_section = f"""
## 5. LLM 深度洞察

{llm_text}
"""
    else:
        llm_section = """
## 5. LLM 深度洞察

未启用（`.env` 中 `LLM_ENABLED=true` 且配置 `LLM_API_KEY` 后生效）。当前仅使用规则引擎。
"""

    report = f"""# {biz} 商机机会分析（{date_key}）

## 1. 摘要

基于规则引擎（核心/次要/排除关键词打分）从当日 **{len(df)} 条命中商机** 中筛选。**筛选口径：标题命中排除关键词即剔除；标题命中核心关键词→直接相关；标题命中次要关键词→相关；仅正文反复命中核心词→意向线索。**

- 直接相关（标题含核心关键词，如 服务器/算力/AI/信创/数据中心）：**{counts[TIER_DIRECT]} 条**
- 相关（标题含次要关键词，如 信息化/系统集成/运维）：**{counts[TIER_RELATED]} 条**
- 意向线索（正文含核心关键词，标题无关键词）：**{counts[TIER_LEAD]} 条**

机会集中地区：{region_str}。

> 说明：仅正文命中（意向线索）可能存在噪声，请以标题命中为主重点跟进。

## 2. 直接相关机会（重点跟进）

{table(df[df["相关度"] == TIER_DIRECT])}

## 3. 相关机会

{table(df[df["相关度"] == TIER_RELATED])}

## 4. 意向线索

{table(df[df["相关度"] == TIER_LEAD])}

{llm_section}
## 6. 大金额机会 TOP5

{('| 地区 | 商机标题 | 金额(万元) | 相关度 |\n|---|---|---|---|\n' + '\n'.join(f"| {r['地区']} | {str(r['商机标题'])[:50]} | {r['金额(万元)']:,.0f} | {r['相关度']} |" for _, r in big.iterrows())) if not big.empty else "_（无披露金额项目）_"}
"""
    return report


def run_opportunities(date_key=None):
    """主入口：加载数据 → 规则引擎筛选 → 输出 Excel + MD（可选 LLM 精读）。"""
    if not date_key:
        date_key = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    cfg = load_config()
    if cfg is None:
        return None
    df = load_and_featurize(date_key)
    if df is None or df.empty:
        print(f"[opportunities] {date_key} 无数据，跳过机会分析")
        return None
    hit = screen(df, cfg)
    print(f"[opportunities] 规则引擎筛出 {len(hit)} 条机会 "
          f"(直接相关 {(hit['相关度'] == TIER_DIRECT).sum()} / "
          f"相关 {(hit['相关度'] == TIER_RELATED).sum()} / "
          f"意向线索 {(hit['相关度'] == TIER_LEAD).sum()})")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    xlsx = os.path.join(OUTPUT_DIR, f"机会清单_{date_key}.xlsx")
    hit.to_excel(xlsx, index=False)
    print(f"[opportunities] 机会清单已生成: {xlsx}")

    llm_text = llm_deep_read(hit, date_key)
    report = build_report(cfg, hit, date_key, llm_text)
    md = os.path.join(OUTPUT_DIR, f"机会分析_{date_key}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[opportunities] 机会分析报告已生成: {md}")
    return hit


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_opportunities(arg)
