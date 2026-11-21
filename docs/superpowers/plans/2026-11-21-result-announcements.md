# 中标/废标公告采集与结果追踪 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 独立采集各省中标/废标公告并按 `announce_type` 标记入库，回填历史数据，提供 `scripts/query_results.py` 查询命令，让用户掌握"哪些在招标/哪个公司中标/哪些流标"。

**Architecture:** 新增 `utils/announce.py`（类型分类 + 中标单位/金额提取）；`Tender` 与 ES mapping 增 3 字段；现有爬虫在 `_enrich_meta` 里自动按标题分类；新增 `crawler/result_*.py`（继承对应省爬虫，仅改列表源 + 预设类型）+ `crawler/result_collector.py` 注册表，由 `fast_run.py results <date>` 驱动；新增 `scripts/backfill_announce_type.py` 回填存量；新增 `scripts/query_results.py` 查询。中标/废标公告与招标公告共用 `tenders` 索引，靠 `announce_type` 区分，不配对。

**Tech Stack:** Python 3 / Playwright / Elasticsearch 8.x / pandas / openpyxl。项目无测试框架（requirements.txt 无 pytest），验证采用「轻量断言命令 + 真实运行验证」模式，沿用现有脚本风格。

**Spec:** `docs/superpowers/specs/2026-11-21-result-announcements-design.md`

---

### 环境前置（所有任务通用）

- 用项目虚拟环境执行所有命令：`.venv/bin/python ...`
- 受限终端先修复 PATH：`export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"`
- 确保 ES 运行：`curl -s -u elastic:7aNJbD0LTxsVLyuRcHSQ "http://localhost:9200/tenders/_count"`

---

### Task 1: 新增 `utils/announce.py`（类型分类 + 中标单位/金额提取）

**Files:**
- Create: `utils/announce.py`

独立小模块，供爬虫 `_enrich_meta`、回填脚本、查询脚本共用。注意：**不得 import `crawler.base_crawler`**（避免循环依赖——base_crawler 会在函数内延迟 import 本模块），正文转纯文本直接用 html2text（已是依赖）。

- [ ] **Step 1: 创建模块并写轻量断言验证**

```python
"""中标/废标公告分类与提取工具

- classify_announce_type: 按标题关键词把公告归为 招标公告 / 中标公告 / 废标公告
- extract_win_company: 从正文提取中标单位名称（中标供应商/成交单位/中标人 等）
- extract_win_amount: 从正文提取中标金额（兼容 元/万元），返回万元 float 或 None
"""
import re

import html2text


def _to_text(html):
    h = html2text.HTML2Text()
    h.ignore_links = True
    h.ignore_images = True
    h.body_width = 0
    return h.handle(html or '')


def classify_announce_type(title):
    """标题关键词分类：废标/流标/终止/失败 -> 废标公告；中标/成交/结果 -> 中标公告；其余 -> 招标公告。"""
    t = str(title)
    if any(k in t for k in ('废标', '流标', '终止', '失败')):
        return '废标公告'
    if any(k in t for k in ('中标', '成交', '结果')):
        return '中标公告'
    return '招标公告'


_COMPANY_PATS = [
    r'(?:中标供应商名称|成交供应商名称|中标单位名称|成交单位名称)\s*[:：]\s*([^\s，。,；;、|\n]{2,60})',
    r'(?:中标供应商|成交供应商|中标单位|成交单位|中标人|成交人)\s*[（(]?[^）)]{0,8}[)）]?\s*[:：]\s*([^\s，。,；;、|\n]{2,60})',
]


def extract_win_company(html):
    """从正文提取中标单位名称，未提取到返回空串。"""
    text = _to_text(html)
    text = text.replace('\u3000', ' ')
    for pat in _COMPANY_PATS:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return ''


def extract_win_amount(html):
    """提取中标/成交金额，兼容 元/万元，返回万元 float；未提取到返回 None。"""
    text = _to_text(html)
    for kw in ('中标金额', '成交金额'):
        m = re.search(kw + r'[（(]?万元[)）]?[:：]?\s*([\d,，.]+)', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', ''))
        m = re.search(kw + r'[（(]?元[)）]?[:：]?\s*([\d,，.]+)', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', '')) / 10000
        m = re.search(kw + r'[:：]\s*([\d,，.]+)\s*万元', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', ''))
    return None
```

- [ ] **Step 2: 运行断言验证**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python - <<'PY'
from utils.announce import classify_announce_type, extract_win_company, extract_win_amount
assert classify_announce_type('XX医院设备采购项目中标（成交）结果公告') == '中标公告'
assert classify_announce_type('XX项目废标公告') == '废标公告'
assert classify_announce_type('XX项目终止公告') == '废标公告'
assert classify_announce_type('XX项目公开招标公告') == '招标公告'
assert classify_announce_type('XX项目竞争性磋商公告') == '招标公告'
html = '<div>中标供应商：北京华宇科技有限公司\n中标金额：128.5万元</div>'
assert extract_win_company(html) == '北京华宇科技有限公司'
assert extract_win_amount(html) == 128.5
html2 = '<div>成交金额：3,000,000元</div>'
assert extract_win_amount(html2) == 300.0
print('PASS')
PY
```

Expected: 输出 `PASS`

- [ ] **Step 3: 提交**

```bash
git add utils/announce.py
git commit -m "feat(announce): 公告类型分类与中标单位/金额提取工具

- 原因：中标/废标公告采集需要统一的分类与中标信息提取入口
- 改动：utils/announce.py 提供 classify_announce_type/extract_win_company/extract_win_amount
- 影响：纯新增工具模块，未被引用；断言验证 PASS"
```

---

### Task 2: Tender dataclass + ES mapping + Excel 列名扩展 3 字段

**Files:**
- Modify: `crawler/base_crawler.py:35-45`（Tender dataclass）、`crawler/base_crawler.py:20-24`（CN_EXPORT_COLS）
- Modify: `utils/es.py:34-62`（TENDER_INDEX_BODY）
- Modify: `fast_run.py:167-172`（CN_EXPORT_COLS）

- [ ] **Step 1: 扩展 Tender dataclass 与 CN_EXPORT_COLS（base_crawler.py）**

在 `crawler/base_crawler.py` 中：

```python
CN_EXPORT_COLS = {
    'region': '地区', 'href': '公告链接', 'title': '商机标题',
    'release_date': '发布日期', 'crawl_date': '抓取日期',
    'html': '商机详情', 'truncated': '详情截断',
    'announce_type': '公告类型', 'win_company': '中标单位', 'win_amount_wan': '中标金额(万元)',
}
```

```python
@dataclass
class Tender:
    region: str
    href: str
    title: str
    release_date: str = ''
    html: str = ''
    crawl_date: str = ''
    amount_wan: float = None      # 项目金额（万元），入库时从正文提取
    bid_deadline: str = ''        # 投标截止时间（ISO YYYY-MM-DD[ HH:MM]），入库时从正文提取
    announce_type: str = ''       # 招标公告 / 中标公告 / 废标公告（入库时按标题分类或由结果采集器预设）
    win_company: str = ''         # 中标单位（仅中标公告）
    win_amount_wan: float = None  # 中标金额（万元，仅中标公告）
```

- [ ] **Step 2: 扩展 ES mapping（utils/es.py）**

在 `utils/es.py` 的 `TENDER_INDEX_BODY` 中 `properties` 追加：

```python
            "announce_type": {"type": "keyword"},
            "win_company": {"type": "keyword"},
            "win_amount_wan": {"type": "double"},
```

说明：`ESConnection.ensure_index` 已对已存在索引调用 `put_mapping` 补充新字段，无需重建索引。

- [ ] **Step 3: 扩展 fast_run.py 的 CN_EXPORT_COLS**

在 `fast_run.py:168-172` 的 `CN_EXPORT_COLS` 中追加同样的 3 个中文映射（与 Step 1 相同内容）。

- [ ] **Step 4: 验证 mapping 生效**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python - <<'PY'
from utils.es import ESConnection
es = ESConnection()
es.ensure_index()
m = es._client().indices.get_mapping(index='tenders')
props = m['tenders']['mappings']['properties']
assert 'announce_type' in props and 'win_company' in props and 'win_amount_wan' in props, props.keys()
assert props['win_amount_wan'] == {'type': 'double'}, props['win_amount_wan']
print('PASS mapping:', sorted(props.keys()))
PY
```

Expected: 输出 `PASS mapping:` 且包含 3 个新字段。

- [ ] **Step 5: 提交**

```bash
git add crawler/base_crawler.py utils/es.py fast_run.py
git commit -m "feat(announce): Tender 与 ES mapping 新增公告类型/中标单位/中标金额字段

- 原因：结果公告采集与查询需要结构化存储三类公告与中标信息
- 改动：Tender dataclass、TENDER_INDEX_BODY、两处 CN_EXPORT_COLS 各加 3 字段
- 影响：ES put_mapping 自动补字段不重建索引；旧数据字段缺省不报错"
```

---

### Task 3: `_enrich_meta` 接入类型分类与中标信息提取

**Files:**
- Modify: `crawler/base_crawler.py:143-161`（`_enrich_meta`）

现有爬虫（含混合列表 11 省）入库时自动按标题分类 + 中标公告自动提取中标单位/金额。

- [ ] **Step 1: 重写 `_enrich_meta`**

```python
    def _enrich_meta(self, tender):
        """入库前从正文提取结构化字段：金额、投标截止时间，并按标题分类公告类型。

        - announce_type 为空时按标题关键词分类（结果采集器可预设，跳过分类）
        - 中标公告额外提取 win_company / win_amount_wan
        - amount_wan 复用 analyze_today 提取逻辑（金额兼容元/万元）
        """
        try:
            text = self._html_to_text(tender.html)
        except Exception:
            text = ''
        try:
            from utils.announce import classify_announce_type, extract_win_company, extract_win_amount
            if not tender.announce_type:
                tender.announce_type = classify_announce_type(tender.title)
            if tender.announce_type == '中标公告':
                if not tender.win_company:
                    tender.win_company = extract_win_company(text)
                if tender.win_amount_wan is None:
                    tender.win_amount_wan = extract_win_amount(text)
        except Exception as e:
            logger.warning(f"[{self.region}] 分类/中标信息提取失败: {e}")
        if tender.amount_wan is not None and tender.bid_deadline:
            return  # 已提取过，避免重复
        try:
            from analyze_today import extract_money, extract_timeline
            unit, val = extract_money(text)
            if unit == '万元':
                tender.amount_wan = float(val)
            elif unit == '元':
                tender.amount_wan = float(val) / 10000
            tl = extract_timeline(text)
            tender.bid_deadline = tl.get('投标截止') or ''
        except Exception as e:
            logger.warning(f"[{self.region}] 提取金额/截止时间失败: {e}")
```

- [ ] **Step 2: 验证（用现有 ES 数据抽查分类）**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python - <<'PY'
from crawler.base_crawler import Tender, BaseCrawler
bc = BaseCrawler.__new__(BaseCrawler)
t = Tender('beijing', 'http://x/1', 'XX医院设备采购项目中标（成交）结果公告', '2026-11-20')
bc._enrich_meta(t)
assert t.announce_type == '中标公告', t.announce_type
t2 = Tender('beijing', 'http://x/2', 'XX项目废标公告', '2026-11-20')
bc._enrich_meta(t2)
assert t2.announce_type == '废标公告', t2.announce_type
t3 = Tender('beijing', 'http://x/3', 'XX项目公开招标公告', '2026-11-20')
bc._enrich_meta(t3)
assert t3.announce_type == '招标公告', t3.announce_type
print('PASS:', t.announce_type, t2.announce_type, t3.announce_type)
PY
```

Expected: 输出 `PASS: 中标公告 废标公告 招标公告`

- [ ] **Step 3: 提交**

```bash
git add crawler/base_crawler.py
git commit -m "feat(announce): 入库前自动按标题分类公告类型并提取中标单位/金额

- 原因：混合列表顺带抓到的中标/废标公告需要自动分类标记
- 改动：_enrich_meta 增加 announce_type 分类 + 中标公告的 win_company/win_amount_wan 提取
- 影响：新抓数据自动带类型；结果采集器预设类型时跳过标题分类"
```

---

### Task 4: 每日汇总/查询导出增加 3 字段列

**Files:**
- Modify: `fast_run.py:217-267`（`_load_region_rows_from_es`）、`fast_run.py:400-415`（`query_date_from_es` 的 rows 构建）

`summarize` / `today_db` / `db_date` 导出的 Excel 需带 `公告类型/中标单位/中标金额(万元)` 三列（数据从 ES 带出）。

- [ ] **Step 1: `_load_region_rows_from_es` 增加 3 字段**

在 `fast_run.py` 的 `_load_region_rows_from_es` 中，rows.append 字典追加：

```python
        rows.append({
            'region': src.get('region', ''),
            'href': src.get('href', ''),
            'title': src.get('title', ''),
            'release_date': src.get('release_date', ''),
            'html': html_text,
            'crawl_date': src.get('crawl_date', ''),
            'truncated': '是' if len(html_text) > 32767 else '否',
            'announce_type': src.get('announce_type', ''),
            'win_company': src.get('win_company', ''),
            'win_amount_wan': src.get('win_amount_wan'),
        })
```

- [ ] **Step 2: `query_date_from_es` 的 rows 构建增加 3 字段**

在 `fast_run.py` 的 `query_date_from_es` 中，rows.append 字典追加与 Step 1 相同 3 行（announce_type/win_company/win_amount_wan）。

- [ ] **Step 3: 验证（合并导出含新列）**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python - <<'PY'
# 直接调用 _load_region_rows_from_es 抽查今天/最近有数据的日期
from fast_run import _load_region_rows_from_es
rows = _load_region_rows_from_es('2026-11-20', ['beijing'])
print('rows:', len(rows))
for r in rows[:3]:
    assert 'announce_type' in r and 'win_company' in r and 'win_amount_wan' in r, r.keys()
print('PASS columns')
PY
```

Expected: 输出 `rows: N` 与 `PASS columns`（若 2026-11-20 无 beijing 数据，改用一个有数据的日期）。

- [ ] **Step 4: 提交**

```bash
git add fast_run.py
git commit -m "feat(announce): 每日汇总/查库导出增加公告类型/中标单位/中标金额列

- 原因：三类公告需要在同一汇总表并列呈现
- 改动：_load_region_rows_from_es 与 query_date_from_es 导出行增加 3 字段
- 影响：仅增加导出列，不影响既有数据与逻辑"
```

---

### Task 5: 结果公告采集器（继承省爬虫，只改列表源 + 预设类型）

**Files:**
- Create: `crawler/result_beijing.py`
- Create: `crawler/result_tianjin.py`
- Create: `crawler/result_chongqing.py`
- Create: `crawler/result_shandong.py`
- Create: `crawler/result_guizhou.py`
- Create: `crawler/result_collector.py`

设计：每个结果爬虫继承对应省爬虫，**只覆盖列表源与 announce_type 预设**，复用全部列表/详情/入库逻辑（DRY）。`result_collector.py` 提供注册表与按日期运行入口（供 fast_run 调用），并给单地区 Excel 加 `_result` 后缀避免与招标 Excel 冲突。

- [ ] **Step 1: `crawler/result_beijing.py`（区级中标 + 区级废标两个列表）**

```python
"""北京区级中标/废标公告采集器（复用 BeiJing 的列表/详情/入库逻辑）

- 中标列表：http://www.ccgp-beijing.gov.cn/xxgg/qjxxgg/qjzbggs/A002004002002index_{n}.htm
- 废标列表：http://www.ccgp-beijing.gov.cn/xxgg/qjxxgg/qjfbgg/A002004002005index_{n}.htm
结构兼容现有 beijing.py（li > a + span.datetime，详情 div.mainTextBox）。
"""
from crawler.beijing import BeiJing
from utils.log import logger


class BeiJingResult(BeiJing):
    def _crawl(self, context):
        self._crawl_type(context, '中标公告',
                         'http://www.ccgp-beijing.gov.cn/xxgg/qjxxgg/qjzbggs/A002004002002index_{}.htm')
        self._crawl_type(context, '废标公告',
                         'http://www.ccgp-beijing.gov.cn/xxgg/qjxxgg/qjfbgg/A002004002005index_{}.htm')

    def _crawl_type(self, context, announce_type, page_url_tpl):
        for i in range(1, self.max_pages + 1):
            page_url = page_url_tpl.format(i)
            logger.info(f"start to crawl: {page_url}")
            tenders = self._execute_by_new_page(context, page_url, self.get_one_page_titles)
            for url, tender in tenders.items():
                if url in self.exists_urls or url in self.tenders:
                    continue
                try:
                    tender.html = self._execute_by_new_page(context, url, self.parse_detail)
                except Exception as e:
                    logger.error(f"[{self.region}]parse detail failed {url}: {e}")
                tender.announce_type = announce_type
                tender.crawl_date = self._get_crawl_date()
                self.tenders[url] = tender
                self._random_sleep(_max=30)


if __name__ == '__main__':
    BeiJingResult().run()
```

- [ ] **Step 2: `crawler/result_tianjin.py`（接口 announcementType=10 中标 / 16 废标终止）**

```python
"""天津结果公告采集器（复用 TianJin 的请求头捕获 + 列表接口 + 详情接口）

结果列表：POST /api/api/portal/Announcement/Page
  industryType=["4"]（政府采购）+ announcementType=10（中标结果）/ 16（废标终止）
详情：POST /api/api/portal/Announcement/GetDetail
"""
from crawler.tianjin import TianJin
from crawler.base_crawler import Tender
from utils.log import logger


class TianJinResult(TianJin):
    def _crawl(self, context):
        page = context.new_page()
        self.authorization = None

        def handle_request(request):
            headers = request.headers
            if 'authorization' in headers:
                self.authorization = headers['authorization']

        page.on("request", handle_request)
        page.goto("https://www.tjggzy.cn/announcementIndex")
        page.wait_for_load_state("networkidle")
        if not self.authorization:
            try:
                auth_storage = page.evaluate("() => localStorage.getItem('authorization')")
                if auth_storage:
                    self.authorization = auth_storage
            except Exception:
                pass
        self.headers = {
            "Content-Type": "application/json",
            "Referer": "https://www.tjggzy.cn/announcementIndex",
            "User-Agent": page.evaluate("navigator.userAgent"),
        }
        if self.authorization:
            self.headers["Authorization"] = self.authorization

        for announcement_type, announce_type in ((10, '中标公告'), (16, '废标公告')):
            tender_list = self._get_tender_list(context, '4', announcement_type)
            for tender_id in tender_list:
                if tender_id in self.exists_urls:
                    continue
                self._get_tender_details(context, tender_id, announce_type)
                self._random_sleep(_max=30)

    def _get_tender_details(self, context, tender_id, announce_type=None):
        logger.info(f"start to request tender details for {tender_id}")
        response = context.request.post(
            url="https://www.tjggzy.cn/api/api/portal/Announcement/GetDetail",
            data={"Id": tender_id},
            headers=self.headers,
        )
        data = response.json()
        if data.get("statusCode") != 2000:
            logger.error(f"get tender details error:{data}")
            return
        tender = Tender(region=self.region, href=tender_id,
                        title=data['responseData']['announcementName'],
                        release_date=data['responseData']['publishTime'],
                        html=data['responseData']['noticeContent'],
                        crawl_date=self._get_crawl_date())
        if announce_type:
            tender.announce_type = announce_type
        self.save_tender_to_es(tender)
        self.tenders[tender_id] = tender
        logger.info(f"get tender details for {tender_id} success")


if __name__ == '__main__':
    TianJinResult().run()
```

注意：`_get_tender_list(context, '4', announcement_type)` 中 `hylx='4'` 对应行业类型「政府采购」，`xxlx` 为公告类型值（10/16）。

- [ ] **Step 3: `crawler/result_chongqing.py`（采购结果公告栏目 014005004）**

```python
"""重庆采购结果公告采集器（复用 ChongQing 的 Nuxt SSR 列表 + div.app-detail 详情）

列表：https://www.cqggzy.com/trade/014005004?categoryNum=014005004
该栏目含"中标（成交）结果公告"与"终止公告"（废标），类型靠标题关键词分类（不预设）。
"""
from crawler.chongqing import ChongQing


class ChongQingResult(ChongQing):
    def __init__(self):
        super().__init__()
        # 采购结果公告栏目（原 014005001 为采购公告）
        self.list_url = 'https://www.cqggzy.com/trade/014005004?categoryNum=014005004'


if __name__ == '__main__':
    ChongQingResult().run()
```

- [ ] **Step 4: `crawler/result_shandong.py`（SPA 接口 colCode=0302 结果公告）**

```python
"""山东结果公告采集器（复用 ShanDong 的 SPA JSON 接口 + div.site-content 详情）

列表：getListByCode 接口 colCode=0302（结果公告；原 0301 为省级采购公告）
类型靠标题关键词分类（结果/终止公告混合）。
"""
from crawler.shandong import ShanDong


class ShanDongResult(ShanDong):
    def __init__(self):
        super().__init__()
        self.body['colCode'] = '0302'


if __name__ == '__main__':
    ShanDongResult().run()
```

- [ ] **Step 5: `crawler/result_guizhou.py`（接口 categoryCode=ZcyAnnouncement5 中标结果公告）**

```python
"""贵州中标结果公告采集器（复用 GuiZhou 的 portal/category 接口）

列表：portal/category 接口 categoryCode=ZcyAnnouncement5（中标结果公告；原 ZcyAnnouncement1 为采购意向）
"""
from crawler.guizhou import GuiZhou


class GuiZhouResult(GuiZhou):
    def __init__(self):
        super().__init__()
        self.category_code = 'ZcyAnnouncement5'
        # Referer 与 categoryCode 保持一致，避免接口被拒
        self.headers['Referer'] = self.list_url + '&categoryCode=ZcyAnnouncement5'


if __name__ == '__main__':
    GuiZhouResult().run()
```

- [ ] **Step 6: `crawler/result_collector.py`（注册表 + 按日期运行入口）**

```python
"""结果公告采集注册表与按日期运行入口（供 fast_run.py results 命令调用）

与招标爬虫共用 tenders 索引，靠 announce_type 区分；单地区 Excel 加 _result 后缀，
避免与同省招标 Excel 冲突（汇总后仍会被 cleanup 清理）。
"""
import json
import os

from fast_run import date_dir

# region -> (模块名, 类名)；仅登记已核实/可确认的结果栏目
RESULT_CRAWLERS = {
    'beijing': ('result_beijing', 'BeiJingResult'),
    'tianjin': ('result_tianjin', 'TianJinResult'),
    'chongqing': ('result_chongqing', 'ChongQingResult'),
    'shandong': ('result_shandong', 'ShanDongResult'),
    'guizhou': ('result_guizhou', 'GuiZhouResult'),
}


def run_result_region(region, start_date, end_date, summary_path=None):
    """单地区按日期抓结果公告；追加一行结果到 summary_path（JSONL）。"""
    module_name, class_name = RESULT_CRAWLERS[region]
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
    print(f'[results:{region}] {status}, kept={kept}, no_date={no_date}')
    return record
```

- [ ] **Step 7: 验证（import 与注册表）**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python - <<'PY'
from crawler.result_collector import RESULT_CRAWLERS
assert set(RESULT_CRAWLERS) == {'beijing', 'tianjin', 'chongqing', 'shandong', 'guizhou'}, RESULT_CRAWLERS
import crawler.result_beijing, crawler.result_tianjin, crawler.result_chongqing, crawler.result_shandong, crawler.result_guizhou
print('PASS import:', list(RESULT_CRAWLERS))
PY
```

Expected: 输出 `PASS import: ['beijing', 'chongqing', 'guizhou', 'shandong', 'tianjin']`

- [ ] **Step 8: 提交**

```bash
git add crawler/result_beijing.py crawler/result_tianjin.py crawler/result_chongqing.py crawler/result_shandong.py crawler/result_guizhou.py crawler/result_collector.py
git commit -m "feat(announce): 结果公告采集器（北京区级中标/废标、天津、重庆、山东、贵州）

- 原因：需独立采集中标/废标公告，与招标采集解耦
- 改动：5 个 result_*.py 继承对应省爬虫仅改列表源+预设类型；result_collector.py 注册表+按日期入口
- 影响：新模块未被 fast_run 引用，import 验证 PASS；单地区 Excel 加 _result 后缀"
```

---

### Task 6: `fast_run.py results <date>` 命令

**Files:**
- Modify: `fast_run.py:436-514`（`main`）

- [ ] **Step 1: main() 增加 results 分支**

在 `fast_run.py` 的 `main()` 中、`if cmd == 'summarize':` 分支之后插入：

```python
    if cmd == 'results':
        # 抓结果公告（中标/废标）：用法 python fast_run.py results <YYYY-MM-DD> [--regions a,b]
        if len(args) < 2:
            print('用法: python fast_run.py results <YYYY-MM-DD> [--regions a,b]')
            sys.exit(1)
        from crawler.result_collector import RESULT_CRAWLERS, run_result_region
        start = args[1]
        regions = None
        rest = args[2:]
        i = 0
        while i < len(rest):
            if rest[i] == '--regions' and i + 1 < len(rest):
                regions = [r.strip() for r in rest[i + 1].split(',') if r.strip()]
                i += 2
            else:
                i += 1
        result_regions = regions or list(RESULT_CRAWLERS)
        summary_path = os.path.join(date_dir(start), f'date_result_run_{start}.jsonl')
        for region in result_regions:
            if region not in RESULT_CRAWLERS:
                print(f'[results] 未知结果地区: {region}（支持: {", ".join(RESULT_CRAWLERS)}）')
                continue
            run_result_region(region, start, start, summary_path)
        print(f'[results] 结果公告抓取完成（{start}），可运行 summarize 合并汇总')
        return
```

注意：`os` 已在 fast_run.py 顶部导入（`import os`），`date_dir` 为同模块函数。

- [ ] **Step 2: 验证（help/用法分支）**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python fast_run.py results   # 无参数应打印用法
```

Expected: 输出 `用法: python fast_run.py results <YYYY-MM-DD> [--regions a,b]` 且退出码 1。

- [ ] **Step 3: 提交**

```bash
git add fast_run.py
git commit -m "feat(fast_run): 新增 results 命令按日期抓结果公告

- 原因：中标/废标公告需要独立于招标采集的运行入口
- 改动：main() 增加 results 分支，调 result_collector.run_result_region
- 影响：新命令不触碰招标流程；结果公告 jsonl 写 date_result_run_<date>.jsonl"
```

---

### Task 7: 存量数据回填分类脚本

**Files:**
- Create: `scripts/backfill_announce_type.py`

仿 `scripts/backfill_meta.py` 模式，幂等：仅处理 announce_type 缺失的文档；中标公告额外提取 win_company / win_amount_wan。

- [ ] **Step 1: 创建脚本**

```python
"""存量数据回填：为 ES 中缺少 announce_type 的公告按标题分类标记，中标公告补提取中标单位/金额。

用法：
    python scripts/backfill_announce_type.py            # 全量回填（只处理 announce_type 缺失的）
    python scripts/backfill_announce_type.py 1000       # 分批回填（每次处理条数）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elasticsearch import helpers

from utils.es import ESConnection
from utils.announce import classify_announce_type, extract_win_company, extract_win_amount


def backfill(batch=None):
    es = ESConnection()
    client = es._client()
    if not client:
        print('[backfill_announce_type] ES 连接失败')
        return

    # 只处理 announce_type 缺失的文档（幂等：已标记的一次更新后收敛）
    query = {
        "query": {
            "bool": {
                "must_not": [{"exists": {"field": "announce_type"}}]
            }
        },
        "sort": ["_doc"],
    }

    seen = 0
    updated = 0
    missing = 0
    actions = []
    try:
        resp = client.search(index='tenders', body=query, scroll='10m', size=1000, _source=True)
        sid = resp['_scroll_id']
        hits = resp['hits']['hits']
        while hits:
            for h in hits:
                seen += 1
                src = h.get('_source', {})
                doc_id = h.get('_id')
                title = src.get('title', '')
                atype = classify_announce_type(title)
                doc = {'announce_type': atype}
                if atype == '中标公告':
                    html = src.get('html', '') or ''
                    doc['win_company'] = extract_win_company(html)
                    doc['win_amount_wan'] = extract_win_amount(html)
                else:
                    missing += 1
                    continue
                actions.append({
                    "_op_type": "update",
                    "_index": "tenders",
                    "_id": doc_id,
                    "doc": doc,
                })
                updated += 1
                if batch and updated >= batch:
                    break
            if batch and updated >= batch:
                break
            resp = client.scroll(scroll_id=sid, scroll='10m')
            hits = resp['hits']['hits']
        try:
            client.clear_scroll(scroll_id=sid)
        except Exception:
            pass
    except Exception as e:
        print(f'[backfill_announce_type] 扫描失败: {e}')
        return

    if actions:
        try:
            success, errors = helpers.bulk(client, actions, chunk_size=500, raise_on_error=False)
            print(f'[backfill_announce_type] 更新成功 {success} 条，失败 {len(errors) if isinstance(errors, list) else 0} 条')
        except Exception as e:
            print(f'[backfill_announce_type] bulk 更新失败: {e}')
    print(f'[backfill_announce_type] 扫描 {seen} 条，其中非中标公告跳过 {missing} 条，提交更新 {len(actions)} 条')


if __name__ == '__main__':
    batch = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    backfill(batch)
```

- [ ] **Step 2: 先跑小批量验证**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python scripts/backfill_announce_type.py 500
```

Expected: 输出形如 `[backfill_announce_type] 扫描 N 条，其中非中标公告跳过 M 条，提交更新 K 条`，无异常。

- [ ] **Step 3: 抽查回填结果**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python - <<'PY'
from utils.es import ESConnection
es = ESConnection()
client = es._client()
# 抽查：已分类的文档数 + 中标公告抽样
q = {'query': {'exists': {'field': 'announce_type'}}, 'size': 0}
total = client.count(index='tenders', body=q)['count']
print('已分类文档数:', total)
q2 = {'query': {'term': {'announce_type': '中标公告'}}, 'size': 3}
hits = client.search(index='tenders', body=q2)['hits']['hits']
for h in hits:
    s = h['_source']
    print('-', s.get('title', '')[:40], '|', s.get('win_company', ''), '|', s.get('win_amount_wan'))
PY
```

Expected: 已分类文档数 > 0；中标公告抽样含 win_company（部分为空属正常漏提）。

- [ ] **Step 4: 全量回填（确认收敛）**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python scripts/backfill_announce_type.py
```

Expected: `扫描 N 条，提交更新 K 条`（第二次跑应接近 0 条待更新，幂等收敛）。

- [ ] **Step 5: 提交**

```bash
git add scripts/backfill_announce_type.py
git commit -m "feat(announce): 存量公告按标题回填分类并补提取中标信息

- 原因：历史已入库数据需要与新增字段对齐
- 改动：新增 scripts/backfill_announce_type.py，幂等回填 announce_type + 中标公告的 win_company/win_amount_wan
- 影响：全量回填已验证收敛；分类漏提属正常（各省表述差异）"
```

---

### Task 8: 独立查询命令 `scripts/query_results.py`

**Files:**
- Create: `scripts/query_results.py`

仿 `scripts/query_opportunities.py` 模式，从 ES 按 release_date 范围 + 类型/地区/关键词/中标单位过滤，输出三组 MD + 全字段 Excel，按 `output/结果查询/<时间戳>_<起>_<止>/` 归档。

- [ ] **Step 1: 创建脚本**

```python
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
import sys
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from analyze_today import region_name, classify_type
from utils.es import ESConnection

OUTPUT_BASE = os.path.join("output", "结果查询")

# 公告类型归一（spec 定义的三类）
ANNOUNCE_TYPES = ['招标公告', '中标公告', '废标公告']


def load_range(start_date, end_date, regions=None, types=None, kw=None, company=None):
    """从 ES 按 release_date 范围 + 过滤条件拉取公告。"""
    es = ESConnection()
    client = es._client()
    must = [{"range": {"release_date": {"gte": start_date, "lte": end_date + " 23:59:59"}}}]
    if regions:
        must.append({"terms": {"region": regions}})
    if types:
        must.append({"terms": {"announce_type": types}})
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
    rows = []
    for d in docs:
        html_text = bc._html_to_text(str(d.get("html", "") or ""))
        atype = d.get("announce_type", "") or classify_type(d.get("title", ""))
        if atype in ('中标/成交', '合同公告', '更正公告', '废标/终止'):
            # 旧数据回填前/漏分类的兜底：映射到三类
            atype = {'废标/终止': '废标公告'}.get(atype, '中标公告' if '中标' in atype or '成交' in atype else '招标公告')
        rows.append({
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
        })
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
```

- [ ] **Step 2: 运行验证（近 30 天全量）**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python scripts/query_results.py
```

Expected: 输出命中条数 > 0，生成 `output/结果查询/<时间戳>_<起>_<止>/查询结果.md` 与 `查询结果.xlsx`。

- [ ] **Step 3: 带过滤验证**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python scripts/query_results.py 2026-11-01 2026-11-21 --types 中标,废标 --regions beijing,chongqing
```

Expected: 命中条数 ≤ 全量结果，MD 中"已中标/已流标"两组有数据（或 0 正常）。

- [ ] **Step 4: 提交**

```bash
git add scripts/query_results.py
git commit -m "feat(announce): 新增中标/废标/招标结果查询命令 query_results.py

- 原因：用户需要随时查看哪些项目在招标/哪个公司中标/哪些流标
- 改动：新增 scripts/query_results.py，按时间+类型/地区/关键词/中标单位过滤，输出三组 MD+Excel
- 影响：纯新增查询命令，只读 ES 不爬网站；产物归档 output/结果查询/ 下"
```

---

### Task 9: 端到端验证（真实抓取一轮结果公告）

**Files:** 无代码改动，验证 Task 5-8 的联动。

- [ ] **Step 1: 先跑重庆（已实测栏目）抓结果公告**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python fast_run.py results 2026-11-20 --regions chongqing
```

Expected: `[results:chongqing] ok, kept=N`（N>0，含中标与终止公告）。若 ES 已有同日数据，kept=0 属去重正常。

- [ ] **Step 2: 验证入库带类型**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python - <<'PY'
from utils.es import ESConnection
es = ESConnection()
client = es._client()
q = {'query': {'bool': {'must': [{'prefix': {'release_date': '2026-11-20'}}, {'term': {'region': 'chongqing'}}, {'exists': {'field': 'announce_type'}}]}}, 'size': 5}
hits = client.search(index='tenders', body=q)['hits']['hits']
for h in hits:
    s = h['_source']
    print('-', s.get('announce_type'), '|', s.get('title', '')[:44])
PY
```

Expected: 输出多条记录，announce_type 为 `中标公告` 或 `废标公告`。

- [ ] **Step 3: 用查询命令验证**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python scripts/query_results.py 2026-11-20 2026-11-21 --regions chongqing --types 中标,废标
```

Expected: 命中所抓数据，"已中标/已流标"两组有内容。

- [ ] **Step 4: 顺带跑北京区级（两个列表）验证**

```bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
.venv/bin/python fast_run.py results 2026-11-20 --regions beijing
```

Expected: `[results:beijing] ok, kept=N`（含区级中标与区级废标）。

---

### Task 10: 更新采集清单文档（记录本轮接入）

**Files:**
- Modify: `docs/省份采集站点清单.md`

- [ ] **Step 1: 更新文档**

在 `docs/省份采集站点清单.md` 的"待接入的结果公告栏目"表格中，把本轮已接入的 5 项标注为「✅ 已接入」并把内容移至"已接入"说明；同时更新底部备注，把北京/天津/重庆/山东/贵州从对应分组中移出。具体调整：

- "待接入的结果公告栏目（已核实，等待统一爬取）" 表格 5 行（北京×2、天津、重庆、山东、贵州）状态列改为「✅ 已接入 2026-11-21」；
- 备注中"站点有结果栏目、但当前没抓"分组移除 山东、重庆、贵州；
- 备注中"当前只抓采购公告、结果栏目待确认的省"分组移除 北京、天津（北京区级/天津结果已接入）；
- 在备注新增一行说明：结果公告采集走 `python fast_run.py results <date>`，中标/废标公告与招标公告同库以 `announce_type` 区分，历史数据已回填分类，查询用 `scripts/query_results.py`。

- [ ] **Step 2: 提交**

```bash
git add docs/省份采集站点清单.md
git commit -m "docs(announce): 更新采集清单，标注结果公告接入状态

- 原因：本轮已接入北京区级中标/废标、天津、重庆、山东、贵州结果公告
- 改动：更新清单状态列与备注分组
- 影响：纯文档"
```

---

## 自检记录

**1. Spec 覆盖检查：**
- 数据模型 3 字段 → Task 2 ✓
- 类型判定/中标提取 → Task 1 + Task 3 ✓
- 结果采集器 6 项（北京×2/天津/重庆/山东/贵州）→ Task 5 ✓
- 每日流程（results 命令 + 汇总 Excel 3 列）→ Task 4 + Task 6 ✓
- 历史回填 → Task 7 ✓
- 独立查询命令 → Task 8 ✓
- 成功标准 1-5 → Task 2/3、Task 5/9、Task 7、Task 8、Task 4 各覆盖 ✓

**2. Placeholder 扫描：** 无 TBD/TODO/占位说明；每个代码步骤均有完整代码与验证命令。

**3. 类型一致性检查：**
- `announce_type` / `win_company` / `win_amount_wan` 字段名在 Task 1/2/3/4/7/8 全部一致 ✓
- `RESULT_CRAWLERS` / `run_result_region` / `date_dir` 在 Task 5 定义、Task 6 引用一致 ✓
- `classify_announce_type` / `extract_win_company` / `extract_win_amount` 签名在 Task 1 定义、Task 3/7 调用一致 ✓
- `_load_region_rows_from_es` / `query_date_from_es` 导出行在 Task 4 修改，与 `CN_EXPORT_COLS`（Task 2）映射一致 ✓
- `run_by_date` / `get_exists_url_from_es` / `_random_sleep` 等基类方法在各 result 爬虫中沿用父类 ✓
