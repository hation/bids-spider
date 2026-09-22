# bids-spider 项目规则（Project Rules）

本规则固化 bids-spider 的标准操作流程（SOP），供后续会话直接按此执行，避免重复摸索。

## 1. 环境前提

- 本机需运行 Elasticsearch 8.x（`~/software/elasticsearch-8.17.0/bin/elasticsearch -d -p ~/software/elasticsearch.pid`），索引 `tenders` 启动时自动创建。
- 使用项目虚拟环境执行所有 Python 命令：`.venv/bin/python ...`（**禁止**用系统 python3）。
- 日志统一写 `logs/`；抓取产物统一写 `output/`；需求洞察报告写 `output/需求洞察报告_<date>.md`，图表写 `output/charts/`；个人机会分析写 `output/机会分析_<date>.md` + `output/机会清单_<date>.xlsx`。
- `output/`、`logs/`、`.venv/` 已在 `.gitignore` 中，不提交。

## 2. 每日抓取标准流程

### 2.1 先检查环境

1. ES 可用：`curl -s -u elastic:7aNJbD0LTxsVLyuRcHSQ "http://localhost:9200/tenders/_count"`（能返回数字即可）。
2. 确认无残留爬虫进程：`ps aux | grep "fast_run.py" | grep -v grep`，如有先清理。

### 2.2 命令速查

```bash
.venv/bin/python fast_run.py today                  # 今日商机：两段式（先查库整理已有 → 再增量爬补新增）
.venv/bin/python fast_run.py today_db               # 只查库整理今日已入库商机，不爬网站（秒出）
.venv/bin/python fast_run.py by_date 2026-09-18 --regions beijing   # 指定日期+地区（重新爬网站）
.venv/bin/python fast_run.py db_date 2026-09-17     # 查库历史某天，不爬网站
.venv/bin/python fast_run.py summarize <date>       # 汇总 + 自动生成洞察报告
.venv/bin/python analyze_opportunities.py <date>    # 单独重跑个人机会分析（省略日期默认今天）
```

跑完自动产出：汇总 Excel `output/date_<date>.xlsx`、摘要 `output/summary_<date>.md`、需求洞察报告 `output/需求洞察报告_<date>.md`、**个人机会清单 `output/机会清单_<date>.xlsx` + 机会分析 `output/机会分析_<date>.md`**（均自动附带，无需手动触发）。

### 2.3 全量并行批跑（29 地区，日常主流程）

```bash
mkdir -p logs && rm -f output/date_run_<date>.jsonl
printf '%s\n' beijing tianjin hebei liaoning jilin neimenggu shanxi jiangsu zhejiang anhui fujian shandong jiangxi hubei hunan guangdong hainan chongqing sichuan guizhou yunnan qinghai ningxia xinjiang guangxi hlj gansu shaanxi henan \
  | xargs -P 4 -I {} sh -c '.venv/bin/python fast_run.py by_date <date> --regions {} --summary output/date_run_<date>.jsonl --no-summary >> logs/date_run_{}.log 2>&1'
echo "ALL REGIONS TODAY DONE"
```

要点：
- `-P 4` 并行 4 路，各地区站点互不冲突；**不要**改动该并行参数为过大值，避免触发站点风控。
- **必须带 `--no-summary`**：该参数让并行进程只抓取+写 jsonl、不自动 merge/export/cleanup，也不覆盖汇总文件；各地区单地区 Excel 会保留，交给 2.4 的 `summarize` 统一合并。若不带，各进程会互相覆盖汇总 Excel 并删除单地区 Excel，导致汇总只剩最后一个地区。
- 每个进程写独立日志 `logs/date_run_<region>.log`，结果行追加到 `output/date_run_<date>.jsonl`。
- 进度检查：`wc -l output/date_run_<date>.jsonl`（应为 29 行）+ `grep -c 'ok,' logs/date_run_*.log`。

### 2.4 汇总 + 自动生成洞察报告

全部完成后执行：

```bash
.venv/bin/python fast_run.py summarize <date>
```

该命令读取 jsonl，合并各地区 Excel 生成 `output/date_<date>.xlsx`，并**自动调用 `analyze_today.py` 生成需求洞察报告、`analyze_opportunities.py` 生成个人机会分析**（均无需手动触发）。

**汇总完成后自动清理**：单地区 Excel（`date_<date>_<region>.xlsx`）会自动删除——数据已合并进汇总且全量在 ES（可随时 `db_date` 重导出），不保留避免 output 膨胀。

### 2.5 按日期与查库命令

| 命令 | 行为 | 适用场景 |
|---|---|---|
| `today` | 两段式：先查库整理今日已有，再增量爬网站补新增 | 获取今日完整商机（推荐） |
| `today_db` | 只查 ES 今日已入库数据，不爬网站 | 快速看今日已有数据 |
| `by_date <date>` | 重新爬网站该日公告 | 补抓缺失数据 |
| `db_date <date>` | 从 ES 查库该日公告 | 快速看历史，不碰网站 |

注意：`db_date`/`today_db` 只能查到**已入库**的数据，若某天从未运行过抓取，库里无该日数据会提示 `ES 中无数据`。

### 2.6 定时任务（自动化）

| 任务 | 时间 | 作用 |
|---|---|---|
| 每日补抓昨日商机（`8f6dc4dd`） | 每天 10:00 | 补抓昨天 00:00~24:00 全量公告；**顺带收录今天发布的公告**（不丢弃，供 today_db/today 直接复用） |
| output 目录自动归档（`8b309137`） | 每天 09:00 | output 与 logs 下超过 7 天的旧文件自动压缩归档到各 `archive/` |

**补爬顺带收录机制**：`by_date` 补任意历史日时，列表页扫到的**目标日及之后**（含今天/更晚）公告一并入库（`guarded_save` 已放开上界，finally 先入库全部再过滤导出 Excel）。Excel 仍只含目标日，今天的公告只进 ES 供查询。

**翻页深度由日期决定**：扫描模式按日期判断翻页——`guarded_save` 遇早于目标日的公告立即停止（列表倒序，翻过目标日即止），页数仅作防死循环兜底（上限 200 页），已移除固定 8 页 / 累计 100 条的硬截断。因此今日发布很多时仍会继续翻页直到覆盖昨日；无日期站点回退到页数兜底。

## 3. 需求洞察报告（analyze_today.py）

- 独立脚本：`python analyze_today.py <date>`（省略日期默认今天）。
- 逻辑：读取 `output/date_<date>.xlsx` → 特征提取（公告类型 / 需求品类 10 类 / 采购人主体 6 类 / 金额「元|万元」两单位）→ 生成 5 张图表（品类 / 地区 / 类型 / 采购人 / 金额规模）到 `output/charts/<date>_*.png` → 输出 Markdown 报告。
- 报告含章节：Abstract / 1.Introduction / 2.需求品类结构 / 3.区域需求版图 / 4.金额规模与重点机会 / 5.Conclusion / 6.References。
- **图表与报告按日期隔离命名**，多日数据不会互相覆盖。

## 4. 数据口径说明

- 去重：以公告 URL（href）为文档 ID 幂等去重，`exists_urls` 命中即跳过。**kept=0 可能意味着今日公告已入库（去重正常），不代表抓取失败**，需结合日志判断。
- 金额提取率约 13%（由各平台披露完整度决定），报告中需注明该局限。
- 已知地区行为：北京今日数据量大时 kept 可能为 0（此前全量已入库）；**内蒙古接口持续返回 500 系统错误（2026-09-18 起，暂不可用，待站点恢复）**；河南可能返回 no_match（当日无发布）；**江苏列表页只显示"访问当天"、江西分页器窗口有限（约 60 条）、云南仅首页 10 条（翻页遇 406 风控）——这三个地区次日补抓看不到昨日，昨日数据依赖当天抓取的顺带收录兜底（数据不丢，但次日补抓无法补足）**；上海已封 IP（420 Blacklist）不可用。

## 5. 个人机会分析（analyze_opportunities.py）

- 业务配置：`config/opportunities.json`（业务名称 / 核心关键词 / 次要关键词 / 排除关键词 / 关注地区 / 金额区间 / **LLM提示词**）。**LLM 精读运行参数统一放 `.env`**：`LLM_ENABLED`（true/false）/ `LLM_API_BASE` / `LLM_MODEL` / `LLM_LIMIT`（兜底上限）/ `LLM_API_KEY`（密钥，不入库，已 gitignore）；**提示词模板放 `config/opportunities.json` 的 `LLM提示词`**（system + user 两段，占位符 `{date_key}` / `{items}`，调整后重跑即生效）。
- 规则引擎：标题命中**排除关键词**（复印纸/物业/食堂…）→ 剔除；标题命中**核心关键词**（服务器/算力/AI/大模型/信创/数据中心…）→ **直接相关**；标题命中**次要关键词**（信息化/系统集成/运维…）→ **相关**；仅正文反复命中核心词 → **意向线索**（弱信号，可能存在噪声，以标题命中为主）。
- 产物：`output/机会清单_<date>.xlsx`（地区/链接/标题/类型/相关度/匹配词/金额/商机详情/详情截断）+ `output/机会分析_<date>.md`（分档清单 + 大金额 TOP + LLM 洞察）。
- 自动触发：`today` / `by_date` / `summarize` / `db_date` 跑完自动附带；可独立 `python analyze_opportunities.py <date>` 重跑。
- LLM 精读：`.env` 中 `LLM_ENABLED=true` 且配置 `LLM_API_KEY` 后，自动挑选精读条目（**直接相关全部 + 其余按「有金额优先、金额降序」补足**），调用大模型精读（火山方舟 deepseek-v4-flash，超时 600s）；`LLM_LIMIT` 仅作兜底上限（默认 50，设 0 不限），正常无需手动改；调用失败自动降级为纯规则引擎，不影响主流程。

## 6. 提交推送规范

- 仓库为**独立项目**（2026-09-22 已脱离 fork 网络，非 fork），remote 指向用户自己的 GitHub：`origin` → `https://github.com/hation/bids-spider.git`。
- 默认分支为 **`master`**。
- 提交：先 `git status` / `git diff` 确认改动，按逻辑拆 commit（如 `fix(crawler):` / `feat(fast_run):` / `feat(analyze):`），每条含中文说明。
- 推送：`git push origin master`。
- 若本机 git 配置了 socks5 代理（`http.proxy`/`https.proxy`）且代理未运行导致推送失败（报 127.0.0.1 连接失败），可用 `git -c http.proxy= -c https.proxy= push origin master` 临时直连推送。
- 提交前确认 `.env`、密钥等不入库；ES 凭据已在 `utils/es.py` 硬编码（本地服务），保持现有模式即可。

## 7. 日常维护

- 详情正文用 html2text 转纯文本存 Excel，超长内容标 `truncated=是`（Excel 单格上限 32767 字符）。
- 验证码站点（黑龙江/甘肃/陕西/河南）已用 ddddocr + 重试打通，勿回退。
- **output 目录管理**：单地区 Excel 汇总后自动清理；`scripts/archive_output.py` 每日 09:00 归档超 7 天的汇总 Excel/报告/图表/jsonl 到 `output/archive/`。
- **logs 目录管理**：`date_run_*.log`/`crawl_*.log`/`backfill_*.log` 超 7 天自动归档到 `logs/archive/`；`runtime.log` 超 50MB 自动轮转压缩（loguru 已配每周轮转+保留 10 份，双保险）。
- **补爬顺带收录**：每天 10:00 补抓昨日时，今天的公告会一并入库（不丢弃），用 `today_db` 或 `today` 第一步即可直接查库拿到。
