# 中标/废标数据完善 + 统一防风控 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建统一防风控层；把 22,721 条中标公告的中标单位/金额提取率从 30%/7.5% 提到 ≥85%/≥50%；新增废标原因字段并回填；补近 3 个月结果公告历史；补齐缺失地区结果采集器；每日任务纳入中标/废标补抓。

**Architecture:** 先做两个底层能力（`utils/throttle.py` 防风控、`utils/announce.py` 提取增强 + `void_reason` 字段），再做存量回填（修 backfill 脚本的空串查询 bug），最后用编排脚本补历史、开发缺失采集器、更新定时任务。

**Tech Stack:** Python 3.12 / requests / Playwright / Elasticsearch 8（alias `tenders` → `tenders_v3`，IK 分词）/ 现有 `fast_run.py` 与 `BaseCrawler`

---

## 背景事实（实施前必读，均已实测）

1. **存量回填是最高性价比**：抽样 100 条 `win_company` 为空的中标公告，用现有 `extract_win_company` 重跑 **100% 能提取**、`extract_win_amount` **58%**。说明数据在，只是没回填。
2. **`scripts/backfill_announce_type.py` 的 `--meta` 查询有 bug**：用 `must_not: exists(win_company)`，但 ES 里 `win_company` 都是**空字符串 `""`**（字段存在，exists=true），所以永远匹配不到 → 回填从未生效。必须改成 `term: {"win_company": ""}`。
3. **ES 当前字段**：region/href/title/release_date/crawl_date/amount_wan/bid_deadline/announce_type/win_company/win_amount_wan/html。**没有 void_reason**，需 put_mapping 新增（无需 reindex）。
4. **结果采集现状**：`crawler/result_collector.py` 的 `RESULT_CRAWLERS` 注册 18 个地区；`fast_run.py results <start> [end] --regions X --no-summary` 可跑。11 个地区无注册：hebei, jiangsu, jiangxi, guangdong, hainan, sichuan, ningxia, xinjiang, hlj, gansu, henan。
5. **项目无 pytest**，验证用 `py_compile` + 内联 python 脚本 + 实跑。
6. 环境：`export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"`；ES `curl -s http://localhost:9200`（**无 -u**）；Python 用 `.venv/bin/python`。

---

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `utils/throttle.py` | 新建 | 令牌桶限速 + 退避 + 熔断 + 自适应降速 |
| `crawler/base_crawler.py` | 修改 | 引用 throttle，提供 `_throttle()` / `_with_retry()`；Tender 加 `void_reason` |
| `utils/announce.py` | 修改 | 单位后缀白名单过滤、金额变体、新增 `extract_void_reason` |
| `utils/es.py` | 修改 | TENDER_INDEX_BODY 加 `void_reason` |
| `scripts/backfill_announce_type.py` | 修改 | 修空串查询 bug；新增 `--void` 回填废标原因 |
| `scripts/backfill_results.py` | 新建 | 近 3 个月结果公告按段补抓编排 |
| `crawler/result_<region>.py` | 新建（部分） | 缺失地区结果采集器 |

---

## Task 1: 统一防风控层

**Files:**
- Create: `utils/throttle.py`
- Modify: `crawler/base_crawler.py:47-60`（`__init__` 与新增方法）

- [ ] **Step 1: 创建 `utils/throttle.py`**

```python
"""统一防风控：令牌桶限速 + 指数退避重试 + 连续失败熔断 + 自适应降速。

用法（见 BaseCrawler._throttle / _with_retry）：
    self._throttle()                      # 请求前限速
    data = self._with_retry(lambda: fetch())  # 带退避重试的请求
"""
import random
import time
from dataclasses import dataclass, field


class CircuitOpenError(Exception):
    """连续失败超过阈值，本轮熔断停止。"""


class TokenBucket:
    """令牌桶：按 rate_per_min 匀速补充令牌，请求前 acquire 一个。"""

    def __init__(self, rate_per_min=30, burst=None):
        self.rate_per_min = float(rate_per_min)
        self.capacity = float(burst or max(1, int(rate_per_min) // 2))
        self.tokens = self.capacity
        self.updated = time.monotonic()

    def _refill(self):
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * (self.rate_per_min / 60.0))
        self.updated = now

    def acquire(self):
        while True:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return
            wait = (1 - self.tokens) / max(self.rate_per_min / 60.0, 1e-6)
            time.sleep(min(wait, 5))


@dataclass
class Throttle:
    rate_per_min: int = 30
    min_rate_per_min: int = 6
    max_fail_streak: int = 5
    retries: int = 3
    recover_after: int = 20
    bucket: TokenBucket = field(default=None)
    fail_streak: int = 0
    success_streak: int = 0

    def __post_init__(self):
        if self.bucket is None:
            self.bucket = TokenBucket(self.rate_per_min)

    def wait(self):
        self.bucket.acquire()

    def on_success(self):
        self.fail_streak = 0
        self.success_streak += 1
        if self.success_streak >= self.recover_after and self.bucket.rate_per_min < self.rate_per_min:
            self.bucket.rate_per_min = min(float(self.rate_per_min), self.bucket.rate_per_min * 2)
            self.success_streak = 0

    def on_blocked(self):
        """命中 429/403 等风控：降速并计一次失败。"""
        self.success_streak = 0
        self.bucket.rate_per_min = max(float(self.min_rate_per_min), self.bucket.rate_per_min * 0.5)

    def on_fail(self):
        self.fail_streak += 1
        self.success_streak = 0
        if self.fail_streak >= self.max_fail_streak:
            raise CircuitOpenError(f'连续失败 {self.fail_streak} 次，熔断停止本轮')

    @staticmethod
    def backoff(attempt):
        time.sleep(min(60, (2 ** attempt) + random.uniform(0, 1)))
```

- [ ] **Step 2: 编译检查**

Run: `.venv/bin/python -m py_compile utils/throttle.py`
Expected: 无输出

- [ ] **Step 3: 内联自测（令牌桶速率 + 熔断）**

Run:
```bash
.venv/bin/python - <<'PY'
import time, sys
sys.path.insert(0, '.')
from utils.throttle import Throttle, CircuitOpenError
t = Throttle(rate_per_min=60, max_fail_streak=3, retries=2)  # 60/min = 1/s
t.bucket.tokens = 0  # 清空
s = time.monotonic(); t.wait(); d1 = time.monotonic() - s
print(f"首次等待 {d1:.2f}s（期望约 1s）")
t.on_blocked(); print(f"降速后速率 {t.bucket.rate_per_min}/min（期望 30）")
for _ in range(3): t.on_fail()
print("熔断未触发❌" if True else "")
PY
```
Expected: 打印等待约 1s、降速后 30，且 `on_fail` 第 3 次抛 `CircuitOpenError`（脚本最后可捕获打印"熔断OK"）

- [ ] **Step 4: 在 `BaseCrawler` 集成**

在 `crawler/base_crawler.py` 顶部 import 区加入：

```python
from utils.throttle import Throttle, CircuitOpenError
```

在 `BaseCrawler.__init__` 末尾加入（各爬虫可通过类属性 `rate_per_min` / `max_fail_streak` 覆盖）：

```python
        self._throttler = Throttle(
            rate_per_min=getattr(self, 'rate_per_min', 30),
            max_fail_streak=getattr(self, 'max_fail_streak', 5),
        )
```

在 `BaseCrawler` 类内新增两个方法（放在 `_random_sleep` 之后）：

```python
    def _throttle(self):
        """请求前限速（令牌桶）。"""
        self._throttler.wait()

    def _with_retry(self, fn):
        """带指数退避的请求包装；连续失败超过阈值抛 CircuitOpenError。"""
        for attempt in range(self._throttler.retries):
            try:
                self._throttle()
                result = fn()
                self._throttler.on_success()
                return result
            except CircuitOpenError:
                raise
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ('429', '403', 'too many', 'forbidden', 'rate limit')):
                    self._throttler.on_blocked()
                self._throttler.on_fail()
                if attempt == self._throttler.retries - 1:
                    raise
                self._throttler.backoff(attempt)
```

- [ ] **Step 5: 编译 + 现有爬虫不回归**

Run: `.venv/bin/python -m py_compile crawler/base_crawler.py && .venv/bin/python -c "import sys; sys.path.insert(0,'.'); from crawler.beijing import BeiJing; c=BeiJing(); print('throttler ok:', c._throttler.rate_per_min)"`
Expected: 输出 `throttler ok: 30`

- [ ] **Step 6: 给结果采集器接入限速（最小改动）**

在 `crawler/result_collector.py` 的 `run_result_region` 中，`crawler.run_by_date(...)` 前后不需要改；限速由各采集器的请求点调用 `_throttle()`。为控制范围，本任务只保证 `base_crawler` 提供能力，具体接入在 Task 5 的编排层通过"段间间隔"实现。

- [ ] **Step 7: Commit**

```bash
git add utils/throttle.py crawler/base_crawler.py
git commit -m "feat(throttle): 统一防风控层（令牌桶限速+指数退避+熔断+自适应降速）"
```

---

## Task 2: 提取增强（`utils/announce.py`）

**Files:**
- Modify: `utils/announce.py`

- [ ] **Step 1: 加单位后缀白名单过滤**

在 `utils/announce.py` 中新增：

```python
_ORG_SUFFIX = re.compile(
    r'(有限公司|股份有限公司|有限责任公司|集团|研究院|研究所|设计院|事务所|中心|医院|学校|学院|大学|'
    r'银行|合作社|联合社|工作室|服务部|商行|厂|站|队|院|社|馆)$')


def _clean_company(val):
    """清洗中标单位：去掉前缀噪声；无合法单位后缀的（如项目名）返回空串。"""
    if not val:
        return ''
    v = val.strip()
    v = re.sub(r'^(?:牵头供应商|投标联合体|联合体供应商|联合体)[：:]\s*', '', v)
    v = re.sub(r'[；;]\s*$', '', v).strip()
    for noise in ('原公告的合同包', '合同包', '采购包', '分包'):
        if v.startswith(noise):
            return ''
    if not _ORG_SUFFIX.search(v):
        return ''
    return v
```

- [ ] **Step 2: 在 `extract_win_company` 返回前统一走清洗**

把 `extract_win_company` 里两处 `return val` / `return m2.group(1).strip()` 改为 `return _clean_company(val)` / `return _clean_company(m2.group(1))`。

- [ ] **Step 3: 新增 `extract_void_reason`**

在文件末尾加入：

```python
def extract_void_reason(html):
    """从废标/流标/终止公告正文提取废标原因，未提取到返回空串。"""
    text = _to_text(html).replace('\u3000', ' ')
    for kw in ('废标原因', '流标原因', '终止原因', '失败原因', '废标情形', '流标情形'):
        m = re.search(re.escape(kw) + r'\s*[:：]?\s*([^\n]{2,120})', text)
        if m:
            val = m.group(1).strip().strip('。;；')
            if val:
                return val
    return ''
```

- [ ] **Step 4: 验证白名单与废标原因**

Run:
```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0,'.')
from utils.announce import extract_win_company, extract_void_reason
print("单位1:", extract_win_company("<p>中标供应商：江苏诉服达数据科技有限公司</p>"))
print("噪声:", repr(extract_win_company("<p>中标人：新疆大学喀什校区学生宿舍楼和公共实验楼等建设项目</p>")))
print("废标:", extract_void_reason("<p>废标原因：有效投标人不足三家</p>"))
PY
```
Expected: 单位1 输出公司名；噪声输出 `''`；废标输出"有效投标人不足三家"

- [ ] **Step 5: Commit**

```bash
git add utils/announce.py
git commit -m "feat(announce): 中标单位后缀白名单过滤 + 废标原因提取"
```

---

## Task 3: `void_reason` 字段（Tender + ES mapping）

**Files:**
- Modify: `crawler/base_crawler.py`（Tender dataclass + `_enrich_meta`）
- Modify: `utils/es.py`（TENDER_INDEX_BODY）

- [ ] **Step 1: Tender 加字段**

`crawler/base_crawler.py` 的 `Tender` 末尾加：

```python
    void_reason: str = ''         # 废标原因（仅废标公告）
```

- [ ] **Step 2: `_enrich_meta` 提取废标原因**

在 `_enrich_meta` 的 `try` 块内（`import ... from utils.announce` 之后）加：

```python
            from utils.announce import extract_void_reason
            if tender.announce_type == '废标公告' and not tender.void_reason:
                tender.void_reason = extract_void_reason(text)
```

- [ ] **Step 3: ES mapping 加字段**

`utils/es.py` 的 `TENDER_INDEX_BODY.mappings.properties` 加：

```python
            "void_reason": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
```

- [ ] **Step 4: 对现有索引 put_mapping（无需 reindex）**

Run:
```bash
curl -s -XPUT "http://localhost:9200/tenders/_mapping" -H 'Content-Type: application/json' -d '{"properties":{"void_reason":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}}}'
```
Expected: `{"acknowledged":true}`

- [ ] **Step 5: 编译 + 映射验证**

Run: `.venv/bin/python -m py_compile utils/es.py crawler/base_crawler.py && curl -s "http://localhost:9200/tenders/_mapping" | python3 -c "import json,sys; d=json.load(sys.stdin); p=list(d.values())[0]['mappings']['properties']; print('void_reason' in p)"`
Expected: `True`

- [ ] **Step 6: Commit**

```bash
git add crawler/base_crawler.py utils/es.py
git commit -m "feat(tender): 新增 void_reason 废标原因字段（Tender+ES mapping）"
```

---

## Task 4: 修回填脚本 bug + 存量回填 + 验证报告（核心）

**Files:**
- Modify: `scripts/backfill_announce_type.py`

- [ ] **Step 1: 修 `--meta` 查询空串 bug**

把 `meta_only` 分支的 `should` 改为（覆盖"空字符串"和"字段不存在"两种空态）：

```python
        query = {
            "query": {
                "bool": {
                    "must": [{"term": {"announce_type": "中标公告"}}],
                    "should": [
                        {"term": {"win_company": ""}},
                        {"bool": {"must_not": [{"exists": {"field": "win_company"}}]}},
                        {"bool": {"must_not": [{"exists": {"field": "win_amount_wan"}}]}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": ["_doc"],
        }
```

- [ ] **Step 2: 新增 `--void` 模式回填废标原因**

在 `backfill()` 增加参数 `void_only=False`，并在查询构造前加分支：

```python
    if void_only:
        query = {
            "query": {
                "bool": {
                    "must": [{"term": {"announce_type": "废标公告"}}],
                    "should": [
                        {"term": {"void_reason": ""}},
                        {"bool": {"must_not": [{"exists": {"field": "void_reason"}}]}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": ["_doc"],
        }
```

文档循环内对应加：

```python
                if void_only:
                    html = src.get('html', '') or ''
                    doc = {'void_reason': extract_void_reason(html)}
                elif meta_only:
                    ...
```

顶部 import 加 `extract_void_reason`，`__main__` 加 `void_only = '--void' in args` 并传入。

- [ ] **Step 3: 编译**

Run: `.venv/bin/python -m py_compile scripts/backfill_announce_type.py`
Expected: 无输出

- [ ] **Step 4: 先小批量验证（500 条）**

Run: `.venv/bin/python scripts/backfill_announce_type.py --meta 500`
Expected: 输出"更新成功 N 条"，N≈500；随后抽样确认

- [ ] **Step 5: 抽样确认回填生效**

Run:
```bash
curl -s "http://localhost:9200/tenders/_search" -H 'Content-Type: application/json' -d '{"size":3,"query":{"bool":{"must":[{"term":{"announce_type":"中标公告"}},{"term":{"region":"guangdong"}}],"must_not":[{"term":{"win_company":""}}]}},"_source":["win_company"]}' | python3 -c "import json,sys; d=json.load(sys.stdin); [print(h['_source'].get('win_company')) for h in d['hits']['hits']]"
```
Expected: 打印出公司名（非空）

- [ ] **Step 6: 全量回填（分批，避免压垮 ES）**

Run:
```bash
for i in 1 2 3 4 5 6; do .venv/bin/python scripts/backfill_announce_type.py --meta 5000; sleep 3; done
.venv/bin/python scripts/backfill_announce_type.py --void 5000
```
Expected: 每批"更新成功 N 条"，直到剩余为空（重复跑幂等，已填充的不会再命中）

- [ ] **Step 7: 生成回填前后对比报告**

Run:
```bash
.venv/bin/python - <<'PY'
import json, subprocess, datetime
def stat():
    q = {"size":0,"query":{"term":{"announce_type":"中标公告"}},
         "aggs":{"tot":{"value_count":{"field":"href"}},
                 "wc":{"filter":{"bool":{"must_not":[{"term":{"win_company":""}}]}}},
                 "wa":{"filter":{"exists":{"field":"win_amount_wan"}}}}}
    out = subprocess.run(["curl","-s","http://localhost:9200/tenders/_search","-H","Content-Type: application/json","-d",json.dumps(q)],capture_output=True,text=True).stdout
    a = json.loads(out)["aggregations"]; tot=a["tot"]["value"]
    return tot, a["wc"]["doc_count"], a["wa"]["doc_count"]
tot, wc, wa = stat()
print(f"中标公告 {tot}；中标单位非空 {wc} ({wc/tot*100:.1f}%)；金额非空 {wa} ({wa/tot*100:.1f}%)")
PY
```
Expected: 单位非空率显著提升（目标 ≥85%）、金额 ≥50%

- [ ] **Step 8: Commit**

```bash
git add scripts/backfill_announce_type.py
git commit -m "fix(backfill): 修复空串查询 bug + 新增废标原因回填模式"
```

---

## Task 5: 近 3 个月结果公告补抓编排

**Files:**
- Create: `scripts/backfill_results.py`

- [ ] **Step 1: 创建编排脚本**

```python
"""结果公告（中标/废标）历史补抓编排：按地区×7天段调用 fast_run.py results。

用法：
  .venv/bin/python scripts/backfill_results.py --start 2026-08-22 --end 2026-11-21 --parallel 2
  .venv/bin/python scripts/backfill_results.py --regions guangdong,hainan --start 2026-08-22 --end 2026-11-21
"""
import argparse, json, os, subprocess, sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, '.venv', 'bin', 'python')
sys.path.insert(0, ROOT)
from crawler.result_collector import RESULT_CRAWLERS

OUT = os.path.join(ROOT, 'output', 'backfill')

def _parse(s): return datetime.strptime(s, '%Y-%m-%d').date()
def _fmt(d): return d.strftime('%Y-%m-%d')

def segments(start, end, seg_days=7):
    segs, cur = [], start
    while cur <= end:
        seg_end = min(cur + timedelta(days=seg_days - 1), end)
        segs.append((_fmt(cur), _fmt(seg_end)))
        cur = seg_end + timedelta(days=1)
    return segs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--regions', default='')
    ap.add_argument('--seg-days', type=int, default=7)
    ap.add_argument('--parallel', type=int, default=2)
    ap.add_argument('--gap', type=int, default=20, help='段间间隔秒数（防风控）')
    args = ap.parse_args()

    regions = [r.strip() for r in args.regions.split(',') if r.strip()] or list(RESULT_CRAWLERS)
    os.makedirs(OUT, exist_ok=True)
    run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    progress = os.path.join(OUT, f'results_{run_id}.jsonl')
    tasks = [(r, s, e) for r in regions for (s, e) in segments(_parse(args.start), _parse(args.end), args.seg_days)]
    print(f'共 {len(tasks)} 段，并发 {args.parallel}', flush=True)

    import time
    idx = 0
    while idx < len(tasks):
        batch = tasks[idx: idx + args.parallel]; idx += args.parallel
        procs = []
        for r, s, e in batch:
            log = os.path.join(ROOT, 'logs', f'results_{run_id}_{r}_{s}_{e}.log')
            f = open(log, 'w', encoding='utf-8')
            procs.append((r, s, e, f, subprocess.Popen(
                [PY, 'fast_run.py', 'results', s, e, '--regions', r, '--no-summary'],
                cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)))
        for r, s, e, f, p in procs:
            p.wait(); f.close()
            status = 'ok' if p.returncode == 0 else f'exit{p.returncode}'
            rec = {'region': r, 'start': s, 'end': e, 'status': status}
            with open(progress, 'a', encoding='utf-8') as pf:
                pf.write(json.dumps(rec, ensure_ascii=False) + '\n')
            print(f'  {r} {s}~{e} -> {status}', flush=True)
        time.sleep(args.gap)
    print(f'完成，进度 {progress}')

if __name__ == '__main__':
    main()
```

> 注：`fast_run.py results` 当前**只支持单日**（`run_result_region(region, start, start, ...)`，`fast_run.py:528`），Step 2 需扩展为支持 `start end` 日期范围。

- [ ] **Step 2: 扩展 `fast_run.py results` 支持日期范围**

把 `fast_run.py` 的 `cmd == 'results'` 分支（约 506-530 行）替换为：

```python
    if cmd == 'results':
        # 抓结果公告（中标/废标）：用法 python fast_run.py results <start> [end] [--regions a,b]
        if len(args) < 2:
            print('用法: python fast_run.py results <YYYY-MM-DD> [YYYY-MM-DD] [--regions a,b]')
            sys.exit(1)
        from crawler.result_collector import RESULT_CRAWLERS, run_result_region
        start = args[1]
        end = start
        regions = None
        rest = args[2:]
        i = 0
        while i < len(rest):
            if rest[i] == '--regions' and i + 1 < len(rest):
                regions = [r.strip() for r in rest[i + 1].split(',') if r.strip()]
                i += 2
            elif not rest[i].startswith('--'):
                end = rest[i]
                i += 1
            else:
                i += 1
        result_regions = regions or list(RESULT_CRAWLERS)
        summary_path = os.path.join(date_dir(start), f'date_result_run_{start}.jsonl')
        for region in result_regions:
            if region not in RESULT_CRAWLERS:
                print(f'[results] 未知结果地区: {region}（支持: {", ".join(RESULT_CRAWLERS)}）')
                continue
            run_result_region(region, start, end, summary_path)
        print(f'[results] 结果公告抓取完成（{start}~{end}）')
        return
```

- [ ] **Step 3: 编译 + 小范围验证（1 地区 1 段）**

Run: `.venv/bin/python -m py_compile scripts/backfill_results.py fast_run.py && .venv/bin/python scripts/backfill_results.py --regions guangdong --start 2026-11-14 --end 2026-11-20 --parallel 1`
Expected: 打印 `guangdong 2026-11-14~2026-11-20 -> ok`

- [ ] **Step 4: 验证入库**

Run: `curl -s "http://localhost:9200/tenders/_count" -H 'Content-Type: application/json' -d '{"query":{"bool":{"must":[{"term":{"region":"guangdong"}},{"term":{"announce_type":"中标公告"}},{"range":{"release_date":{"gte":"2026-11-14","lte":"2026-11-20 23:59:59"}}}]}}}'`
Expected: count > 0

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_results.py fast_run.py
git commit -m "feat(results): 新增结果公告历史补抓编排 + results 支持日期范围"
```

---

## Task 6: 每日任务纳入 results

**Files:** 无代码改动（更新 Schedule `8f6dc4dd`）

- [ ] **Step 1: 更新定时任务 prompt**

在"每日补抓昨日商机"任务的第 7 步（summarize）之前插入：

```
6.5 补抓昨日结果公告（中标/废标）：
.venv/bin/python fast_run.py results <昨天> --regions beijing,tianjin,chongqing,shandong,guizhou,zhejiang,yunnan,qinghai,anhui,shanxi,neimenggu,liaoning,jilin,hubei,hunan,guangxi,fujian,shaanxi --no-summary >> logs/results_<昨天>.log 2>&1
（已注册结果采集器的地区；与招标补抓共用 tenders 索引，靠 announce_type 区分，不会重复）
```

- [ ] **Step 2: 记录变更**

在 `docs/superpowers/specs/2026-11-22-winbid-antiban-design.md` 末尾追加"每日任务已纳入 results（2026-11-22）"。

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-11-22-winbid-antiban-design.md
git commit -m "docs: 记录每日任务纳入结果公告补抓"
```

---

## Task 7: 缺失地区结果采集器（分批，逐个地区）

**Files:**
- Create/Modify: `crawler/result_<region>.py`（按需）
- Modify: `crawler/result_collector.py`（注册）

处理顺序（按可获得性）：

- [ ] **Step 1: 审计已覆盖的 9 个地区**（hebei, jiangsu, jiangxi, guangdong, hainan, sichuan, ningxia, xinjiang, henan）
  对每个地区：查 ES 中标/废标数量与最近 30 天覆盖；若中标已足量但**废标缺失**，只需补废标栏目采集器；若已覆盖则跳过（记录理由）。

- [ ] **Step 2: 开发 hlj（中标 38、废标 4，明确缺失）**
  探测 hljcg.hlj.gov.cn 结果公告栏目 → 实现 `crawler/result_hlj.py` → 注册 → 单日验证。

- [ ] **Step 3: 开发 gansu（中标 0、废标 0）**
  探测 ccgp-gansu.gov.cn 结果栏目 → 实现 `crawler/result_gansu.py` → 注册 → 单日验证。

- [ ] **Step 4: 按审计结果补开发其余地区**（逐个，每个：探测入口 → 实现 → 注册 → 单日验证 → commit）

- [ ] **Step 5: 每完成一个地区提交一次**

```bash
git add crawler/result_<region>.py crawler/result_collector.py
git commit -m "feat(result): 新增 <region> 结果公告采集器"
```

> 说明：验证码站点（henan/hlj/gansu 主站有验证码）复用现有 `utils/captcha.py` DdddOCR；若站点改版/风控导致失败，记录并跳过，不阻塞其他地区。

---

## 验收标准

1. 中标单位非空率 ≥ 85%、中标金额 ≥ 50%（全量，回填后统计）
2. 废标 `void_reason` 非空率 ≥ 60%
3. 近 3 个月结果公告补抓完成（18 个已有采集器地区）
4. 缺失地区采集器按审计结果补齐（hlj/gansu 至少完成）
5. 防风控层生效：`Throttle` 单测通过；补抓过程无长时间封禁
6. 每日任务 prompt 已包含 results 步骤

## 已知限制

- 部分中标公告正文无中标单位信息，提取率无法到 100%
- 验证码站点（河南/黑龙江/甘肃）开发难度高，允许延后
- 全量回填 2.2 万+ 文档需分批（每批 5000，间隔 3s）避免压垮单节点 ES
