# bids-spider 项目规则（Project Rules）

本规则固化 bids-spider 的标准操作流程（SOP），供后续会话直接按此执行，避免重复摸索。

## 1. 环境前提

- 本机需运行 Elasticsearch 8.x（`~/software/elasticsearch-8.17.0/bin/elasticsearch -d -p ~/software/elasticsearch.pid`），索引 `tenders` 启动时自动创建。
- 使用项目虚拟环境执行所有 Python 命令：`.venv/bin/python ...`（**禁止**用系统 python3）。
- 日志统一写 `logs/`；抓取产物统一写 `output/`；需求洞察报告写 `output/需求洞察报告_<date>.md`，图表写 `output/charts/`。
- `output/`、`logs/`、`.venv/` 已在 `.gitignore` 中，不提交。

## 2. 每日抓取标准流程

### 2.1 先检查环境

1. ES 可用：`curl -s -u elastic:7aNJbD0LTxsVLyuRcHSQ "http://localhost:9200/tenders/_count"`（能返回数字即可）。
2. 确认无残留爬虫进程：`ps aux | grep "fast_run.py" | grep -v grep`，如有先清理。

### 2.2 单命令顺序跑（少量地区 / 冒烟测试）

```bash
.venv/bin/python fast_run.py today                       # 抓今天全部地区（顺序）
.venv/bin/python fast_run.py by_date 2026-09-18 --regions beijing   # 指定日期+地区
```

跑完自动产出：汇总 Excel `output/date_<date>.xlsx`、摘要 `output/summary_<date>.md`、需求洞察报告 `output/需求洞察报告_<date>.md`。

### 2.3 全量并行批跑（29 地区，日常主流程）

```bash
mkdir -p logs && rm -f output/date_run_<date>.jsonl
printf '%s\n' beijing tianjin hebei liaoning jilin neimenggu shanxi jiangsu zhejiang anhui fujian shandong jiangxi hubei hunan guangdong hainan chongqing sichuan guizhou yunnan qinghai ningxia xinjiang guangxi hlj gansu shaanxi henan \
  | xargs -P 4 -I {} sh -c '.venv/bin/python fast_run.py by_date <date> --regions {} --summary output/date_run_<date>.jsonl >> logs/date_run_{}.log 2>&1'
echo "ALL REGIONS TODAY DONE"
```

要点：
- `-P 4` 并行 4 路，各地区站点互不冲突；**不要**改动该并行参数为过大值，避免触发站点风控。
- 每个进程写独立日志 `logs/date_run_<region>.log`，结果行追加到 `output/date_run_<date>.jsonl`。
- 进度检查：`wc -l output/date_run_<date>.jsonl`（应为 29 行）+ `grep -c 'ok,' logs/date_run_*.log`。

### 2.4 汇总 + 自动生成洞察报告

全部完成后执行：

```bash
.venv/bin/python fast_run.py summarize <date>
```

该命令读取 jsonl，合并各地区 Excel 生成 `output/date_<date>.xlsx`，并**自动调用 `analyze_today.py` 生成需求洞察报告**（无需手动触发）。

### 2.5 查看历史某天商机（查库，不爬网站）

```bash
.venv/bin/python fast_run.py db_date 2026-09-17
```

该命令直接从 Elasticsearch 按 `release_date` 查询历史某天的全部公告（scroll 拉全量），导出统一 Excel 并自动生成洞察报告，**不访问任何网站**。与 `by_date`（重新爬取）的区别：`db_date` 只读库，适合回溯已入库的历史数据。

## 3. 需求洞察报告（analyze_today.py）

- 独立脚本：`python analyze_today.py <date>`（省略日期默认今天）。
- 逻辑：读取 `output/date_<date>.xlsx` → 特征提取（公告类型 / 需求品类 10 类 / 采购人主体 6 类 / 金额「元|万元」两单位）→ 生成 5 张图表（品类 / 地区 / 类型 / 采购人 / 金额规模）到 `output/charts/<date>_*.png` → 输出 Markdown 报告。
- 报告含章节：Abstract / 1.Introduction / 2.需求品类结构 / 3.区域需求版图 / 4.金额规模与重点机会 / 5.Conclusion / 6.References。
- **图表与报告按日期隔离命名**，多日数据不会互相覆盖。

## 4. 数据口径说明

- 去重：以公告 URL（href）为文档 ID 幂等去重，`exists_urls` 命中即跳过。**kept=0 可能意味着今日公告已入库（去重正常），不代表抓取失败**，需结合日志判断。
- 金额提取率约 13%（由各平台披露完整度决定），报告中需注明该局限。
- 已知地区行为：北京今日数据量大时 kept 可能为 0（此前全量已入库）；内蒙古/河南可能返回 no_match（当日无发布）；云南仅首页 10 条（服务端风控，翻页遇 406 自动停止）；上海已封 IP（420 Blacklist）不可用。

## 5. 提交推送规范

- 仓库有两个 remote：
  - `fork` → `https://github.com/hation/bids-spider.git`（**用户的 fork，推送目标**）
  - `origin` → `https://github.com/broholens/bids-spider.git`（原仓库，**不要推送**）
- 提交：先 `git status` / `git diff` 确认改动，按逻辑拆 commit（如 `fix(crawler):` / `feat(fast_run):` / `feat(analyze):`），每条含中文说明。
- 推送：**一律 `git push fork master`**。`git push origin` 会因权限被拒（403），属预期，不是错误。
- 提交前确认 `.env`、密钥等不入库；ES 凭据已在 `utils/es.py` 硬编码（本地服务），保持现有模式即可。

## 6. 日常维护

- 爬虫进度可配定时任务每 30 分钟同步（`schedule` ab6f26ca），检查 `crawl_parallel.log` 与各 `crawl_p_*.log`。
- 详情正文用 html2text 转纯文本存 Excel，超长内容标 `truncated=是`（Excel 单格上限 32767 字符）。
- 验证码站点（黑龙江/甘肃/陕西/河南）已用 ddddocr + 重试打通，勿回退。
