# 历史缺口智能补抓系统 — 设计文档

- 日期：2026-11-20
- 状态：设计确认，待实施

## 背景

爬虫系统日常批跑（29 地区）发现 5 个地区存在历史断档：

| 地区 | 断档范围 | 天数 | 根因 | 历史可访问性 |
|---|---|---|---|---|
| neimenggu | 2026-09-17 ~ 11-19 | 64 | 接口 500 / 页面改版（当日已修复） | ✅ 接口按日查询全通（实测 9-17:84条/10-15:59条/11-5:64条） |
| guizhou | 2026-10-01 ~ 11-19 | 51 | 页面捕获请求头失效（同 neimenggu 旧病） | ⚠ 接口通但 data=None，需逆向 body 格式 |
| jiangsu | 2026-10-24 ~ 11-19 | 27 | http 被拒（当日已修复 https） | ⚠ 页面只显示当天；底层接口有全量数据但 payload 复杂（尽力而为） |
| tianjin | 2026-10-23 ~ 11-08 | 17 | 间歇 Timeout 30s | ⚠ 需诊断 |
| shaanxi | 间歇缺失 | ~14 | 间歇 no_match | ⚠ 需诊断 |

其他 24 个地区启动后基本连续，无需补。

## 目标

1. 自动扫描 ES 生成缺口清单（地区×日期）
2. 修复 guizhou / tianjin / shaanxi 爬虫后，逐日逐地区补抓
3. 全程风控限速、断点续跑、产出进度与覆盖率报告

## 架构

### 组件 1：缺口扫描器（脚本内置）

扫 ES，按 `release_date`（含时间戳格式，取前 10 位规整）聚合 `region × day`。

- 起算日：**该地区首次入库日**（排除爬虫项目启动前的合理空档）
- 终止日：昨天（今天及未来不补）
- 输出：`[(region, date), ...]` 清单，按地区分组、组内日期升序

### 组件 2：爬虫前置修复

- **guizhou.py**：页面触发式捕获请求头失效 → 改直接构造 POST（复用 neimenggu 修复模式）；逆向接口 body（当前 `{"pageNo":1,"pageSize":15,"parentId":"190013","childrenCode":"ZcyAnnouncement"}` 返回 data=None，需从列表页 JS 还原真实参数）
- **tianjin.py**：Page.goto 间歇 Timeout 30s → 加超时重试
- **shaanxi.py**：间歇 no_match → 诊断列表页结构
- **jiangsu.py**：标 low-priority（页面只显示当天），尽力而为

### 组件 3：执行器

对缺口清单逐项调用现有 `fast_run.py by_date <date> --regions <region> --no-summary`。

- 并发：跨地区 `-P 2`；地区内逐日串行
- 幂等/断点续跑：`exists_urls` 去重，已入库自动跳过；可中断后从 jsonl 进度续跑
- 熔断：某地区连续失败 ≥3 天暂停该地区，记录原因，继续其他地区
- 单日多次请求限速沿用各爬虫自带 `_random_sleep`

### 组件 4：报告

- 进度：`output/backfill/<run_id>.jsonl`（每条 `{region, date, status, kept}`）
- 结束：重扫 ES 生成缺口覆盖率报告（补前 vs 补后）

## 产物

| 项 | 路径 |
|---|---|
| 编排脚本 | `scripts/backfill_gaps.py` |
| 进度 jsonl | `output/backfill/<run_id>.jsonl` |
| 运行日志 | `logs/backfill_<run_id>.log` |
| 覆盖率报告 | `output/backfill/<run_id>.md` |

## 成功标准

- neimenggu / guizhou / tianjin / shaanxi 断档全部补完（ES count 校验通过）
- jiangsu 尽力而为，能补多少算多少
- 全程未触发站点风控（无 403/429 封禁）
- 脚本可重复运行（幂等）
