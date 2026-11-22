# 中标/废标数据完善 + 统一防风控 — 设计文档

- 日期：2026-11-22
- 状态：设计待确认，待实施
- 关联：`docs/superpowers/specs/2026-11-20-backfill-gaps-design.md`（历史缺口补抓）

## 背景与实测盘点（2026-11-22）

用户诉求：① 增加防风控逻辑；② 追加中标相关数据、废标相关信息；③ 补历史中标数据。

实测盘点结论（重要，修正原始假设）：

| 维度 | 实测值 | 结论 |
|---|---|---|
| 招标公告 | 103,776 条 | 已完备 |
| 中标公告 | 22,721 条，覆盖 27 地区 | **采集已覆盖**（多靠主采集器列表混排 + 标题分类） |
| 废标公告 | 2,247 条，覆盖 21 地区 | 覆盖但不均（河南 652 vs 广西 3） |
| 中标单位非空率 | **30.3%**（6,891 / 22,721） | **真正痛点** |
| 中标金额非空率 | **7.5%**（1,707 / 22,721） | **真正痛点** |
| 抽样 100 条空值重提取 | 单位 **100%**、金额 **58%** | **存量入库早于提取逻辑增强，从未回填** |
| 真正缺中标采集 | gansu（0/0）、hlj（38/4）、guizhou（1）、jilin（4） | 少数地区需开发 |

关键判断：**"追加中标数据"的实质是"存量回填提取字段"，不是"重新采集"**。回填收益最大、风险最低，应最先做。

地区提取质量差异（中标单位非空率）：北京 97% / 江西 95% / 湖北 99% / 河北 61% ⟷ 广东 1% / 海南 2% / 新疆 4% / 河南 11%。

## 现状代码资产

- `utils/announce.py`：`classify_announce_type` / `extract_win_company` / `extract_win_amount`（已有较丰富正则）
- `crawler/base_crawler.py` `_enrich_meta`：入库时自动分类 + 提取
- `scripts/backfill_announce_type.py`：存量回填脚本（`--meta` 模式重提取），**存在但从未跑过**
- `crawler/result_collector.py`：`RESULT_CRAWLERS`（18 地区）+ `run_result_region`
- `fast_run.py results <date> [end] --regions`：结果公告按日期抓取命令
- 防风控现状：仅 `crawler/guangxi.py` 有参数化限速/冷却/熔断；`base_crawler` 只有 `_random_sleep`，其余爬虫零散使用

## 设计

### Part A：统一防风控层

新增 `utils/throttle.py`（独立文件，便于单测），由 `BaseCrawler` 引用，所有爬虫继承即生效：

1. **令牌桶限速**：每爬虫实例一个令牌桶，默认 `rate_per_min = 30`（类属性可覆盖）。请求前 `self._throttle()` 消耗令牌，不足则 sleep。
2. **指数退避重试**：`self._with_retry(fn, retries=3)`，捕获 429/403/超时/连接错误，退避 `2^n` 秒 + 随机抖动。
3. **连续失败熔断**：连续失败 `max_fail_streak=5` 次抛 `CircuitOpenError`，本轮停止并记录（不重试）。
4. **自适应降速**：命中 429/403 时把令牌桶速率 ×0.5（下限 `min_rate_per_min=6`）；连续 `recover_after=20` 次成功后逐步恢复。
5. **兼容**：现有 `_random_sleep` 保留；各爬虫逐步改调用 `_throttle`。`guangxi.py` 的参数收敛为类属性覆盖，行为不变。

> 触发点：所有列表请求、详情请求、翻页请求。对 Playwright `context.request` 与 `requests` 两条路径统一。

### Part B：中标/废标提取质量 + 存量回填（最高优先）

1. **回填存量**：跑 `scripts/backfill_announce_type.py --meta`，对 22,721 条中 `win_company` 空/`win_amount_wan` 空的中标公告重提取（预期单位 ≈90%+、金额 ≈58%）。
2. **修复误提取**：抽样发现的 case（如新疆把项目名"新疆大学喀什校区…"当单位）——加"单位后缀白名单"过滤（有限公司/集团/研究院/事务所/中心/医院/学校/大学/银行/事务所 等），无合法后缀时置空而非保留噪声。
3. **补金额变体**：金额提取 58%→尽量提升，覆盖 `中标价`/`审定金额`/`成交价`/表格表头变体。
4. **废标信息**：`Tender` 与 ES mapping 新增 `void_reason`（废标原因，text+keyword），从正文"废标原因/流标原因/终止原因"提取，并回填存量废标 2,247 条（存量 mapping 用 `put_mapping` 增字段即可，无需 reindex）。
5. **验证报告**：回填前后按地区统计提取率对比。

### Part C：历史补抓（近 3 个月）

- 范围：2026-08-22 ~ 2026-11-21（近 90 天）
- 对象：18 个已有 result 采集器地区的中标/废标公告；招标历史缺口沿用 `scripts/backfill_gaps.py`
- 方式：逐地区按 7 天段调 `fast_run.py results <start> <end> --regions X --no-summary`
- 风控：Part A 统一层 + 地区内串行 + 段间随机间隔
- 优先级：提取率低且缺口大的地区优先（广东/海南/新疆/河南/宁夏）

### Part D：补齐 11 个地区结果公告采集

11 个地区：hebei, jiangsu, jiangxi, guangdong, hainan, sichuan, ningxia, xinjiang, hlj, gansu, henan

分两类处理：
- **已由主采集器覆盖中标**（hebei/henan/xinjiang/ningxia/sichuan/guangdong/jiangsu/jiangxi/hainan）：先审计覆盖率，仅对"废标缺失/结果栏目不全"的站点补专项采集器，避免重复。
- **真正缺采集**（hlj、gansu）：新开发 `result_<region>.py`。

标准开发流程（复用 `result_collector` 模式）：
1. 探测站点结果公告栏目入口（中标/成交/废标/终止）
2. 实现 `result_<region>.py`（继承 `BaseCrawler`，预设 `announce_type`）
3. 注册到 `RESULT_CRAWLERS`
4. 单日验证 `kept>0` + `win_company` 有值
5. 风控参数按站点特性覆盖

### Part E：每日任务纳入 results

更新 Schedule `8f6dc4dd`：在招标补抓后追加 `fast_run.py results <昨日> --regions <有采集器的地区>`，并说明中标/废标纳入每日汇总。

## 产物

| 项 | 路径 |
|---|---|
| 风控层 | `crawler/base_crawler.py`（修改） |
| 提取增强 | `utils/announce.py`（修改，新增 void_reason） |
| 回填脚本 | `scripts/backfill_announce_type.py`（修改：加废标原因、白名单过滤） |
| 结果补抓编排 | `scripts/backfill_results.py`（新建，复用 backfill_gaps 模式） |
| 新采集器 | `crawler/result_hlj.py`、`crawler/result_gansu.py` 等 |
| 回填验证报告 | `output/backfill/winbid_fillrate_<日期>.md` |

## 成功标准

1. 中标单位非空率 ≥ 85%、中标金额非空率 ≥ 50%（全量）
2. 废标 `void_reason` 非空率 ≥ 60%
3. 近 3 个月中标/废标公告补抓完成，缺 result 采集器地区补齐
4. 全程无站点封禁（统一风控生效，429/403 自动降速/熔断）
5. 每日任务自动包含中标/废标补抓

## 风险与限制

- 部分中标公告正文本身无中标单位信息（数据源限制），提取率无法到 100%
- 11 个地区中验证码站点（河南/黑龙江/甘肃）开发难度高，可能延后
- 回填会重写 2 万+ 文档，需限速批量更新（ES update_by_query 或脚本分批）

## 变更记录

- 2026-11-22：每日定时任务（Schedule 8f6dc4dd）已纳入 results 步骤——招标补抓后追加 `fast_run.py results <昨日> --regions <18 个已注册地区> --no-summary`，中标/废标结果公告随每日任务自动补抓。
