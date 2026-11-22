# 关键词商机检索（标题+正文）+ 投标情况关联 — 设计文档

- 日期：2026-11-22
- 状态：设计待确认
- 关联能力：ES alias `tenders` → `tenders_v3`（IK 分词，title/html 全文可搜）；`scripts/query_opportunities.py`（现有窗口期查询，**仅匹配标题**）

## 背景

用户需求：输入一个关键词，导出该关键词下的**招标项目 + 投标情况**；搜索须覆盖**标题与标书正文**并综合匹配。

现有差距：
- `scripts/query_opportunities.py` 的 `screen_window` 调用 `ao.match_keywords(title, ...)`，**只匹配标题**，正文不参与筛选
- 无"关键词 → 直接导出"的入口（现有按投标截止窗口 + 业务关键词配置）
- 无"招标项目 ↔ 中标/废标结果"的关联导出
- 无联系人/联系电话字段

已具备：ES IK 中文分词（title、html 均可全文检索）、html2text 正文提取、`extract_win_company`/`extract_win_amount`/`extract_void_reason`、Excel 导出。

## 需求定义（已与用户确认）

1. **搜索**：标题权重高（title^3）+ 正文（html），IK 分词，综合打分。
2. **时间**：默认最近 3 个月；支持 `--start/--end` 自定义；`--all` 全库；**结果按发布日期降序（越近越前）**。
3. **投标情况**：
   - 招标项目关联其中标结果（中标单位、中标金额、中标时间）；
   - 同时展示废标信息（废标原因）；
   - **必须附招标项目链接**（便于人工核对）。
4. **联系人/联系方式**：优先取中标供应商的，缺失则兜底采购人/代理机构，并标注主体类型。
5. **输出**：Excel（全字段）+ Markdown 汇总（分档、Top 金额、地区分布）。

## 设计

### 组件 1：`scripts/search_tenders.py`（新建）

```
用法：
  .venv/bin/python scripts/search_tenders.py "服务器"                 # 默认近3个月，标题+正文加权
  .venv/bin/python scripts/search_tenders.py "信创 数据库" --start 2026-09-01 --end 2026-11-22
  .venv/bin/python scripts/search_tenders.py "算力" --all             # 全库
```

流程：
1. **ES 检索**：`multi_match` / `bool.should` 组合
   - `match: {title: {query, boost: 3}}`
   - `match: {html: {query}}`
   - 时间过滤：`release_date` range（默认近 90 天，`--all` 不限）
   - 排序：`release_date desc`，`size` 上限可配（默认 2000）
2. **分类分派**：命中记录按 `announce_type` 分招标 / 中标 / 废标
3. **关联**：招标项目 ↔ 中标/废标（见组件 3）
4. **补字段**：联系人/电话（见组件 2）
5. **导出**：Excel + MD

### 组件 2：联系人/联系方式提取（`utils/announce.py` 扩展）

新增 `extract_contact(html, prefer_win=True) -> (person, phone, subject)`：
- 优先在中标供应商名称附近（同段落/后 300 字符）找 `联系人[:：]` / `联系电话[:：]` / `电话[:：]`
- 兜底全文首个 `联系人/联系电话/电话`
- `subject` 标注为 `中标供应商` / `采购人/代理`
- 同时新增 `Tender` 字段与 ES mapping：`contact_person`(keyword)、`contact_phone`(keyword)、`contact_subject`(keyword)，入库时自动提取；存量用回填脚本补

### 组件 3：招标 ↔ 中标/废标关联

无项目编号字段，采用多策略匹配（按优先级）：
1. **项目编号正则**：从标题提取编号（如 `Z13\d{10,}`、`[A-Z]{2,}-?\d{6,}`、`JG[A-Z0-9-]+`），编号相同即同一项目
2. **标题归一化**：去除"中标（成交）结果公告/中标公告/结果公告/废标公告/终止公告/采购公告/招标公告"等后缀与包号，取前 20 字核心串相等即关联
3. 仍无则仅展示，不强关联

输出时每个招标项目一行，附：中标单位、中标金额、中标时间、中标公告链接、废标原因（若有）。

### 组件 4：Excel / Markdown 输出

Excel 列（中文名）：
`关键词 | 项目标题 | 招标链接 | 地区 | 采购类型 | 发布日期 | 投标截止 | 项目金额(万) | 中标单位 | 中标金额(万) | 中标时间 | 中标公告链接 | 废标原因 | 联系人 | 联系电话 | 联系方式主体 | 匹配位置`

MD 汇总：命中总数、类型分布、地区 Top、金额 Top20、最近 20 条清单。

输出目录：`output/关键词检索/<关键词>_<时间戳>/`（`result.xlsx` + `summary.md`）。

### 组件 5：爬虫功能整理（文档）

更新 `docs/项目功能说明.md`，补充最新状态：
- 29 个招标地区 + 20 个结果公告采集器（含新增 gansu/hlj、maincms 基类）
- IK 全文检索、announce_type/win_company/win_amount_wan/void_reason 字段
- 防风控层（令牌桶/退避/熔断）、骨架页修复、缺口补抓脚本
- 每日定时任务（招标 + 结果公告）

## 产物

| 项 | 路径 |
|---|---|
| 关键词检索脚本 | `scripts/search_tenders.py`（新建） |
| 联系人提取 | `utils/announce.py` + `crawler/base_crawler.py` + `utils/es.py`（新增字段） |
| 存量回填 | `scripts/backfill_announce_type.py` 扩展 `--contact` 模式 |
| 功能说明文档 | `docs/项目功能说明.md`（更新） |

## 成功标准

1. `search_tenders.py "服务器"` 能搜到标题或正文命中"服务器"的项目，正文命中也能召回
2. 输出的招标项目行含招标链接 + 中标单位/金额/时间/链接 + 废标原因
3. 联系人/电话优先中标供应商、兜底采购人，并有主体标注
4. 结果按发布日期降序
5. 默认近 3 个月，`--all` 可全库
