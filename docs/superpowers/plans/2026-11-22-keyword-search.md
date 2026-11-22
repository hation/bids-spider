# 关键词商机检索（标题+正文）+ 投标情况关联 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增"输入关键词 → 标题+正文加权检索 → 导出招标项目及其中标/废标情况（含供应商、价格、联系人电话、链接）"的脚本，并补充联系人字段与存量回填；同时整理更新项目功能说明。

**Architecture:** 先扩展数据层（`utils/announce.py` 提取联系人/电话 + Tender/ES 新字段 + 回填），再新增独立检索脚本 `scripts/search_tenders.py`（ES multi_match 加权 + 招标↔中标关联 + Excel/MD 导出），最后更新功能文档。

**Tech Stack:** Python 3.12 / Elasticsearch 8（alias `tenders`→`tenders_v3`，IK 分词）/ pandas+openpyxl / 现有 `utils/announce.py`、`analyze_today.py`

---

## 背景事实（已确认）

1. ES 已启用 IK：`title` 与 `html` 均为 text 且可全文检索（`title` 有 `keyword` 子字段）。
2. 现有 `scripts/query_opportunities.py` 的 `screen_window` **只匹配标题**（`ao.match_keywords(title, ...)`），不搜正文——本计划新增独立脚本，不改动它。
3. 已有字段：`region/href/title/release_date/crawl_date/amount_wan/bid_deadline/announce_type/win_company/win_amount_wan/void_reason/html`；**缺 contact_person/contact_phone/contact_subject**。
4. 无项目编号字段，关联靠"编号正则 + 标题归一化"。
5. 环境：`export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"`；ES `curl -s http://localhost:9200`（无认证）；Python 用 `.venv/bin/python`；项目无 pytest，用 py_compile + 实跑验证。

---

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `utils/announce.py` | 修改 | 新增 `extract_contact`（联系人/电话，中标商优先兜底采购人） |
| `crawler/base_crawler.py` | 修改 | Tender 加 3 字段；`_enrich_meta` 调 extract_contact |
| `utils/es.py` | 修改 | TENDER_INDEX_BODY 加 3 字段 |
| `scripts/backfill_announce_type.py` | 修改 | 新增 `--contact` 回填模式 |
| `scripts/search_tenders.py` | 新建 | 关键词检索 + 关联 + 导出 |
| `docs/项目功能说明.md` | 修改 | 更新最新爬虫/数据能力 |

---

## Task 1: 联系人/电话提取工具

**Files:**
- Modify: `utils/announce.py`

- [ ] **Step 1: 新增 `extract_contact`**

在 `utils/announce.py` 末尾追加：

```python
_CONTACT_PATS = [
    r'(?:中标供应商|成交供应商|中标单位|中标人)[^\n]{0,40}?(?:联系人|项目联系人)\s*[:：]\s*([^\s，。,；;\n]{2,20})',
    r'(?:中标供应商|成交供应商|中标单位|中标人)[^\n]{0,40}?(?:联系电话|电话|联系方式)\s*[:：]\s*([\d\-()（）转\s]{7,25})',
    r'(?:联系人|项目联系人)\s*[:：]\s*([^\s，。,；;\n]{2,20})',
    r'(?:联系电话|联系方式|电话)\s*[:：]\s*([\d\-()（）转\s]{7,25})',
]


def extract_contact(html):
    """提取联系人与联系电话，返回 (person, phone, subject)。

    subject：命中"中标供应商/成交供应商/中标单位/中标人"前置上下文的记为"中标供应商"，
    否则（采购人/代理机构段落）记为"采购人/代理"。未提取到返回空串。
    """
    text = _to_text(html).replace('\u3000', ' ')
    person = phone = ''
    subject = ''
    # 第一轮：中标供应商前置（优先）
    for pat in _CONTACT_PATS[:2]:
        m = re.search(pat, text)
        if m:
            val = re.sub(r'\s+', '', m.group(1)).strip()
            if '联系' in pat or '电话' in pat:
                phone = phone or val
            else:
                person = person or val
            subject = subject or '中标供应商'
    # 第二轮：全文兜底（采购人/代理）
    if not person:
        m = re.search(_CONTACT_PATS[2], text)
        if m:
            person = re.sub(r'\s+', '', m.group(1)).strip()
            subject = subject or '采购人/代理'
    if not phone:
        m = re.search(_CONTACT_PATS[3], text)
        if m:
            phone = re.sub(r'\s+', '', m.group(1)).strip()
            subject = subject or '采购人/代理'
    return person, phone, subject
```

- [ ] **Step 2: 编译 + 内联测试**

Run:
```bash
.venv/bin/python -m py_compile utils/announce.py && .venv/bin/python - <<'PY'
import sys; sys.path.insert(0,'.')
from utils.announce import extract_contact
print(extract_contact("<p>中标供应商：XX科技有限公司</p><p>联系人：张三</p><p>联系电话：0755-12345678</p>"))
print(extract_contact("<p>采购人：某局</p><p>联系人：李四</p><p>电话：010-88886666</p>"))
print(extract_contact("<p>无联系方式</p>"))
PY
```
Expected: 第一行 `('张三', '0755-12345678', '中标供应商')`（或 person/phone 分别有值）；第二行 `('李四', '010-88886666', '采购人/代理')`；第三行 `('', '', '')`

- [ ] **Step 3: Commit**

```bash
git add utils/announce.py
git commit -m "feat(announce): 新增联系人/联系电话提取（中标商优先兜底采购人）"
```

---

## Task 2: Tender/ES 新增联系人字段

**Files:**
- Modify: `crawler/base_crawler.py`、`utils/es.py`

- [ ] **Step 1: Tender 加字段**

`crawler/base_crawler.py` 的 `Tender` 末尾加：

```python
    contact_person: str = ''      # 联系人
    contact_phone: str = ''       # 联系电话
    contact_subject: str = ''     # 联系人主体（中标供应商 / 采购人/代理）
```

- [ ] **Step 2: `_enrich_meta` 提取**

在 `_enrich_meta` 的 `try` 块中（import 行补 `extract_contact`），加：

```python
            if not tender.contact_person and not tender.contact_phone:
                person, phone, subject = extract_contact(tender.html)
                tender.contact_person = person
                tender.contact_phone = phone
                tender.contact_subject = subject
```

- [ ] **Step 3: ES mapping 加 3 字段**

`utils/es.py` 的 `TENDER_INDEX_BODY.mappings.properties` 加：

```python
            "contact_person": {"type": "keyword"},
            "contact_phone": {"type": "keyword"},
            "contact_subject": {"type": "keyword"},
```

并对现有索引 put_mapping：

```bash
curl -s -XPUT "http://localhost:9200/tenders/_mapping" -H 'Content-Type: application/json' -d '{"properties":{"contact_person":{"type":"keyword"},"contact_phone":{"type":"keyword"},"contact_subject":{"type":"keyword"}}}'
```

- [ ] **Step 4: 验证**

Run: `.venv/bin/python -m py_compile utils/es.py crawler/base_crawler.py && curl -s "http://localhost:9200/tenders/_mapping" | python3 -c "import json,sys; p=list(json.load(sys.stdin).values())[0]['mappings']['properties']; print(all(k in p for k in ('contact_person','contact_phone','contact_subject')))"`
Expected: `True`

- [ ] **Step 5: Commit**

```bash
git add crawler/base_crawler.py utils/es.py
git commit -m "feat(tender): 新增联系人/电话/主体字段（Tender+ES mapping）"
```

---

## Task 3: 存量回填联系人

**Files:**
- Modify: `scripts/backfill_announce_type.py`

- [ ] **Step 1: 加 `--contact` 模式**

在 `backfill()` 增加 `contact_only=False` 参数，查询构造前加分支：

```python
    if contact_only:
        query = {
            "query": {
                "bool": {
                    "must": [],
                    "should": [
                        {"term": {"contact_person": ""}},
                        {"bool": {"must_not": [{"exists": {"field": "contact_person"}}]}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": ["_doc"],
        }
```

文档循环内加：

```python
                if contact_only:
                    html = src.get('html', '') or ''
                    p, ph, sub = extract_contact(html)
                    doc = {'contact_person': p, 'contact_phone': ph, 'contact_subject': sub}
```

顶部 import 加 `extract_contact`；`__main__` 加 `contact_only = '--contact' in args` 并传入。

- [ ] **Step 2: 编译 + 小批验证**

Run: `.venv/bin/python -m py_compile scripts/backfill_announce_type.py && .venv/bin/python scripts/backfill_announce_type.py --contact 500`
Expected: 打印"更新成功 N 条"（N>0）

- [ ] **Step 3: 全量回填（分批）**

Run:
```bash
for i in 1 2 3 4 5 6 7 8; do .venv/bin/python scripts/backfill_announce_type.py --contact 5000; sleep 2; done
```
Expected: 各批收敛；最后统计联系人非空率

- [ ] **Step 4: 统计非空率**

Run:
```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0,'.')
from elasticsearch import Elasticsearch
es=Elasticsearch(['http://127.0.0.1:9200'], request_timeout=60)
tot=es.count(index='tenders')['count']
cp=es.count(index='tenders', body={'query':{'bool':{'must_not':[{'term':{'contact_person':''}}]}}})['count']
print(f"全库 {tot}，含联系人 {cp} ({cp/tot*100:.1f}%)")
PY
```
Expected: 打印含联系人比例（把实际数字记录到报告，不设硬指标）

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_announce_type.py
git commit -m "feat(backfill): 新增联系人/电话回填模式"
```

---

## Task 4: 关键词检索导出脚本（核心）

**Files:**
- Create: `scripts/search_tenders.py`

- [ ] **Step 1: 创建脚本（完整实现）**

```python
"""关键词商机检索：标题+正文加权搜索，关联中标/废标情况，导出 Excel + Markdown。

用法：
  .venv/bin/python scripts/search_tenders.py "服务器"                      # 默认近3个月
  .venv/bin/python scripts/search_tenders.py "信创 数据库" --start 2026-09-01 --end 2026-11-22
  .venv/bin/python scripts/search_tenders.py "算力" --all                  # 全库
  .venv/bin/python scripts/search_tenders.py "服务器" --size 5000
"""
import argparse
import os
import re
import sys
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from elasticsearch import Elasticsearch

OUTPUT_BASE = os.path.join('output', '关键词检索')

# 项目编号正则（河北 Z13xxx、通用字母+数字、JG 等）
_CODE_PATTERNS = [
    re.compile(r'([A-Z]{1,3}\d{10,})'),
    re.compile(r'([A-Z]{2,6}[-_]?[A-Z0-9]{6,})'),
    re.compile(r'(JG[A-Z0-9\-]{4,})'),
]
# 标题后缀（归一化时去除）
_SUFFIX = re.compile(
    r'(中标（成交）结果公告|中标\(成交\)结果公告|中标结果公告|中标候选人公示|成交结果公告|'
    r'结果更正公告|结果公告|中标公告|成交公告|废标公告|终止公告|流标公告|采购公告|招标公告|'
    r'竞争性磋商公告|竞争性谈判公告|询价公告|单一来源公告|第[一二三四五六七八九十\d]+次|'
    r'[（(]第?[一二三四五六七八九十\d]+包[)）]|[（(]第[一二三四五六七八九十\d]+次[)）])')


def extract_code(title):
    t = str(title or '')
    for p in _CODE_PATTERNS:
        m = p.search(t)
        if m:
            return m.group(1)
    return ''


def normalize_title(title):
    t = str(title or '')
    t = _SUFFIX.sub('', t)
    t = re.sub(r'[\s　]+', '', t)
    return t[:24]


def search(keyword, start=None, end=None, size=2000):
    es = Elasticsearch(['http://127.0.0.1:9200'], request_timeout=120)
    must = []
    if start and end:
        must.append({'range': {'release_date': {'gte': start, 'lte': end + ' 23:59:59'}}})
    body = {
        'size': size,
        'query': {
            'bool': {
                'must': must,
                'should': [
                    {'match': {'title': {'query': keyword, 'boost': 3}}},
                    {'match': {'html': {'query': keyword}}},
                ],
                'minimum_should_match': 1,
            }
        },
        'sort': [{'release_date': {'order': 'desc'}}],
        '_source': ['region', 'href', 'title', 'release_date', 'amount_wan', 'bid_deadline',
                    'announce_type', 'win_company', 'win_amount_wan', 'void_reason',
                    'contact_person', 'contact_phone', 'contact_subject'],
    }
    r = es.search(index='tenders', body=body)
    return r['hits']['hits']


def build_rows(hits, keyword):
    """分派招标/中标/废标，关联后输出招标项目行。"""
    tenders, wins, voids = [], [], []
    for h in hits:
        s = h['_source']
        at = s.get('announce_type', '')
        (wins if at == '中标公告' else voids if at == '废标公告' else tenders).append(s)

    # 关联索引：编号 -> 中标/废标
    win_by_code, void_by_code = {}, {}
    win_by_title, void_by_title = {}, {}
    for w in wins:
        c = extract_code(w.get('title'))
        if c:
            win_by_code.setdefault(c, w)
        win_by_title.setdefault(normalize_title(w.get('title')), w)
    for v in voids:
        c = extract_code(v.get('title'))
        if c:
            void_by_code.setdefault(c, v)
        void_by_title.setdefault(normalize_title(v.get('title')), v)

    rows = []
    for t in tenders or []:
        code = extract_code(t.get('title'))
        key = normalize_title(t.get('title'))
        w = win_by_code.get(code) if code else None
        w = w or win_by_title.get(key)
        v = void_by_code.get(code) if code else None
        v = v or void_by_title.get(key)
        rows.append({
            '关键词': keyword,
            '项目标题': t.get('title', ''),
            '招标链接': t.get('href', ''),
            '地区': t.get('region', ''),
            '发布日期': (t.get('release_date') or '')[:10],
            '投标截止': t.get('bid_deadline', ''),
            '项目金额(万)': t.get('amount_wan'),
            '中标单位': (w or {}).get('win_company', ''),
            '中标金额(万)': (w or {}).get('win_amount_wan'),
            '中标时间': ((w or {}).get('release_date') or '')[:10],
            '中标公告链接': (w or {}).get('href', ''),
            '废标原因': (v or {}).get('void_reason', ''),
            '联系人': (w or t).get('contact_person', '') or t.get('contact_person', ''),
            '联系电话': (w or t).get('contact_phone', '') or t.get('contact_phone', ''),
            '联系方式主体': (w or {}).get('contact_subject', '') or t.get('contact_subject', ''),
        })
    # 无招标公告但命中的中标/废标也列出（避免丢失）
    covered = {r['招标链接'] for r in rows}
    for w in wins:
        if w.get('href') in covered:
            continue
        rows.append({
            '关键词': keyword, '项目标题': w.get('title', ''), '招标链接': '',
            '地区': w.get('region', ''), '发布日期': (w.get('release_date') or '')[:10],
            '投标截止': '', '项目金额(万)': None,
            '中标单位': w.get('win_company', ''), '中标金额(万)': w.get('win_amount_wan'),
            '中标时间': (w.get('release_date') or '')[:10], '中标公告链接': w.get('href', ''),
            '废标原因': '', '联系人': w.get('contact_person', ''),
            '联系电话': w.get('contact_phone', ''), '联系方式主体': w.get('contact_subject', ''),
        })
    return rows


def write_outputs(keyword, rows, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    df = pd.DataFrame(rows)
    xlsx = os.path.join(out_dir, 'result.xlsx')
    df.to_excel(xlsx, index=False)
    # Markdown 汇总
    lines = [f'# 关键词「{keyword}」商机检索结果', '',
             f'- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}',
             f'- 命中条数：{len(rows)}',
             f'- 有中标结果：{sum(1 for r in rows if r["中标单位"])}',
             f'- 有废标信息：{sum(1 for r in rows if r["废标原因"])}', '']
    if rows:
        from collections import Counter
        reg = Counter(r['地区'] for r in rows)
        lines += ['## 地区分布', ''] + [f'- {k}: {v}' for k, v in reg.most_common(10)] + ['']
        top = sorted([r for r in rows if r.get('项目金额(万)')], key=lambda x: -x['项目金额(万)'])[:10]
        if top:
            lines += ['## 金额 Top10', '', '| 项目 | 地区 | 金额(万) | 截止 |', '|---|---|---|---|']
            lines += [f"| {r['项目标题'][:40]} | {r['地区']} | {r['项目金额(万)']} | {r['投标截止']} |" for r in top]
            lines.append('')
    md = os.path.join(out_dir, 'summary.md')
    with open(md, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return xlsx, md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('keyword')
    ap.add_argument('--start', default='')
    ap.add_argument('--end', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--size', type=int, default=2000)
    args = ap.parse_args()

    start, end = None, None
    if not args.all:
        end = args.end or date.today().isoformat()
        start = args.start or (date.today() - timedelta(days=90)).isoformat()
    hits = search(args.keyword, start, end, args.size)
    rows = build_rows(hits, args.keyword)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_kw = re.sub(r'[^\w\u4e00-\u9fa5-]', '_', args.keyword)[:30]
    out_dir = os.path.join(OUTPUT_BASE, f'{safe_kw}_{stamp}')
    xlsx, md = write_outputs(args.keyword, rows, out_dir)
    scope = '全库' if args.all else f'{start}~{end}'
    print(f'关键词「{args.keyword}」范围[{scope}] 命中 {len(hits)} 条，导出 {len(rows)} 行')
    print(f'  Excel: {xlsx}')
    print(f'  汇总:  {md}')


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 编译**

Run: `.venv/bin/python -m py_compile scripts/search_tenders.py`
Expected: 无输出

- [ ] **Step 3: 实跑验证（关键词"服务器"，近3个月）**

Run: `.venv/bin/python scripts/search_tenders.py "服务器"`
Expected: 打印命中条数 >0 和导出路径；Excel 列齐全

- [ ] **Step 4: 验证"正文命中也能召回"**

Run:
```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0,'.'); sys.path.insert(0,'scripts')
from search_tenders import search
# 选一个正文才出现、标题不含的词（如"内存条"）验证正文检索
for kw in ['服务器','内存条']:
    hits=search(kw, '2026-09-01', '2026-11-22', 100)
    title_hit=sum(1 for h in hits if kw in h['_source'].get('title',''))
    print(f"{kw}: 命中{len(hits)}，其中标题含词{title_hit}（差额即正文命中）")
PY
```
Expected: "内存条"命中数 > 标题含词数（证明正文检索生效）

- [ ] **Step 5: 验证关联与字段**

Run:
```bash
.venv/bin/python - <<'PY'
import pandas as pd, glob, os
f=sorted(glob.glob('output/关键词检索/*/result.xlsx'))[-1]
df=pd.read_excel(f)
print('列:', list(df.columns))
print('行数:', len(df))
print('有中标单位行:', df['中标单位'].notna().sum())
print('有联系人行:', df['联系人'].notna().sum())
print(df[['项目标题','中标单位','中标金额(万)','联系人','联系电话']].head(5).to_string())
PY
```
Expected: 列齐全；有中标单位/联系人行数 >0；样本可见中标单位或联系人

- [ ] **Step 6: Commit**

```bash
git add scripts/search_tenders.py
git commit -m "feat(search): 关键词商机检索（标题+正文加权+中标/废标关联+Excel/MD导出）"
```

---

## Task 5: 更新项目功能说明文档

**Files:**
- Modify: `docs/项目功能说明.md`

- [ ] **Step 1: 追加最新能力章节**

在文档末尾追加（保持原有内容）：

```markdown
## 最新能力（2026-11-22）

### 爬虫覆盖
- 招标采集：29 个地区（crawler/<region>.py）
- 结果公告采集：20 个地区（crawler/result_*.py，新增 gansu/hlj；maincms 平台共用 result_maincms.py 基类）
- 每日定时任务（Schedule 8f6dc4dd）：29 地区招标 + 18 地区结果公告，自动汇总分析

### 数据字段（ES alias tenders → tenders_v3，IK 分词）
region/href/title/release_date/crawl_date/amount_wan/bid_deadline/
announce_type(招标/中标/废标公告)/win_company/win_amount_wan/void_reason/
contact_person/contact_phone/contact_subject/html

### 检索与分析
- IK 中文分词，title 与 html（正文）均可全文检索
- 关键词商机检索：`scripts/search_tenders.py "关键词"`（标题^3+正文加权，关联中标/废标，导出 Excel+MD）
- 窗口期商机查询：`scripts/query_opportunities.py`（按投标截止时间窗口）

### 运维能力
- 防风控层 utils/throttle.py（令牌桶/退避/熔断/自适应降速）
- 历史缺口补抓：scripts/backfill_gaps.py
- 结果公告补抓：scripts/backfill_results.py
- 骨架页回填：scripts/backfill_skeleton.py
- 字段回填：scripts/backfill_announce_type.py（--meta/--void/--contact）
```

- [ ] **Step 2: 提交**

```bash
git add docs/项目功能说明.md
git commit -m "docs: 更新项目功能说明（爬虫覆盖/字段/检索/运维能力）"
```

---

## 验收标准

1. `search_tenders.py "服务器"` 命中并导出（Excel + MD），列含：项目标题、招标链接、中标单位、中标金额、中标公告链接、废标原因、联系人、联系电话、联系方式主体
2. 正文命中可召回（"内存条"类词命中数 > 标题命中数）
3. 结果按发布日期降序；默认近 3 个月，`--all` 全库
4. 联系人优先中标供应商、兜底采购人，并有主体标注
5. 功能说明文档已更新

## 已知限制

- 招标↔中标关联无项目编号字段，靠编号正则+标题归一化，可能有个别漏关联（不强关联则不展示中标信息）
- 联系人/电话多数公告只披露采购人/代理机构，中标供应商联系方式提取率偏低
- 单次检索 size 默认 2000，超大关键词可调 `--size`
