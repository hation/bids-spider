# 按日期抓取招标公告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 bids-spider 新增"按日期抓取"能力——`today` / `by_date` 命令抓取指定日期的招标公告，支持原生日期参数站点精准回溯、其余站点扫描早停，并生成汇总 Excel 与摘要报告。

**Architecture:** BaseCrawler 新增 `run_by_date` 入口：原生站点（`date_filter='url_param'`）在列表请求中带日期参数；扫描站点复用现有 `_crawl`，通过拦截逐条入库的 `save_tender_to_es` 做日期守卫（超出范围不落 ES，遇到早于起始日期的公告抛 `_DateBoundaryReached` 提前停止），结束后按日期严格过滤 `self.tenders` 再批量入库与导出。fast_run.py 增加 `today` / `by_date` / `summarize` 命令与汇总输出。

**Tech Stack:** Python 3.12 / Playwright (headless) / Elasticsearch / pandas / loguru。

**验证方式说明：** 本项目无 pytest 测试基建，且验证依赖真实浏览器与线上站点，因此每个任务用"实现 → 运行验证命令 → 断言输出"的功能验证代替单测（对应设计文档第 7 节）。

参考设计文档：`docs/superpowers/specs/2026-09-18-by-date-crawling-design.md`

---

### Task 1: BaseCrawler 日期模式核心

**Files:**
- Modify: `crawler/base_crawler.py`

- [ ] **Step 1: 在模块常量区新增边界异常**

在 `EXCEL_CELL_LIMIT` 定义之后加入：

```python
class _DateBoundaryReached(Exception):
    """扫描模式下已翻过目标日期范围（列表按日期倒序），提前停止翻页。"""
```

- [ ] **Step 2: 修改 `save_tenders_to_excel` 支持确定性子文件名**

把方法内这两行：

```python
        file_name = str(datetime.now()).replace(' ', '_').replace('-', '_').replace(':', '_').replace('.', '_')
        file_name = f"{self.region}_{file_name}.xlsx"
```

改为：

```python
        file_name = getattr(self, '_excel_name', None) or (
            f"{self.region}_"
            f"{str(datetime.now()).replace(' ', '_').replace('-', '_').replace(':', '_').replace('.', '_')}.xlsx"
        )
```

- [ ] **Step 3: 在 `BaseCrawler` 类内新增日期模式方法**

在 `get_exists_url_from_es` 方法之后追加以下方法（保持缩进在类内）：

```python
    def run_by_date(self, start_date, end_date=None):
        """按日期抓取：只保留 release_date 落在 [start_date, end_date] 的公告。

        - 声明了 date_filter='url_param' 的站点：列表请求直接带日期参数，精准定位。
        - 未声明（扫描模式）：复用 _crawl 翻页，遇到早于 start_date 的公告提前停止。
        """
        self._date_start = start_date
        self._date_end = end_date or start_date
        self._date_no_date = 0
        self._excel_name = f"date_{start_date}_{self.region}.xlsx"
        self.exists_urls = self.get_exists_url_from_es()
        try:
            with Stealth().use_sync(sync_playwright()) as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-blink-features=AutomationControlled',
                    ]
                )
                context = browser.new_context()
                self._crawl_by_date(context)
        except _DateBoundaryReached:
            logger.info(f"[{self.region}]已翻过目标日期范围，提前停止翻页")
        finally:
            self._filter_tenders_by_date()
            self.save_tenders_to_es()
            self.save_tenders_to_excel()

    def _crawl_by_date(self, context):
        """日期模式入口：安装日期过滤守卫后复用各爬虫的 _crawl。"""
        if getattr(self, 'date_filter', None) == 'url_param' and hasattr(self, 'build_list_url'):
            # 原生日期参数站点：让列表请求直接带上日期范围（保留 pageNum=1 供 _crawl 分页替换）
            base_url = self.build_list_url(1)
            for attr in ('api_url', 'list_url'):
                if hasattr(self, attr):
                    setattr(self, attr, base_url)
        else:
            # 扫描模式：限制页数兜底，避免无谓翻页
            self.max_pages = min(getattr(self, 'max_pages', 50), 8)

        orig_save = self.save_tender_to_es

        def guarded_save(tender):
            d = (tender.release_date or '').strip()[:10]
            if not d:
                self._date_no_date += 1
                return
            if d < self._date_start:  # 列表按日期倒序，遇到更早的即翻过了目标日期
                raise _DateBoundaryReached()
            if d <= self._date_end:
                orig_save(tender)

        self.save_tender_to_es = guarded_save
        self._crawl(context)
        self.save_tender_to_es = orig_save

    def _filter_tenders_by_date(self):
        """严格过滤兜底：只保留日期范围内的公告（批量入库/Excel 导出前调用）。"""
        self.tenders = {
            href: t for href, t in self.tenders.items()
            if self._date_start <= (t.release_date or '').strip()[:10] <= self._date_end
        }
```

- [ ] **Step 4: 语法检查**

Run: `cd /Users/xingan/Documents/software/aiengine/bids-spider && .venv/bin/python -m py_compile crawler/base_crawler.py`
Expected: 退出码 0，无输出

- [ ] **Step 5: 提交**

```bash
git add crawler/base_crawler.py
git commit -m "feat(crawler): BaseCrawler 新增按日期抓取入口（run_by_date + 日期守卫 + 严格过滤）"
```

---

### Task 2: 内蒙古原生日期参数声明

**Files:**
- Modify: `crawler/neimenggu.py`

- [ ] **Step 1: 增加 date_filter 声明与 build_list_url**

在 `self.headers = {}` 之后加入类属性与实例属性，并在 `__init__` 的 `self.headers = {}` 下一行加：

```python
        self.date_filter = 'url_param'
```

在类内 `_crawl` 方法之前加入方法（在 `__init__` 之后）：

```python
    def build_list_url(self, page_num):
        """原生日期参数：searchPublishResource 接口支持 startTime/endTime 过滤。"""
        return (
            self.api_url.replace('pageNum=1', f'pageNum={page_num}')
            .replace('startTime=', f'startTime={self._date_start}')
            .replace('endTime=', f'endTime={self._date_end}')
        )
```

- [ ] **Step 2: 语法检查**

Run: `cd /Users/xingan/Documents/software/aiengine/bids-spider && .venv/bin/python -m py_compile crawler/neimenggu.py`
Expected: 退出码 0，无输出

- [ ] **Step 3: 提交**

```bash
git add crawler/neimenggu.py
git commit -m "feat(crawler): 内蒙古爬虫声明原生日期参数（startTime/endTime）"
```

---

### Task 3: fast_run 日期命令与汇总输出

**Files:**
- Modify: `fast_run.py`

- [ ] **Step 1: 更新头部 import**

把文件顶部：

```python
import random
import sys
import time

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from crawler.base_crawler import BaseCrawler
```

改为：

```python
import glob
import json
import os
import random
import sys
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from crawler.base_crawler import BaseCrawler, _DateBoundaryReached
```

- [ ] **Step 2: 新增日期模式运行与汇总函数**

在 `run_region` 函数之后追加：

```python
CN_TZ = timezone(timedelta(hours=8))  # 北京时间


def today_str():
    return datetime.now(CN_TZ).strftime('%Y-%m-%d')


def run_region_by_date(region, start_date, end_date, summary_path=None):
    """单地区按日期抓取；可追加一行结果到 summary_path（JSONL，供并行批跑汇总）。"""
    module_name, class_name = CRAWLERS[region]
    module = __import__(f'crawler.{module_name}', fromlist=[class_name])
    crawler = getattr(module, class_name)()
    kept = 0
    no_date = 0
    try:
        crawler.run_by_date(start_date, end_date)
        kept = len(crawler.tenders)
        no_date = getattr(crawler, '_date_no_date', 0)
        status = 'ok' if kept > 0 else ('no_match' if no_date == 0 else 'no_date')
    except Exception as e:
        status = f'error: {e}'
    record = {'region': region, 'status': status, 'kept': kept, 'no_date': no_date}
    if summary_path:
        with open(summary_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    print(f'[{region}] {status}, kept={kept}, no_date={no_date}')
    return record


def build_summary(start_date, end_date, records, merged_df):
    """生成摘要报告 output/summary_<date>.md"""
    range_key = start_date if start_date == end_date else f'{start_date}_{end_date}'
    lines = [
        f'# 按日期抓取摘要 {start_date} ~ {end_date}',
        '',
        f'共 {len(records)} 个地区参与，成功 {sum(1 for r in records if r["status"] == "ok")} 个。',
        '',
        '| 地区 | 状态 | 新增条数 |',
        '|---|---|---|',
    ]
    for r in records:
        lines.append(f'| {r["region"]} | {r["status"]} | {r["kept"]} |')
    lines.append('')
    lines.append('## 各条公告标题（截取）')
    if merged_df is not None and not merged_df.empty:
        for _, row in merged_df.iterrows():
            title = str(row['title'])[:60]
            lines.append(f'- [{row["region"]}] {title} ({row["release_date"]})')
    else:
        lines.append('- （无命中）')
    md_path = f'output/summary_{range_key}.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'summary saved: {md_path}')


def merge_region_excels(start_date, regions):
    """合并各地区 date_<start>_<region>.xlsx 为汇总 Excel"""
    dfs = []
    for region in regions:
        for path in glob.glob(f'output/date_{start_date}_{region}.xlsx'):
            dfs.append(pd.read_excel(path))
    if not dfs:
        return None
    return pd.concat(dfs, ignore_index=True)
```

- [ ] **Step 3: 新增日期模式入口函数**

在 `main` 函数之前追加：

```python
def run_date_mode(start_date, end_date=None, regions=None, summary_path=None):
    """顺序跑全部/指定地区按日期抓取，然后生成汇总 Excel + 摘要报告。"""
    end_date = end_date or start_date
    regions = regions or list(CRAWLERS)
    records = []
    for region in regions:
        records.append(run_region_by_date(region, start_date, end_date, summary_path))
    merged = merge_region_excels(start_date, regions)
    if merged is not None:
        range_key = start_date if start_date == end_date else f'{start_date}_{end_date}'
        merged.to_excel(f'output/date_{range_key}.xlsx', index=False)
        print(f'merged excel saved: output/date_{range_key}.xlsx')
    build_summary(start_date, end_date, records, merged)


def summarize_command(date_key, end_date=None):
    """读取 output/date_run_<date>.jsonl 与各地区 Excel，生成汇总输出。"""
    start_date = date_key
    if end_date is None:
        end_date = start_date
    jsonl_path = f'output/date_run_{start_date}.jsonl'
    records = []
    if os.path.exists(jsonl_path):
        with open(jsonl_path, encoding='utf-8') as f:
            for line in f:
                records.append(json.loads(line))
    if not records:
        print(f'no records in {jsonl_path}')
        return
    regions = [r['region'] for r in records]
    merged = merge_region_excels(start_date, regions)
    if merged is not None:
        range_key = start_date if start_date == end_date else f'{start_date}_{end_date}'
        merged.to_excel(f'output/date_{range_key}.xlsx', index=False)
    build_summary(start_date, end_date, records, merged)
```

- [ ] **Step 4: 重写 `main` 支持日期命令**

把现有 `main` 函数整体替换为：

```python
def main():
    args = sys.argv[1:]
    cmd = args[0] if args else 'tianjin'

    # 日期模式：today / by_date <start> [end] [--regions a,b] [--summary path]
    if cmd in ('today', 'by_date'):
        if cmd == 'today':
            start = today_str()
            end = None
            rest = args[1:]
        else:
            if len(args) < 2:
                print('用法: python fast_run.py by_date <YYYY-MM-DD> [YYYY-MM-DD] [--regions a,b] [--summary path]')
                sys.exit(1)
            start = args[1]
            end = args[2] if len(args) > 2 and not args[2].startswith('--') else None
            rest = args[2:] if end else args[1:]
        regions = None
        summary_path = None
        i = 0
        while i < len(rest):
            if rest[i] == '--regions' and i + 1 < len(rest):
                regions = [r.strip() for r in rest[i + 1].split(',') if r.strip()]
                i += 2
            elif rest[i] == '--summary' and i + 1 < len(rest):
                summary_path = rest[i + 1]
                i += 2
            else:
                i += 1
        run_date_mode(start, end, regions=regions, summary_path=summary_path)
        return

    if cmd == 'summarize':
        if len(args) < 2:
            print('用法: python fast_run.py summarize <YYYY-MM-DD> [end]')
            sys.exit(1)
        end = args[2] if len(args) > 2 else None
        summarize_command(args[1], end)
        return

    # 原有模式：all / <region>
    arg = args[0]
    if arg == 'all':
        for region in CRAWLERS:
            print(f'\n===== 开始爬取 {region} =====')
            try:
                run_region(region)
            except Exception as e:
                print(f'[ERROR] {region} 爬取失败: {e}')
                continue
    elif arg in CRAWLERS:
        run_region(arg)
    else:
        print(f'unknown region: {arg}')
        print('支持:', ', '.join(CRAWLERS))
        sys.exit(1)


if __name__ == '__main__':
    main()
```

- [ ] **Step 5: 语法检查**

Run: `cd /Users/xingan/Documents/software/aiengine/bids-spider && .venv/bin/python -m py_compile fast_run.py`
Expected: 退出码 0，无输出

- [ ] **Step 6: 提交**

```bash
git add fast_run.py
git commit -m "feat(fast_run): 新增 today/by_date/summarize 按日期抓取命令与汇总输出"
```

---

### Task 4: 集成验证 — 今日抓取（扫描模式）

**Files:** 无（运行验证）

- [ ] **Step 1: 单地区今日抓取**

Run: `cd /Users/xingan/Documents/software/aiengine/bids-spider && .venv/bin/python fast_run.py by_date 2026-09-18 --regions beijing 2>&1 | tail -5`
Expected: 输出 `[beijing] ok/no_match...`；若 ok，`output/date_2026-09-18_beijing.xlsx` 存在且 `release_date` 全部为 2026-09-18。

- [ ] **Step 2: 校验 ES 入库的日期**

Run: `cd /Users/xingan/Documents/software/aiengine/bids-spider && curl -s -u elastic:'7aNJbD0LTxsVLyuRcHSQ' -H 'Content-Type: application/json' -d '{"query":{"bool":{"must":[{"term":{"region":"beijing"}},{"bool":{"should":[{"range":{"release_date":{"lt":"2026-09-18"}}},{"range":{"release_date":{"gt":"2026-09-18 23:59:59"}}}]}}]}}}' "http://127.0.0.1:9200/tenders/_count"`
Expected: `"count":0`（日期模式新增的北京文档 release_date 全部落在 2026-09-18）。若返回非 0，检查这些文档是否为当日旧数据（crawl_date 早于本次运行），与本次运行无关。

- [ ] **Step 3: 提交（如有产物代码无关，无需提交）**

无代码改动，本任务验证通过即可继续。

---

### Task 5: 集成验证 — 区间回溯（扫描 + 原生）

**Files:** 无（运行验证）

- [ ] **Step 1: 扫描模式区间（天津，近几日）**

Run: `cd /Users/xingan/Documents/software/aiengine/bids-spider && .venv/bin/python fast_run.py by_date 2026-09-15 2026-09-18 --regions tianjin 2>&1 | tail -3`
Expected: 输出 `[tianjin] ok...`；`output/date_2026-09-15_2026-09-18_tianjin.xlsx` 内 release_date 均落在区间内。

- [ ] **Step 2: 原生日期参数（内蒙古单日）**

Run: `cd /Users/xingan/Documents/software/aiengine/bids-spider && .venv/bin/python fast_run.py by_date 2026-09-17 --regions neimenggu 2>&1 | tail -3`
Expected: 输出 `[neimenggu] ok...`（列表接口带 startTime/endTime=2026-09-17）；`output/date_2026-09-17_neimenggu.xlsx` 内 release_date 全部为 2026-09-17。

- [ ] **Step 3: 提交**

无代码改动，验证通过即可。

---

### Task 6: 汇总输出验证与推送

**Files:** 无（运行验证 + git）

- [ ] **Step 1: 顺序全量今日 + 汇总生成**

Run: `cd /Users/xingan/Documents/software/aiengine/bids-spider && .venv/bin/python fast_run.py today 2>&1 | tail -5`
Expected: 输出 `merged excel saved: output/date_<今日>.xlsx` 与 `summary saved: output/summary_<今日>.md`；两个文件存在且内容正确（摘要含各地区条数表）。

- [ ] **Step 2: 并行批跑 + summarize（可选验证）**

Run: `printf '%s\n' jilin shanxi jiangsu | xargs -P 3 -I {} sh -c '.venv/bin/python fast_run.py by_date 2026-09-18 --regions {} --summary output/date_run_2026-09-18.jsonl' && .venv/bin/python fast_run.py summarize 2026-09-18`
Expected: `output/date_run_2026-09-18.jsonl` 3 行；`output/date_2026-09-18.xlsx` 与 `output/summary_2026-09-18.md` 生成。

- [ ] **Step 3: 推送 fork**

```bash
git add -A && git status
# 确认仅新增/修改预期文件后：
git push fork master
```

Expected: 推送成功，远程 master 更新。
