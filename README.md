# tender-spider（bids-spider）

抓取全国各省政府采购网 / 公共资源交易平台的招标、采购公告信息，清洗后写入 Elasticsearch，并导出 Excel。

## 功能特性

- **Playwright 无头浏览器抓取**：29 个省级地区站点，内置验证码识别（ddddocr / OCR）、随机延时、反爬对抗
- **增量去重入库**：以公告 URL（href）为文档 ID 写入 Elasticsearch，已入库自动跳过
- **自动导出 Excel**：每次运行按地区输出 `output/<region>_<时间戳>.xlsx`
- **可配置抓取范围**：单地区 / 全部地区顺序 / 多地区并行批跑

## 项目结构

```
bids-spider/
├── crawler/          # 核心：29 个地区爬虫 + base_crawler 基类（Tender 模型 / ES 存取 / Excel 导出）
├── utils/            # 核心：es（Elasticsearch 客户端）/ log（日志配置）/ captcha（验证码识别）
├── fast_run.py       # 核心入口：单地区或全部地区爬取
├── legacy/           # 旧版 requests+selenium 链路（Mongo/CSV），已归档，不参与当前批跑
├── logs/             # 运行日志（runtime.log、crawl_p_*.log）
├── output/           # 抓取产物（各地区 Excel）
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

## 使用

```bash
# 爬取单个地区（如北京）
python fast_run.py beijing

# 按顺序爬取全部地区
python fast_run.py all

# 多地区并行批跑（每个地区独立日志）
for r in beijing hebei shandong; do
  python fast_run.py $r > logs/crawl_p_$r.log 2>&1 &
done
```

## 数据与产物

- **存储**：Elasticsearch 索引 `tenders`，字段 `region / href / title / release_date / crawl_date / html`，按 `href` 去重
- **Excel**：`output/<region>_<时间戳>.xlsx`，列 `region / href / title / release_date / crawl_date / html（纯文本） / truncated（正文是否超长截断）`
- **日志**：loguru 写入 `logs/runtime.log`；并行批跑日志建议重定向到 `logs/crawl_p_<region>.log`

## 支持的地区

anhui 安徽 · beijing 北京 · chongqing 重庆 · fujian 福建 · gansu 甘肃 · guangdong 广东 · guangxi 广西 · guizhou 贵州 · hainan 海南 · hebei 河北 · henan 河南 · hlj 黑龙江 · hubei 湖北 · hunan 湖南 · jiangsu 江苏 · jiangxi 江西 · jilin 吉林 · liaoning 辽宁 · neimenggu 内蒙古 · ningxia 宁夏 · qinghai 青海 · shaanxi 陕西 · shandong 山东 · shanxi 山西 · sichuan 四川 · tianjin 天津 · xinjiang 新疆 · yunnan 云南 · zhejiang 浙江

## legacy 归档说明

`legacy/` 存放早期 requests + selenium + MongoDB 的抓取实现（`parser.py` 原 `utils.py`、`gov_parser.py`、`sites.py`、`spider.py`、`ccgp.py`、`high_school_parser.py`）。该链路依赖 selenium / pymongo 等不再维护的依赖，已不参与当前批跑，仅作历史参考，确认无用后可删除。
