# tender-spider（bids-spider）

抓取全国各省政府采购网 / 公共资源交易平台的招标、采购公告信息，清洗后写入 Elasticsearch，并导出 Excel。

> **项目来源**：本项目的代码基础来源于 [broholens/bids-spider](https://github.com/broholens/bids-spider)（抓取招标网站招标信息）。2026-09-22 已脱离原 fork 网络转为独立项目继续开发。此来源标识受项目规则约束，**不可移除**。

## 功能特性

- **Playwright 无头浏览器抓取**：29 个省级地区站点，内置验证码识别（ddddocr / OCR）、随机延时、反爬对抗
- **增量去重入库**：以公告 URL（href）为文档 ID 写入 Elasticsearch，已入库自动跳过（三重去重）
- **按日期抓取**：`today`（两段式：先查库再增量爬）/ `by_date`（指定日期）/ `today_db` / `db_date`（只查库）
- **补爬顺带收录**：补抓历史日时，列表页扫到的今日公告一并入库，不丢弃
- **自动导出 Excel**：中文列名（地区/公告链接/商机标题/发布日期/抓取日期/商机详情/详情截断）
- **需求洞察报告**：抓取完自动生成统计图表 + 咨询级 Markdown 报告
- **个人机会分析**：按 `config/opportunities.json` 业务配置（服务器/IT/AI 关键词）自动筛出重点商机，生成机会清单 Excel + 机会分析报告；可选 LLM 精读深度洞察
- **可配置抓取范围**：单地区 / 全部地区顺序 / 多地区并行批跑（`xargs -P 4`）
- **自动化**：每日 10:00 自动补抓昨日；每日 09:00 自动归档超 7 天旧文件

## 项目结构

```
bids-spider/
├── crawler/          # 核心：29 个地区爬虫 + base_crawler 基类（Tender 模型 / ES 存取 / Excel 导出 / 日期模式）
├── utils/            # 核心：es（Elasticsearch 客户端）/ log（日志配置）/ captcha（验证码识别）
├── fast_run.py       # 核心入口：today / today_db / by_date / db_date / summarize
├── analyze_today.py  # 需求洞察报告生成器（抓取后自动调用）
├── analyze_opportunities.py  # 个人业务机会筛选器（规则引擎 + 可选 LLM 精读）
├── config/           # 业务配置：opportunities.json（机会筛选关键词 / LLM 精读开关）
├── scripts/          # 辅助：backfill_details（补详情）/ archive_output（目录归档）
├── legacy/           # 旧版 requests+selenium 链路（Mongo/CSV），已归档，不参与当前批跑
├── logs/             # 运行日志（runtime.log、date_run_*.log）
├── output/           # 抓取产物（汇总 Excel、洞察报告、图表）
└── docs/             # 产品需求与实现细节文档
```

## 环境与安装

```bash
# 依赖见 requirements.txt，建议使用项目自带虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

需要本机运行 Elasticsearch 8.x（连接配置见 `utils/es.py`，索引 `tenders` 启动时自动创建）。

### LLM 精读配置（可选）

```bash
cp .env.example .env   # 复制配置样例，填入你的 LLM API Key
```

编辑 `.env` 填入 `LLM_API_KEY`（火山方舟 / OpenAI 兼容接口均可）。不配置也能用，只是机会分析不启用 LLM 精读（纯规则引擎）。完整字段说明见 `.env.example`。

## 使用

```bash
# 获取今日商机（两段式：先查库整理已有 → 再增量爬补新增）
python fast_run.py today

# 只查库整理今日已入库商机（不爬网站，秒出）
python fast_run.py today_db

# 爬取指定日期+地区（重新爬网站）
python fast_run.py by_date 2026-09-18 --regions beijing

# 查库历史某天（不爬网站）
python fast_run.py db_date 2026-09-17

# 汇总 + 自动生成需求洞察报告
python fast_run.py summarize 2026-09-18

# 单独运行机会分析（today/by_date/summarize/db_date 已自动附带）
python analyze_opportunities.py 2026-09-21
python analyze_opportunities.py   # 默认今天

# 爬取单个地区（如北京）
python fast_run.py beijing

# 按顺序爬取全部地区
python fast_run.py all

# 多地区并行批跑（每个地区独立日志）
for r in beijing hebei shandong; do
  python fast_run.py $r > logs/crawl_p_$r.log 2>&1 &
done

# 按日期并行补抓（xargs -P 4，必须带 --no-summary，汇总交给 summarize 统一合并）
printf '%s\n' beijing hebei shandong | xargs -P 4 -I {} sh -c \
  'python fast_run.py by_date 2026-09-18 --regions {} --summary output/2026-09-18/date_run_2026-09-18.jsonl --no-summary >> logs/date_run_{}.log 2>&1'
python fast_run.py summarize 2026-09-18
```

## 数据与产物

- **存储**：Elasticsearch 索引 `tenders`，字段 `region / href / title / release_date / crawl_date / html`，按 `href` 幂等去重
- **产物按日期归档**：所有产物（汇总 Excel、摘要、洞察报告、图表、机会清单/分析、jsonl）集中存放在 `output/<date>/` 日期子目录
- **Excel**：`output/<date>/date_<date>.xlsx`（汇总，中文列名：地区/公告链接/商机标题/发布日期/抓取日期/商机详情/详情截断）
- **洞察报告**：`output/<date>/需求洞察报告_<date>.md` + `output/<date>/charts/` 图表
- **机会分析**：`output/<date>/机会清单_<date>.xlsx` + `output/<date>/机会分析_<date>.md`（按 `config/opportunities.json` 规则筛出的个人业务商机）
- **日志**：loguru 写入 `logs/runtime.log`（每周轮转）；并行批跑日志写 `logs/date_run_<region>.log`（超 7 天自动归档）

## 支持的地区

anhui 安徽 · beijing 北京 · chongqing 重庆 · fujian 福建 · gansu 甘肃 · guangdong 广东 · guangxi 广西 · guizhou 贵州 · hainan 海南 · hebei 河北 · henan 河南 · hlj 黑龙江 · hubei 湖北 · hunan 湖南 · jiangsu 江苏 · jiangxi 江西 · jilin 吉林 · liaoning 辽宁 · neimenggu 内蒙古 · ningxia 宁夏 · qinghai 青海 · shaanxi 陕西 · shandong 山东 · shanxi 山西 · sichuan 四川 · tianjin 天津 · xinjiang 新疆 · yunnan 云南 · zhejiang 浙江

## legacy 归档说明

`legacy/` 存放早期 requests + selenium + MongoDB 的抓取实现（`parser.py` 原 `utils.py`、`gov_parser.py`、`sites.py`、`spider.py`、`ccgp.py`、`high_school_parser.py`）。该链路依赖 selenium / pymongo 等不再维护的依赖，已不参与当前批跑，仅作历史参考，确认无用后可删除。
