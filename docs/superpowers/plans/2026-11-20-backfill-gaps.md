# 历史缺口智能补抓系统 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 guizhou/tianjin/shaanxi 三个间歇失效爬虫，并新增编排脚本自动扫描 ES 缺口、按日期段逐地区补抓，补齐 5 个地区的历史断档数据。

**Architecture:** 先修爬虫（复用 neimenggu 已验证的"直连接口、不依赖页面触发"模式），再写 `scripts/backfill_gaps.py`：扫描 ES 生成 (region, 日期段) 缺口清单 → 逐段调用现有 `fast_run.py by_date <start> <end> --regions <region> --no-summary` → 进度写入 jsonl（幂等续跑，exists_urls 天然去重）→ 重扫 ES 出覆盖率报告。

**Tech Stack:** Python 3.12 / requests / Elasticsearch 8 (python client) / Playwright（部分爬虫仍需）/ 现有 `fast_run.py` 与 `BaseCrawler.run_by_date`

---

## 背景事实（实施前必读）

以下均为 2026-11-20/21 实测结论，不要重新猜测：

### guizhou（两个 bug）
1. **列表 body 用错**：旧代码靠页面捕获 `{"parentId","childrenCode"}`，实际接口要 `{"pageNo":1,"pageSize":15,"categoryCode":"ZcyAnnouncement1","_t":<毫秒时间戳>}`
2. **响应解析错位**：接口返回 `{"success":true,"result":{"total":149966,"data":[...],"empty":false}}`，`result.data` 是 **dict** 不是 list，旧代码 `result.get("data") or []` 拿到 dict → 遍历得到字符串 key → 全被 `isinstance(record, dict)` 过滤 → kept=0
3. 实际可用请求头：`Content-Type: application/json` + `Referer: http://www.ccgp-guizhou.gov.cn/site/category?parentId=190013&childrenCode=ZcyAnnouncement&categoryCode=ZcyAnnouncement1`

### tianjin
1. 列表接口 `https://www.tjggzy.cn/api/api/portal/Announcement/Page` **不需要 authorization**（带空 headers 也返回 `statusCode=2000`）
2. 请求体固定字段：`{"PageIndex":1,"PageSize":50,"State":2,"tenderProjectCode":"","bidSectionCode":"","announcementName":"","announcementCode":"","orderByFieldType":1,"orderByType":0,"recordCode":null,"industryType":["4"],"announcementType":"10","infoType":"","districtCode":"","publishStartTime":null,"publishEndTime":null,"publishTimeType":-1}`
3. `publishStartTime/publishEndTime` 传了会被忽略（实测请求 10-25 返回 11-20 数据）→ **必须靠翻页 + 扫描模式补历史**
4. 列表按发布时间倒序（orderByFieldType=1, orderByType=0），PageIndex 从 1 递增

### shaanxi
- `_fetch_page` 已主动剔除时间过滤参数、靠扫描翻页；`max_pages=50`（每页 10 条 = 500 条上限）补 14 天可能不够，需按日期段调大
- 列表需图片验证码（已有 DdddOCR 打通）

### neimenggu / jiangsu
- neimenggu 已修复（带时分秒日期参数 + records<10 早停 bug），历史接口可用，**本次直接补**
- jiangsu 页面只显示当天，历史补抓标 low-priority（尽力而为，不阻塞验收）

### fast_run 能力
- `python fast_run.py by_date <start> [end] --regions a,b --no-summary`：按日期区间抓取，`--no-summary` 只抓取+写单地区 Excel 不合并
- `BaseCrawler.run_by_date` 对 `date_filter='url_param'` 站点用 `build_list_url` 带日期参数；否则扫描模式靠 `guarded_save` 遇早于 start 停止
- 去重：`exists_urls` 命中即跳过（断点续跑天然幂等）

---

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `crawler/guizhou.py` | 修改 | 直连接口 + 正确 body + 正确解析嵌套 data |
| `crawler/tianjin.py` | 修改 | 列表翻页（去掉 auth 强依赖） |
| `crawler/shaanxi.py` | 修改 | 日期模式按需调大翻页上限 |
| `scripts/backfill_gaps.py` | 新建 | 缺口扫描 + 逐段补抓编排 + 进度/报告 |
| `docs/superpowers/specs/2026-11-20-backfill-gaps-design.md` | 已存在 | 设计文档 |

---

## Task 1: 修复 guizhou 爬虫（直连接口）

**Files:**
- Modify: `crawler/guizhou.py:21-77`

- [ ] **Step 1: 写一个最小验证脚本，确认直连接口可取数据**

创建 `/tmp/verify_guizhou.py`：

```python
import requests, time
H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json",
     "Referer": "http://www.ccgp-guizhou.gov.cn/site/category?parentId=190013&childrenCode=ZcyAnnouncement&categoryCode=ZcyAnnouncement1",
     "Accept": "application/json, text/plain, */*"}
api = "http://www.ccgp-guizhou.gov.cn/portal/category"
body = {"pageNo": 1, "pageSize": 15, "categoryCode": "ZcyAnnouncement1", "_t": int(time.time() * 1000)}
d = requests.post(api, json=body, headers=H, timeout=15).json()
inner = (d.get("result") or {}).get("data") or {}
recs = inner.get("data") or []
print("success:", d.get("success"), "total:", inner.get("total"), "records:", len(recs))
assert recs and isinstance(recs[0], dict), "接口未返回记录"
print("首条:", recs[0].get("publishDate"), str(recs[0].get("title"))[:30])
```

- [ ] **Step 2: 运行验证**

Run: `.venv/bin/python /tmp/verify_guizhou.py`
Expected: `success: True total: 1499xx records: 15`，打印首条日期与标题

- [ ] **Step 3: 改 `__init__` 与 `_crawl`，去掉页面捕获、直连接口**

将 `crawler/guizhou.py` 的 `__init__` 改为：

```python
    def __init__(self):
        super().__init__('guizhou')
        self.max_pages = 50
        self.list_url = 'http://www.ccgp-guizhou.gov.cn/site/category?parentId=190013&childrenCode=ZcyAnnouncement'
        self.list_api = 'http://www.ccgp-guizhou.gov.cn/portal/category'
        self.detail_api = 'http://www.ccgp-guizhou.gov.cn/portal/detail'
        self.parent_id = '190013'
        self.category_code = 'ZcyAnnouncement1'
        self.headers = {
            'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
            'Content-Type': 'application/json',
            'Referer': (self.list_url + '&categoryCode=ZcyAnnouncement1'),
            'Accept': 'application/json, text/plain, */*',
        }
```

将 `_crawl` 整个方法替换为（不再需要 page）：

```python
    def _crawl(self, context):
        # 2026-11-20：页面触发式捕获请求头失效（列表页改版），改直连接口。
        # 接口 body 必须为 categoryCode 结构，否则 result.data 为 None。
        import time as _time
        for page_no in range(1, self.max_pages + 1):
            body = {'pageNo': page_no, 'pageSize': 15,
                    'categoryCode': self.category_code, '_t': int(_time.time() * 1000)}
            try:
                resp = requests.post(self.list_api, json=body, headers=self.headers, timeout=30)
                data = resp.json()
            except Exception as e:
                logger.error(f"[{self.region}]list request failed: {e}")
                break
            result = data.get('result') if isinstance(data, dict) else None
            inner = result.get('data') if isinstance(result, dict) else None
            records = inner.get('data') if isinstance(inner, dict) else None
            if not records:
                break
            logger.info(f"[{self.region}]get {len(records)} tenders list success (page {page_no}).")
            for record in records:
                if not isinstance(record, dict):
                    continue
                article_id = record.get('articleId') or ''
                title = (record.get('title') or '').strip()
                if not article_id or not title:
                    continue
                href = f'http://www.ccgp-guizhou.gov.cn/site/detail?parentId={self.parent_id}&articleId={quote(article_id)}'
                if href in self.exists_urls:
                    continue
                release_date = self._parse_date(record.get('publishDate'))
                html = self._get_detail(context, article_id)
                tender = Tender(region=self.region, href=href, title=title,
                                release_date=release_date, html=html,
                                crawl_date=self._get_crawl_date())
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
            if len(records) < 15:
                break
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")
```

在文件顶部 import 区加入 `import requests`（`import json` 保留，`import time` 已存在可复用）。

- [ ] **Step 4: 编译检查**

Run: `.venv/bin/python -m py_compile crawler/guizhou.py`
Expected: 无输出（成功）

- [ ] **Step 5: 实跑验证（今天的数据，量小）**

Run: `.venv/bin/python fast_run.py by_date 2026-11-21 --regions guizhou --no-summary > logs/test_guizhou_fix.log 2>&1; grep -E "^\[guizhou\]|get .* tenders list success|Save .* tenders to tenders successfully" logs/test_guizhou_fix.log | tail -5`
Expected: 出现 `get N tenders list success` 且 `[guizhou] ok, kept=N`（N>0）或至少非 500/None

- [ ] **Step 6: Commit**

```bash
git add crawler/guizhou.py
git commit -m "fix(guizhou): 直连接口修复列表请求（body 改 categoryCode + 解析嵌套 data）"
```

---

## Task 2: 修复 tianjin 爬虫（列表翻页）

**Files:**
- Modify: `crawler/tianjin.py:70-138`

- [ ] **Step 1: 验证翻页接口可用**

创建 `/tmp/verify_tianjin.py`：

```python
import requests
H = {"Content-Type": "application/json", "Referer": "https://www.tjggzy.cn/announcementIndex", "User-Agent": "Mozilla/5.0"}
api = "https://www.tjggzy.cn/api/api/portal/Announcement/Page"
def page(i):
    body = {"PageIndex": i, "PageSize": 50, "State": 2, "tenderProjectCode": "", "bidSectionCode": "",
            "announcementName": "", "announcementCode": "", "orderByFieldType": 1, "orderByType": 0,
            "recordCode": None, "industryType": ["4"], "announcementType": "10", "infoType": "",
            "districtCode": "", "publishStartTime": None, "publishEndTime": None, "publishTimeType": -1}
    return requests.post(api, json=body, headers=H, timeout=20).json()
for i in (1, 2):
    d = page(i)
    recs = (d.get("responseData") or {}).get("records") or []
    print(f"page{i}: statusCode={d.get('statusCode')} records={len(recs)}",
          (recs[0].get("publishTime") if recs else ""))
```

- [ ] **Step 2: 运行验证**

Run: `.venv/bin/python /tmp/verify_tianjin.py`
Expected: 两页都 `statusCode=2000 records=50`，page2 首条时间早于 page1（确认倒序）

- [ ] **Step 3: 改写 `_get_tender_list` 支持翻页**

将 `crawler/tianjin.py` 的 `_get_tender_list` 替换为：

```python
    def _get_tender_list(self, context, hylx, xxlx):
        """传入行业类型与信息类型，翻页取全部（列表按发布时间倒序，遇目标日由 guarded_save 停止）。"""
        logger.info(f"start to get tender list for {hylx}, {xxlx}.")
        all_ids = set()
        for page_index in range(1, self.max_pages + 1):
            body = {
                "PageIndex": page_index,
                "PageSize": 50,
                "State": 2,
                "tenderProjectCode": "",
                "bidSectionCode": "",
                "announcementName": "",
                "announcementCode": "",
                "orderByFieldType": 1,
                "orderByType": 0,
                "recordCode": None,
                "industryType": [hylx],
                "announcementType": xxlx,
                "infoType": "",
                "districtCode": "",
                "publishStartTime": None,
                "publishEndTime": None,
                "publishTimeType": -1,
            }
            try:
                response = context.request.post(
                    url="https://www.tjggzy.cn/api/api/portal/Announcement/Page",
                    data=json.dumps(body),
                    headers=self.headers,
                )
                data = response.json()
            except Exception as e:
                logger.error(f"get tender list failed (page {page_index}): {e}")
                break
            if data.get("statusCode") != 2000:
                logger.error(f"get tender list error:{data}")
                break
            records = (data.get("responseData") or {}).get("records") or []
            logger.info(f"[{self.region}]list page {page_index}: {len(records)} records")
            if not records:
                break
            for record in records:
                all_ids.add(record['announcementId'])
            if len(records) < 50:
                break
        return all_ids
```

在 `__init__` 中补 `self.max_pages = 50`：

```python
    def __init__(self):
        super().__init__('tianjin')
        self.authorization = None
        self.headers = None
        self.max_pages = 50
```

- [ ] **Step 4: 让 `_crawl` 的 headers 在拿不到 authorization 时仍可用**

将 `_crawl` 中设置 headers 的段落改为（去掉 None 值 header）：

```python
        self.headers = {"Content-Type": "application/json",
                        "Referer": "https://www.tjggzy.cn/announcementIndex",
                        "User-Agent": page.evaluate("navigator.userAgent")}
        if self.authorization:
            self.headers["Authorization"] = self.authorization
```

- [ ] **Step 5: 编译检查**

Run: `.venv/bin/python -m py_compile crawler/tianjin.py`
Expected: 无输出

- [ ] **Step 6: 实跑验证（今天）**

Run: `.venv/bin/python fast_run.py by_date 2026-11-21 --regions tianjin --no-summary > logs/test_tianjin_fix.log 2>&1; grep -E "^\[tianjin\]|list page|Save .* tenders to tenders successfully" logs/test_tianjin_fix.log | tail -6`
Expected: 出现 `list page 1: ...` 且 `[tianjin] ok, kept=N`（N>0）

- [ ] **Step 7: Commit**

```bash
git add crawler/tianjin.py
git commit -m "fix(tianjin): 列表接口支持翻页 + 去除 authorization 强依赖"
```

---

## Task 3: shaanxi 日期模式翻页上限

**Files:**
- Modify: `crawler/shaanxi.py:17-20`

- [ ] **Step 1: 日期模式下自动放大翻页上限**

修改 `crawler/shaanxi.py` 的 `__init__`：

```python
    def __init__(self, max_pages=50):
        super().__init__('shaanxi', max_page_num=None)
        self.max_pages = max_pages
        self.max_items = 5000
        self.list_url = 'https://www.ccgp-shaanxi.gov.cn/cms-sx/site/shanxi/xxgg/index.html?result=result'
        self.list_api = 'https://www.ccgp-shaanxi.gov.cn/freecms/rest/v1/notice/selectInfoMoreChannel.do'
        self.base_url = 'https://www.ccgp-shaanxi.gov.cn'
        self.ocr = DdddOCR()
```

在 `_get_tenders` 开头加入日期模式下的上限放大（`self._date_start` 由 `run_by_date` 注入）：

```python
    def _get_tenders(self, context, code, query):
        # 日期补抓模式：允许翻更深（每页 10 条），并抬高总条数上限
        if getattr(self, '_date_start', None):
            self.max_pages = max(self.max_pages, 300)
            self.max_items = max(self.max_items, 5000)
        page_no = 1
        total = 0
```

（保留原有 while 循环体不变。）

- [ ] **Step 2: 编译检查**

Run: `.venv/bin/python -m py_compile crawler/shaanxi.py`
Expected: 无输出

- [ ] **Step 3: Commit**

```bash
git add crawler/shaanxi.py
git commit -m "fix(shaanxi): 日期补抓模式放大翻页上限至 300 页"
```

---

## Task 4: 缺口扫描器 `scripts/backfill_gaps.py`

**Files:**
- Create: `scripts/backfill_gaps.py`

- [ ] **Step 1: 编写扫描 + 编排脚本（完整实现）**

创建 `scripts/backfill_gaps.py`：

```python
"""历史缺口智能补抓：扫描 ES 按地区×日期找断档，逐段调用 fast_run by_date 补抓。

用法：
  # 只扫描出缺口清单（不抓取）
  .venv/bin/python scripts/backfill_gaps.py scan --regions neimenggu,guizhou

  # 扫描并补抓（默认按 7 天一段）
  .venv/bin/python scripts/backfill_gaps.py run --regions neimenggu,guizhou --seg-days 7 --parallel 2

  # 从某地区补（指定日期范围）
  .venv/bin/python scripts/backfill_gaps.py run --regions tianjin --start 2026-10-23 --end 2026-11-08
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from elasticsearch import Elasticsearch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, '.venv', 'bin', 'python')
OUT_DIR = os.path.join(ROOT, 'output', 'backfill')


def client():
    return Elasticsearch(['http://127.0.0.1:9200'], request_timeout=60)


def _fmt(d):
    return d.strftime('%Y-%m-%d')


def _parse(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


def scan_gaps(regions):
    """返回 {region: [日期字符串...]}，覆盖区间 = 地区首次入库日 ~ 昨天。"""
    es = client()
    yesterday = date.today() - timedelta(days=1)
    upper = _fmt(yesterday) + ' 23:59:59'  # 兼容 release_date 的纯日期与带时分秒两种格式
    # 每地区首次入库日（release_date 是 keyword，不能用 min 聚合，改用 terms asc size 1）
    first = es.search(index='tenders', body={
        'size': 0,
        'aggs': {'r': {'terms': {'field': 'region', 'size': 50},
                       'aggs': {'first': {'terms': {'field': 'release_date', 'size': 1,
                                                    'order': {'_key': 'asc'}}}}}},
    })
    first_day = {}
    for b in first['aggregations']['r']['buckets']:
        fb = b['first']['buckets']
        if fb:
            first_day[b['key']] = _parse(str(fb[0]['key'])[:10])

    # 地区×日期覆盖
    have = defaultdict(set)
    after = None
    while True:
        comp = {'size': 5000, 'sources': [
            {'region': {'terms': {'field': 'region'}}},
            {'day': {'terms': {'field': 'release_date'}}},
        ]}
        if after:
            comp['after'] = after
        body = {'size': 0, 'query': {'range': {'release_date': {'gte': '2026-08-01', 'lte': upper}}},
                'aggs': {'c': {'composite': comp}}}
        resp = es.search(index='tenders', body=body)
        buckets = resp['aggregations']['c']['buckets']
        for b in buckets:
            r = b['key']['region']
            d = str(b['key']['day'])[:10]
            if '2026-08-01' <= d <= _fmt(yesterday):
                have[r].add(d)
        after = resp['aggregations']['c'].get('after_key')
        if not buckets or not after:
            break

    gaps = {}
    for r in (regions or first_day.keys()):
        if r not in first_day:
            continue
        start = first_day[r]
        miss = []
        d = start
        while d <= yesterday:
            if _fmt(d) not in have[r]:
                miss.append(_fmt(d))
            d += timedelta(days=1)
        if miss:
            gaps[r] = miss
    return gaps


def to_segments(days, seg_days):
    """把日期列表压成连续日期段 [(start,end), ...]，每段不超过 seg_days 天。"""
    if not days:
        return []
    ds = sorted(_parse(x) for x in days)
    segs = []
    cur_s = cur_e = ds[0]
    for d in ds[1:]:
        if d == cur_e + timedelta(days=1) and (d - cur_s).days < seg_days - 1:
            cur_e = d
        else:
            segs.append((cur_s, cur_e))
            cur_s = cur_e = d
    segs.append((cur_s, cur_e))
    return [(_fmt(s), _fmt(e)) for s, e in segs]


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_scan = sub.add_parser('scan')
    p_scan.add_argument('--regions', default='')

    p_run = sub.add_parser('run')
    p_run.add_argument('--regions', default='')
    p_run.add_argument('--start', default='')
    p_run.add_argument('--end', default='')
    p_run.add_argument('--seg-days', type=int, default=7)
    p_run.add_argument('--parallel', type=int, default=2)
    p_run.add_argument('--max-fail', type=int, default=3)

    args = ap.parse_args()
    regions = [r.strip() for r in args.regions.split(',') if r.strip()]

    os.makedirs(OUT_DIR, exist_ok=True)
    gaps = scan_gaps(regions or None)
    if not gaps:
        print('无缺口')
        return
    print('缺口概览:')
    for r, days in sorted(gaps.items()):
        print(f'  {r:12s} {len(days):3d} 天  {days[0]} ~ {days[-1]}')

    if args.cmd == 'scan':
        return

    run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    progress_path = os.path.join(OUT_DIR, f'{run_id}.jsonl')

    # 显式日期范围覆盖
    if args.start and args.end:
        d = _parse(args.start)
        endp = _parse(args.end)
        days = []
        while d <= endp:
            days.append(_fmt(d))
            d += timedelta(days=1)
        gaps = {r: days for r in regions}

    tasks = []
    for r, days in gaps.items():
        for (s, e) in to_segments(days, args.seg_days):
            tasks.append((r, s, e))
    print(f'共 {len(tasks)} 个补抓段，并发 {args.parallel}')

    fail_count = defaultdict(int)
    done = 0
    while tasks:
        batch = []
        while tasks and len(batch) < args.parallel:
            r, s, e = tasks.pop(0)
            if fail_count[r] >= args.max_fail:
                print(f'  [skip] {r} 连续失败 {fail_count[r]} 次，熔断')
                continue
            batch.append((r, s, e))
        if not batch:
            break
        procs = []
        for r, s, e in batch:
            log_path = os.path.join(ROOT, 'logs', f'backfill_{run_id}_{r}_{s}_{e}.log')
            logf = open(log_path, 'w', encoding='utf-8')
            procs.append((r, s, e, logf, subprocess.Popen(
                [PY, 'fast_run.py', 'by_date', s, e, '--regions', r, '--no-summary'],
                cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT)))
        for r, s, e, logf, p in procs:
            code = p.wait()
            logf.close()
            done += 1
            status = 'ok' if code == 0 else f'exit{code}'
            ok_flag = code == 0
            fail_count[r] = 0 if ok_flag else fail_count[r] + 1
            rec = {'region': r, 'start': s, 'end': e, 'exit': code, 'status': status}
            with open(progress_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            print(f'  [{done}/{done + len(tasks)}] {r} {s}~{e} -> {status}')
    print(f'补抓完成，进度: {progress_path}')


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 编译检查**

Run: `.venv/bin/python -m py_compile scripts/backfill_gaps.py`
Expected: 无输出

- [ ] **Step 3: 只扫描不抓取，确认缺口清单正确**

Run: `.venv/bin/python scripts/backfill_gaps.py scan --regions neimenggu,guizhou,jiangsu,tianjin,shaanxi`
Expected: 打印 5 个地区缺口天数（neimenggu ≈64、guizhou ≈51、jiangsu ≈28、tianjin ≈17、shaanxi ≈14，允许因今日数据入库而略变）

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_gaps.py
git commit -m "feat(backfill): 新增历史缺口扫描与按段补抓编排脚本"
```

---

## Task 5: 按地区分批实跑补抓

**Files:** 无代码改动，仅执行

- [ ] **Step 1: 先补 neimenggu（最大的确定性缺口，接口已确认可用）**

Run:
```bash
.venv/bin/python scripts/backfill_gaps.py run --regions neimenggu --seg-days 7 --parallel 1
```
Expected: 逐段打印 `neimenggu <start>~<end> -> ok`，约 10 段（64 天 / 7）

- [ ] **Step 2: 抽查 neimenggu 补抓结果**

Run:
```bash
curl -s "http://localhost:9200/tenders/_count" -H 'Content-Type: application/json' -d '{"query":{"bool":{"must":[{"term":{"region":"neimenggu"}},{"range":{"release_date":{"gte":"2026-09-20","lt":"2026-10-20"}}}]}}}'
```
Expected: count 明显 > 0（补前该区间为 0）

- [ ] **Step 3: 补 guizhou（Task 1 已修好）**

Run:
```bash
.venv/bin/python scripts/backfill_gaps.py run --regions guizhou --seg-days 7 --parallel 1
```
Expected: 逐段 `ok`；若某段出现 `exit1`，查看 `logs/backfill_*_guizhou_*.log` 定位

- [ ] **Step 4: 补 tianjin + shaanxi（并发 2）**

Run:
```bash
.venv/bin/python scripts/backfill_gaps.py run --regions tianjin,shaanxi --seg-days 7 --parallel 2
```
Expected: 两地区各自逐段 `ok`

- [ ] **Step 5: jiangsu 尽力而为（页面只显示当天，预期多数段 no_match，不阻塞）**

Run:
```bash
.venv/bin/python scripts/backfill_gaps.py run --regions jiangsu --seg-days 7 --parallel 1
```
Expected: 允许 `no_match`；能补到多少算多少

- [ ] **Step 6: 重扫确认缺口收敛**

Run: `.venv/bin/python scripts/backfill_gaps.py scan --regions neimenggu,guizhou,jiangsu,tianjin,shaanxi`
Expected: neimenggu/guizhou/tianjin/shaanxi 缺口天数大幅下降（接近 0）；jiangsu 可能仍有缺口（已知限制）

---

## Task 6: 覆盖率报告 + 提交

**Files:**
- Create: `output/backfill/<run_id>.md`（脚本产物，不入库）

- [ ] **Step 1: 生成补前后对比报告**

Run:
```bash
.venv/bin/python - <<'PY'
import subprocess, json
# 用 scan 输出做补后快照
out = subprocess.run(['.venv/bin/python','scripts/backfill_gaps.py','scan','--regions','neimenggu,guizhou,jiangsu,tianjin,shaanxi'], capture_output=True, text=True).stdout
print(out)
open('output/backfill/final_coverage.txt','w').write(out)
PY
```
Expected: 打印最终缺口概览并写入 `output/backfill/final_coverage.txt`

- [ ] **Step 2: 提交代码（不含 output/logs）**

```bash
git add crawler/guizhou.py crawler/tianjin.py crawler/shaanxi.py scripts/backfill_gaps.py
git commit -m "feat(backfill): 修复 guizhou/tianjin/shaanxi + 新增缺口补抓编排"
```

- [ ] **Step 3: 确认工作树干净**

Run: `git status --porcelain`
Expected: 仅剩 output/logs 等被 .gitignore 忽略的项（无输出）

---

## 验收标准

1. `guizhou/tianjin/shaanxi` 今日实跑均 `ok, kept>0`
2. `scan` 重扫后 neimenggu/guizhou/tianjin/shaanxi 缺口收敛到接近 0
3. 全程无 403/429 封禁（遇风控由熔断跳过，记录在 progress jsonl）
4. 脚本幂等：重复 run 已入库数据全部跳过（kept 只含新增）

## 已知限制（不阻塞验收）

- jiangsu：列表页只显示当天，历史基本补不回，标 low-priority
- tianjin 接口忽略日期参数，补历史依赖翻页深度（max_pages=50 × 50 条 = 2500 条/组合，够覆盖 17 天）
- 海南/天津 release_date 带时间戳、河南无日期等站点特性沿用现有逻辑
