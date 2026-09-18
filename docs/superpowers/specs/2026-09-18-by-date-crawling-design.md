# 按日期抓取招标公告 — 设计文档

- 日期：2026-09-18
- 状态：已确认（用户批准方案 A）
- 范围：bids-spider 爬虫新增"按日期抓取"能力

## 1. 背景与目标

当前爬虫只支持"抓最近 N 页"（默认 50 页 / 500 条），无法精确抓取某一天或某段日期发布的招标公告。用户需要：

1. **每日监控**：每天抓取"今日新发布"的公告，用于日常跟进。
2. **指定日期回溯**：抓取某一天 / 某段日期（如昨天、上周、某年某月）发布的全部公告，用于补数据。

目标：在不破坏现有 50 页全量模式的前提下，新增"按日期"模式，覆盖全部 29 个地区，默认全量、可选子集，输出汇总 Excel + 摘要报告，并入库 ES。

## 2. 现状基础（已核实）

- 每条 `Tender` 都有 `release_date` 字段，各站点从列表接口/详情中提取（noticeTime / publishDate / webdate / date 等）。
- 所有站点列表均按日期**倒序**排列 → "今日公告"通常在前 1-3 页。
- 部分站点列表接口/URL 原生支持日期范围参数（如内蒙古列表 URL 已带 `startTime` / `endTime` 空参数）。
- 已入库数据可按 `release_date` 范围从 ES 随时查询（无需改动）。

## 3. 方案（用户选定：方案 A）

**原生日期参数优先 + 扫描早停回退。**

### 3.1 核心机制

BaseCrawler 新增按日期入口 `run_by_date(start_date, end_date=None, increment=True)`，内部走新分支 `_crawl_by_date(context)`：

1. 加载 `exists_urls`（按 href 去重，沿用现有逻辑）。
2. 逐页解析列表，每页记录条目的 `release_date`。
3. 按站点能力选择列表获取策略（见 3.2）。
4. **严格过滤**：仅保留 `start_date <= release_date <= end_date` 的条目（最终防线）。
5. 入库 ES（按 href 去重）+ 单地区 Excel（可关闭）。

### 3.2 两种列表获取策略

| 策略 | 声明方式 | 行为 | 适用 |
|---|---|---|---|
| 原生日期参数 | 爬虫声明 `date_filter = "url_param"` 并实现 `build_list_url(page, start, end)` | 列表 URL / body 直接带日期范围，只请求匹配日期的页 | 任意日期精准回溯 |
| 扫描 + 早停（默认） | 不声明 | 从最新往旧翻页，遇 `release_date < start_date` 立即停止翻页 | 今日 / 近几日监控 |

### 3.3 能力声明（29 个爬虫）

- 第一轮给原生已知的站点加声明（内蒙古 `startTime/endTime` 直接补值；portal 类接口顺带排查）。
- 未声明的自动走扫描早停，**不影响**现有 50 页全量模式。

## 4. 入口与命令（fast_run.py）

```
python fast_run.py today                                          # 今日全量（默认全部 29 地区，可并行）
python fast_run.py by_date 2026-09-18                             # 指定单日
python fast_run.py by_date 2026-09-01 2026-09-05                  # 日期区间
python fast_run.py by_date 2026-09-18 --regions beijing,tianjin   # 子集
```

- `today` = `by_date 今天`（Asia/Shanghai）。
- 并行沿用现有 `-P` 批跑；单地区失败不中断，记入摘要。

## 5. 输出

每次按日期跑完后统一生成（在全部地区结束后汇总）。单日命名 `date_<YYYY-MM-DD>`，区间命名 `date_<start>_<end>`：

- **汇总 Excel**：`output/date_2026-09-18.xlsx` 或 `output/date_2026-09-01_2026-09-05.xlsx`（合并全部地区数据，列为 region / href / title / release_date / html / crawl_date / truncated）。
- **摘要报告**：`output/summary_<同上面命名>.md`（各地区条数、新增数、重点公告标题列表、失败/不支持地区及原因）。

## 6. 错误处理与边界（已与用户确认）

1. `release_date` 无法从列表解析的站点（如新疆从详情 URL 提取日期）→ 日期模式下**跳过**该地区，在摘要标注"不支持"，不硬猜。
2. 回溯很老日期但无原生参数的站点 → 告警并**跳过**该地区（翻几百页不可行），摘要标注"不支持回溯"。
3. 严格过滤是最终防线：无论哪种策略，只有 `release_date` 落在 `[start, end]` 的条目才入库。
4. 时间基准：北京时间（Asia/Shanghai）。
5. 现有 50 页全量模式（`fast_run.py <region>` / `all`）完全不变。

## 7. 测试

1. `python fast_run.py by_date 2026-09-18 --regions beijing,tianjin`
   → 校验所有入库记录 `release_date == 2026-09-18`，且与 ES 已有数据无重复。
2. `python fast_run.py by_date 2026-09-01 2026-09-05 --regions jilin`（原生参数站）
   → 校验区间内公告全部命中。
3. 跑完验证 `output/date_*.xlsx` 与 `output/summary_*.md` 生成且内容正确。

## 8. 非目标（YAGNI）

- 不做公告"类型"字段（招标/中标/合同）的统计分类。
- 不引入数据库 schema 变更（仍用 ES，字段不变）。
- 不做定时任务编排（定时由 TRAE 自动化或用户自行 cron 调度）。

## 9. 改动清单

| 文件 | 改动 |
|---|---|
| `crawler/base_crawler.py` | 新增 `run_by_date` / `_crawl_by_date` / 早停与严格过滤逻辑 |
| `crawler/*.py`（29 个） | 可选：声明 `date_filter` 与 `build_list_url`（第一轮只加原生已知的） |
| `fast_run.py` | 新增 `today` / `by_date` 命令、日期模式批跑、汇总 Excel + 摘要报告生成 |
| 测试 | 按第 7 节执行验证 |
