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
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd

from analyze_today import load_and_featurize, classify_type, region_name, extract_money, date_dir

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
            "投标截止": r.get("投标截止", None),
            "开标时间": r.get("开标时间", None),
            "获取文件截止": r.get("获取文件截止", None),
            "距投标截止(天)": r.get("距投标截止天数", None),
            "商机详情": html,
            "详情截断": r.get("truncated", ""),
        })
    if not rows:
        return pd.DataFrame(columns=["地区", "公告链接", "商机标题", "公告类型",
                                     "相关度", "匹配关键词", "金额(万元)", "发布日期",
                                     "投标截止", "开标时间", "获取文件截止",
                                     "距投标截止(天)", "商机详情", "详情截断"])
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
    """LLM 精读配置统一从 .env 读取。

    deerflow 模式（默认）：LLM_ENABLED / DEERFLOW_CLI / DEERFLOW_GATEWAY / DEERFLOW_TIMEOUT，
    经 deerflowcli 执行（模型可 web_fetch 打开链接核实详情），不再需要 API_BASE/KEY/MODEL。
    LLM_LIMIT 为「兜底上限」（默认 15，设 0 表示不限）：实际精读条目由 select_for_llm
    自动挑选——直接相关全部精读 + 其余按金额优先补足，无需手动设置具体条数。
    """
    env = _load_env()

    def _bool(v, default=False):
        return str(v).strip().lower() in ("1", "true", "yes", "on") if v not in (None, "") else default

    try:
        limit = int(float(env.get("LLM_LIMIT", 15) or 15))
    except (TypeError, ValueError):
        limit = 15
    try:
        detail_len = int(float(env.get("LLM_DETAIL_LEN", 400) or 400))
    except (TypeError, ValueError):
        detail_len = 400
    try:
        timeout = int(float(env.get("DEERFLOW_TIMEOUT", 3600) or 3600))
    except (TypeError, ValueError):
        timeout = 3600
    try:
        recursion_limit = int(float(env.get("DEERFLOW_RECURSION_LIMIT", 300) or 300))
    except (TypeError, ValueError):
        recursion_limit = 300
    return {
        "启用": _bool(env.get("LLM_ENABLED"), False),
        "engine": env.get("LLM_ENGINE", "deerflow").strip().lower() or "deerflow",
        "cli": env.get("DEERFLOW_CLI", "").strip(),
        "gateway": env.get("DEERFLOW_GATEWAY", "").strip() or "http://127.0.0.1:8001",
        "timeout": timeout,
        "recursion_limit": recursion_limit,
        "api_base": env.get("LLM_API_BASE", "").rstrip("/"),
        "api_key": env.get("LLM_API_KEY", ""),
        "model": env.get("LLM_MODEL", ""),
        "limit": limit,
        "detail_len": detail_len,
    }


def select_for_llm(df, limit):
    """自动挑选精读条目：直接相关全部 + 其余按「有金额优先、金额降序」补足到 limit。

    limit<=0 表示不限（全部精读，一般不推荐，token 成本高）。
    limit 严格兜底：直接相关数量超过 limit 时，直接相关也按金额优先截断到 limit
    （deerflow 逐条 web_fetch 核实，超长清单会导致任务无法完成）。
    """
    if limit and limit > 0:
        direct = df[df["相关度"] == TIER_DIRECT]
        if len(direct) >= limit:
            d = direct.assign(_has_amt=direct["金额(万元)"].notna()).sort_values(
                ["_has_amt", "金额(万元)"], ascending=[False, False], na_position="last")
            return d.head(limit).reset_index(drop=True)
        rest = df[df["相关度"] != TIER_DIRECT].copy()
        rest = rest.assign(_has_amt=rest["金额(万元)"].notna()).sort_values(
            ["_has_amt", "金额(万元)"], ascending=[False, False], na_position="last")
        return pd.concat([direct, rest.head(limit - len(direct))], ignore_index=True)
    return df.reset_index(drop=True)


def _items_hash(items):
    """输入清单 hash：对条目排序后取 md5，用于判断精读输入是否变化。"""
    import hashlib
    return hashlib.md5("\n".join(sorted(items)).encode("utf-8")).hexdigest()


def validate_llm_output(llm_text, sub):
    """校验 LLM 精读结果对清单编号的引用（方案1+3）。

    - 方案1：提取 LLM 文本中所有 [N] 引用，检查是否在 1~len(sub) 内，
      非法编号列出警告（防幻觉）。
    - 方案3：把被引用的合法编号映射回清单真实项目（地区/标题/金额/投标截止），
      供人工对照模型说法与真实数据是否一致。
    返回 dict：{'invalid': [非法编号], 'refs': {编号: {真实项目信息}}}；无引用返回 None。
    """
    if not llm_text or sub is None or sub.empty:
        return None
    refs = set()
    # 匹配独立的 [N] 商机引用（后不跟 ASCII 字母数字，排除"项目编号：[230201]JHZB"类误报；
    # 用显式 ASCII 类而非 \w——Python \w 默认含中文，会漏掉"[31]车辆管理所"这类后接中文的引用）
    for m in re.finditer(r"\[(\d+)\](?![0-9A-Za-z_])", llm_text):
        refs.add(int(m.group(1)))
    if not refs:
        return None
    valid = set(range(1, len(sub) + 1))
    invalid = sorted(refs - valid)
    ref_map = {}
    for n in sorted(refs & valid):
        r = sub.iloc[n - 1]
        amt = r.get("金额(万元)")
        ref_map[n] = {
            "地区": r.get("地区", ""),
            "标题": str(r.get("商机标题", ""))[:55],
            "金额": f"{amt:,.0f}万" if pd.notna(amt) and amt else "未披露",
            "投标截止": str(r.get("投标截止", ""))[:16] if pd.notna(r.get("投标截止")) and r.get("投标截止") else "未披露",
            "相关度": r.get("相关度", ""),
        }
    return {"invalid": invalid, "refs": ref_map}


def build_llm_validate_section(v):
    """把校验结果渲染成 Markdown 片段（附在 LLM 洞察章节后）。"""
    if not v:
        return ""
    lines = []
    if v["invalid"]:
        lines.append(f"> ⚠️ **编号校验：模型引用了清单中不存在的编号 {v['invalid']}（清单共 "
                     f"{len(v['refs']) + len(v['invalid'])} 条内仅 {len(v['refs'])} 条合法），"
                     f"对应信息请警惕，勿直接采信。**")
    lines.append("**编号 → 清单真实项目对照（人工核对模型说法是否属实）：**")
    lines.append("")
    lines.append("| 编号 | 清单真实项目 | 地区 | 真实金额 | 真实投标截止 |")
    lines.append("|---|---|---|---|---|")
    for n, info in v["refs"].items():
        lines.append(f"| [{n}] | {info['标题']} | {info['地区']} | {info['金额']} | {info['投标截止']} |")
    if v["invalid"]:
        lines.append("")
        lines.append("> 注：上表仅列出合法编号对应的真实项目；模型若引用非法编号，无法对应到清单任何项目。")
    return "\n".join(lines)


def _extract_thread_id(text):
    """从 deerflowcli 输出解析会话 ID（新建「会话 ID:」/复用「复用会话:」）。"""
    m = re.search(r"(?:会话 ID|复用会话)[:：]\s*([0-9a-fA-F-]{36})", text or "")
    return m.group(1) if m else None


def _locate_deerflow_cli(llm):
    """定位 deerflowcli：优先 .env 配置，其次 PATH，最后探测常见安装位置。

    受限终端 PATH 不含 ~/.local/bin 等目录，仅靠 shutil.which 会找不到
    （导致 LLM 精读自动降级），此处补探测常见路径兜底。
    """
    cli = (llm.get("cli") or "").strip() or shutil.which("deerflowcli")
    if cli:
        return cli
    for cand in (
        os.path.expanduser("~/.local/bin/deerflowcli"),
        os.path.expanduser("~/bin/deerflowcli"),
        "/usr/local/bin/deerflowcli",
        "/opt/homebrew/bin/deerflowcli",
    ):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return "deerflowcli"


def _run_deerflow(task, out_dir, fname, llm, retries=1):
    """通过 deerflowcli 执行精读任务，返回模型产出的报告文本；失败返回 None。

    模型在沙箱内按提示词写 /mnt/user-data/outputs/<fname> 并 present_files，
    deerflowcli 会把产出下载到 out_dir/<thread_id>/ 下，此处扫描读取（精确文件名优先，
    找不到则取最新的「商机分析*.md」，容错模型改名）。

    方案A+B：提交任务带 --recursion-limit（长任务避免 LangGraph 递归超限）；
    首次失败未产出文件时，从输出解析 thread-id，下一次自动用 --thread-id 恢复会话
    （让模型基于沙箱已写文件继续补齐并交付），而非新建会话重头跑。
    """
    cli = _locate_deerflow_cli(llm)
    os.makedirs(out_dir, exist_ok=True)
    timeout = llm.get("timeout", 3600)
    recursion_limit = llm.get("recursion_limit")
    thread_id = None
    for attempt in range(retries + 1):
        cmd = [cli, "--output-dir", out_dir, "--timeout", str(timeout), "--no-stream"]
        if recursion_limit:
            cmd += ["--recursion-limit", str(recursion_limit)]
        gateway = llm.get("gateway") or ""
        if gateway and gateway != "http://127.0.0.1:8001":
            cmd += ["--gateway", gateway]
        if thread_id:
            cmd += ["--thread-id", thread_id, "继续"]
            print(f"[deerflow] 恢复会话 {thread_id[:12]}…（第 {attempt + 1} 次，基于沙箱已写文件补齐）")
        else:
            cmd.append(task)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 60)
        except Exception as e:
            print(f"[deerflow] 执行失败: {e}")
            return None
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-300:]
            print(f"[deerflow] deerflowcli 退出码 {proc.returncode}: {tail}")
            return None
        hits = []
        for root, _, files in os.walk(out_dir):
            for fn in files:
                if fn == fname or (fn.endswith(".md") and "商机分析" in fn):
                    hits.append(os.path.join(root, fn))
        if hits:
            p = max(hits, key=os.path.getmtime)
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
        out = proc.stdout or ""
        if not thread_id:
            thread_id = _extract_thread_id(out)
        tail = out.strip()[-600:]
        if attempt < retries:
            how = f"，恢复会话 {thread_id[:12]} 重试" if thread_id else "，重试中"
            print(f"[deerflow] 第 {attempt + 1} 次未产出报告文件（期望 {fname}）{how}。输出：{tail}")
        else:
            print(f"[deerflow] 未找到产出报告文件（期望 {fname}），deerflowcli 输出：{tail}")
    return None


def llm_deep_read(cfg, df, date_key, refresh=False):
    """对规则引擎筛出的机会调用大模型精读（deerflow cli 执行），返回洞察文本。失败时返回 None 降级。

    - 精读结果缓存在 ES llm_reads 索引：key = daily_<date>。
    - 命中且 input_hash 一致 → 直接复用缓存，不调 deerflow。
    - 数据变化（hash 不一致）或 refresh=True → 重新精读并更新缓存。

    提示词模板从 config/opportunities.json 的 LLM提示词 读取（可自由调整），
    占位符 {date_key} / {items} 由本函数填充。模型经 deerflowcli 执行，
    可 web_fetch 打开链接核实详情，最终报告写入沙箱 /mnt/user-data/outputs/ 并由
    deerflowcli 下载到本地 output/<date>/deerflow/，此处读取作为精读结果。
    """
    llm = load_llm_config()
    if not llm["启用"]:
        print("[opportunities] LLM 精读未启用（.env 中 LLM_ENABLED=true 后生效），跳过")
        return None
    # 无相关机会时不精读，避免拿空清单白跑
    if df is None or df.empty:
        print("[opportunities] 无相关机会，跳过 LLM 精读")
        return None
    # 自动挑选精读条目：直接相关全精读 + 其余按金额优先补足，LLM_LIMIT 仅作兜底
    sub = select_for_llm(df, llm["limit"])
    print(f"[opportunities] LLM 精读 {len(sub)} 条 "
          f"(直接相关 {(sub['相关度'] == TIER_DIRECT).sum()} / "
          f"其余 {(sub['相关度'] != TIER_DIRECT).sum()})")
    detail_len = llm.get("detail_len", 400)
    items = []
    for idx, (_, r) in enumerate(sub.iterrows(), start=1):
        amt = r["金额(万元)"]
        amt_s = f"{amt:,.0f}" if pd.notna(amt) else "未披露"
        bid = r.get("投标截止")
        bid_s = str(bid) if pd.notna(bid) and bid else "未披露"
        open_s = r.get("开标时间")
        open_s = str(open_s) if pd.notna(open_s) and open_s else "未披露"
        detail = str(r.get("商机详情", "") or "").replace("\n", " ").strip()
        detail_s = detail[:detail_len] if detail else "（正文未获取）"
        items.append(
            f"- [{idx}][{r['地区']}][{r['相关度']}] {r['商机标题']}\n"
            f"  金额:{amt_s}万元 | 投标截止:{bid_s} | 开标:{open_s}\n"
            f"  正文摘要:{detail_s}\n"
            f"  链接:{r['公告链接']}")
    input_hash = _items_hash(items)

    # 缓存：命中且 hash 一致则复用
    from utils.es import ESConnection
    es = ESConnection()
    cache_key = f"daily_{date_key}"
    if not refresh:
        cached = es.get_llm_read(cache_key)
        if cached and cached.get("input_hash") == input_hash:
            print(f"[opportunities] LLM 精读命中缓存（{cache_key}），复用 {cached.get('updated_at')}")
            return cached.get("result")
        if cached:
            print(f"[opportunities] 数据有变化（{cache_key}），重新精读")

    prompts = (cfg.get("LLM提示词") or {})
    system = (prompts.get("system") or "").strip()
    user_tpl = prompts.get("user") or "以下是 {date_key} 的候选商机，请识别高价值机会。\n\n{items}"
    user_content = user_tpl.format(date_key=date_key, items="\n".join(items))
    task = f"{system}\n\n{user_content}" if system else user_content

    # deerflowcli 执行：模型写 商机分析_<date>.md 到沙箱并 present_files，
    # 产出下载到本地 output/<date>/deerflow/，读取作为精读结果
    out_dir = os.path.join(date_dir(date_key), "deerflow")
    fname = f"商机分析_{date_key}.md"
    result = _run_deerflow(task, out_dir, fname, llm)
    if result:
        # 写缓存
        es.save_llm_read(cache_key, f"每日机会分析 {date_key}", input_hash,
                         f"deerflow/{llm.get('cli') or 'deerflowcli'}", result)
        return result
    print("[opportunities] deerflow 精读失败，降级为纯规则引擎")
    return None


def build_report(cfg, df, date_key, llm_text, sub=None):
    """生成 Markdown 机会分析报告。

    sub 为本次精读挑选的清单（含编号顺序），用于在 LLM 洞察后附编号校验与对照表。
    """
    n = len(df)
    biz = cfg.get("业务名称", "业务")
    tiers = [TIER_DIRECT, TIER_RELATED, TIER_LEAD]
    counts = {t: int((df["相关度"] == t).sum()) for t in tiers}

    region_top = df["地区"].value_counts().head(5)
    region_str = "、".join(f"{k}（{v}）" for k, v in region_top.items())

    # 大金额机会
    amt_df = df.dropna(subset=["金额(万元)"])
    big = amt_df.nlargest(5, "金额(万元)") if not amt_df.empty else pd.DataFrame()

    # ---- 时间节点与紧迫度 ----
    def urgency(days):
        if days is None or pd.isna(days):
            return ""
        if days < 0:
            return "⛔已过期"
        if days <= 7:
            return "🔴立刻处理"
        if days <= 14:
            return "🟡抓紧准备"
        return "🟢从容跟进"

    def table(sub):
        """分档机会表：带投标截止与紧迫度，方便直接识别先跟谁。"""
        if sub.empty:
            return "_（无）_"
        rows = []
        for _, r in sub.iterrows():
            amt = r["金额(万元)"]
            amt_s = f"{amt:,.0f}" if pd.notna(amt) else "未披露"
            bid = r.get("投标截止")
            bid_s = str(bid) if pd.notna(bid) and bid else "—"
            d = r.get("距投标截止(天)") if "距投标截止(天)" in sub.columns else r.get("距投标截止天数")
            ur = urgency(d) or "—"
            rows.append(f"| {r['地区']} | {str(r['商机标题'])[:46]} | {bid_s} | {ur} | {amt_s} |")
        return ("| 地区 | 商机标题 | 投标截止 | 紧迫度 | 金额(万元) |\n"
                "|---|---|---|---|---|\n" + "\n".join(rows))

    def tl_table(sub):
        if sub.empty:
            return "_（无）_"
        rows = []
        for _, r in sub.iterrows():
            d = r.get("距投标截止(天)") if "距投标截止(天)" in sub.columns else r.get("距投标截止天数")
            bid = r.get("投标截止")
            bid_s = str(bid) if pd.notna(bid) and bid else "—"
            open_s = str(r.get("开标时间")) if pd.notna(r.get("开标时间")) and r.get("开标时间") else "—"
            ur = urgency(d) or "—"
            rows.append(f"| {r['地区']} | {str(r['商机标题'])[:45]} | {bid_s} | {ur} | {open_s} |")
        head = "| 地区 | 商机标题 | 投标截止 | 紧迫度 | 开标时间 |\n|---|---|---|---|---|\n"
        return head + "\n".join(rows)

    tl_df = df.dropna(subset=["投标截止"]).copy()
    tl_df["距投标截止(天)"] = pd.to_numeric(
        tl_df.get("距投标截止(天)", tl_df.get("距投标截止天数")), errors="coerce")
    urgent = tl_df[tl_df["距投标截止(天)"].apply(lambda v: pd.notna(v) and 0 <= v <= 14)] \
        .sort_values("距投标截止(天)")
    urgent_count = len(urgent)
    urgent_block = ""
    if urgent_count:
        rows = []
        for _, r in urgent.head(10).iterrows():
            d = int(r["距投标截止(天)"])
            rows.append(f"- {urgency(d)} **{str(r['商机标题'])[:50]}**（{r['地区']}，"
                        f"投标截止 {r['投标截止']}，剩 {d} 天）")
        urgent_block = "**14 天内需行动的机会（先跟这些）：**\n\n" + "\n".join(rows)

    llm_section = ""
    if llm_text:
        validate_section = ""
        if sub is not None and not sub.empty:
            validate_section = "\n\n" + build_llm_validate_section(
                validate_llm_output(llm_text, sub))
        llm_section = f"""
## 5. LLM 深度洞察

{llm_text}
{validate_section}
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

{urgent_block}

> 说明：仅正文命中（意向线索）可能存在噪声，请以标题命中为主重点跟进。

## 2. 直接相关机会（重点跟进）

{table(df[df["相关度"] == TIER_DIRECT])}

## 3. 相关机会

{table(df[df["相关度"] == TIER_RELATED])}

## 4. 意向线索

{table(df[df["相关度"] == TIER_LEAD])}

{llm_section}
## 5. 关键时间节点（投标截止 / 开标）

披露了投标截止时间的 **{len(tl_df)} 条**机会中，14 天内需行动 {urgent_count} 条。紧迫度：🔴=7天内截止（立刻处理）、🟡=14天内（抓紧准备）、🟢=14天以上（从容跟进）、⛔=已过期。

### 5.1 直接相关机会时间节点

{tl_table(df[(df["相关度"] == TIER_DIRECT)])}

### 5.2 相关机会时间节点

{tl_table(df[df["相关度"] == TIER_RELATED])}

## 6. 大金额机会 TOP5

{('| 地区 | 商机标题 | 金额(万元) | 相关度 |\n|---|---|---|---|\n' + '\n'.join(f"| {r['地区']} | {str(r['商机标题'])[:50]} | {r['金额(万元)']:,.0f} | {r['相关度']} |" for _, r in big.iterrows())) if not big.empty else "_（无披露金额项目）_"}
"""
    return report


def run_opportunities(date_key=None, refresh=False):
    """主入口：加载数据 → 规则引擎筛选 → 输出 Excel + MD（可选 LLM 精读）。

    refresh=True 时强制重新精读并更新缓存（默认命中缓存则复用）。
    """
    if not date_key:
        date_key = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    cfg = load_config()
    if cfg is None:
        return None
    df = load_and_featurize(date_key)
    if df is None or df.empty:
        print(f"[opportunities] {date_key} 无数据，跳过机会分析")
        return None
    # 距投标截止天数（以投标截止日期为准，未披露则 None）
    if "投标截止" in df.columns:
        from datetime import date as _date
        _today = _date.today()
        df["距投标截止天数"] = df["投标截止"].apply(
            lambda v: (_date.fromisoformat(str(v)[:10]) - _today).days
            if pd.notna(v) and str(v)[:10] else None)
    hit = screen(df, cfg)
    print(f"[opportunities] 规则引擎筛出 {len(hit)} 条机会 "
          f"(直接相关 {(hit['相关度'] == TIER_DIRECT).sum()} / "
          f"相关 {(hit['相关度'] == TIER_RELATED).sum()} / "
          f"意向线索 {(hit['相关度'] == TIER_LEAD).sum()})")
    os.makedirs(date_dir(date_key), exist_ok=True)

    xlsx = os.path.join(date_dir(date_key), f"机会清单_{date_key}.xlsx")
    hit.to_excel(xlsx, index=False)
    print(f"[opportunities] 机会清单已生成: {xlsx}")

    llm_text = llm_deep_read(cfg, hit, date_key, refresh=refresh)
    sub = select_for_llm(hit, load_llm_config()["limit"]) if llm_text else None
    report = build_report(cfg, hit, date_key, llm_text, sub=sub)
    md = os.path.join(date_dir(date_key), f"机会分析_{date_key}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[opportunities] 机会分析报告已生成: {md}")
    return hit


if __name__ == "__main__":
    args = sys.argv[1:]
    date_arg = next((a for a in args if not a.startswith("--")), None)
    refresh = "--refresh" in args
    run_opportunities(date_arg, refresh=refresh)
