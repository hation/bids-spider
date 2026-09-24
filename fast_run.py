"""快速爬取脚本：无头模式 + 短随机延时（不修改现有爬虫代码）

用法：
    python fast_run.py <region>     # 爬取单个地区，如 tianjin / jiangsu
    python fast_run.py all          # 依次爬取全部已支持地区
    python fast_run.py              # 默认 tianjin

原理：运行时在内存中给 BaseCrawler 打补丁（覆盖 run 与 _random_sleep），
不触碰任何现有文件；已入库的数据会按 href 自动跳过。
"""
import glob
import json
import os
import random
import sys
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from crawler.base_crawler import BaseCrawler, _DateBoundaryReached

# region 名称 -> (模块名, 类名)
CRAWLERS = {
    'beijing': ('beijing', 'BeiJing'),
    'tianjin': ('tianjin', 'TianJin'),
    'hebei': ('hebei', 'HeBei'),
    'liaoning': ('liaoning', 'LiaoNing'),
    'jilin': ('jilin', 'JiLin'),
    'neimenggu': ('neimenggu', 'NeiMengGu'),
    'shanxi': ('shanxi', 'ShanXi'),
    'jiangsu': ('jiangsu', 'JiangSu'),
    'zhejiang': ('zhejiang', 'ZheJiang'),
    'anhui': ('anhui', 'AnHui'),
    'fujian': ('fujian', 'FuJian'),
    'shandong': ('shandong', 'ShanDong'),
    'jiangxi': ('jiangxi', 'JiangXi'),
    'hubei': ('hubei', 'HuBei'),
    'hunan': ('hunan', 'HuNan'),
    'guangdong': ('guangdong', 'GuangDong'),
    'hainan': ('hainan', 'HaiNan'),
    'chongqing': ('chongqing', 'ChongQing'),
    'sichuan': ('sichuan', 'SiChuan'),
    'guizhou': ('guizhou', 'GuiZhou'),
    'yunnan': ('yunnan', 'YunNan'),
    'qinghai': ('qinghai', 'QingHai'),
    'ningxia': ('ningxia', 'NingXia'),
    'xinjiang': ('xinjiang', 'XinJiang'),
    'guangxi': ('guangxi', 'GuangXi'),
    'hlj': ('hlj', 'HeiLongJiang'),
    'gansu': ('gansu', 'GanSu'),
    'shaanxi': ('shaanxi', 'ShaanXi'),
    'henan': ('henan', 'HeNan'),
}


def fast_sleep(_min=1, _max=60):
    """覆盖原随机延时（1-60s）为固定 1-3s"""
    time.sleep(random.uniform(1, 3))


def fast_run(self, increment=True):
    """覆盖 BaseCrawler.run：headless=True 启动浏览器"""
    self.exists_urls = self.get_exists_url_from_es()
    try:
        with Stealth().use_sync(sync_playwright()) as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled',
                ]
            )
            context = browser.new_context()
            if increment:
                self._crawl(context)
            else:
                self._crawl_history(context)
    finally:
        self.save_tenders_to_es()
        self.save_tenders_to_excel()


# 内存打补丁，不改动任何现有文件
BaseCrawler.run = fast_run
BaseCrawler._random_sleep = staticmethod(fast_sleep)


def run_region(region):
    module_name, class_name = CRAWLERS[region]
    module = __import__(f'crawler.{module_name}', fromlist=[class_name])
    crawler_class = getattr(module, class_name)
    crawler_class().run()


CN_TZ = timezone(timedelta(hours=8))  # 北京时间


def today_str():
    return datetime.now(CN_TZ).strftime('%Y-%m-%d')


def date_dir(date_key):
    """按日期归档目录：output/<date>/（当天所有产物集中存放）"""
    d = os.path.join('output', date_key)
    os.makedirs(d, exist_ok=True)
    return d


def run_region_by_date(region, start_date, end_date, summary_path=None):
    """单地区按日期抓取；可追加一行结果到 summary_path（JSONL，供并行批跑汇总）。"""
    module_name, class_name = CRAWLERS[region]
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
    print(f'[{region}] {status}, kept={kept}, no_date={no_date}')
    return record


def build_summary(start_date, end_date, records, merged_df):
    """生成摘要报告 output/summary_<date>.md"""
    range_key = start_date if start_date == end_date else f'{start_date}_{end_date}'
    lines = [
        f'# 按日期抓取摘要 {start_date} ~ {end_date}',
        '',
        f'共 {len(records)} 个地区参与，成功 {sum(1 for r in records if r["status"] == "ok")} 个。',
        '',
        '| 地区 | 状态 | 新增条数 |',
        '|---|---|---|',
    ]
    for r in records:
        lines.append(f'| {r["region"]} | {r["status"]} | {r["kept"]} |')
    lines.append('')
    lines.append('## 各条公告标题（截取）')
    if merged_df is not None and not merged_df.empty:
        # 兼容中文/英文列名（单地区 Excel 现为中文列名）
        t_col = 'title' if 'title' in merged_df.columns else '商机标题'
        r_col = 'region' if 'region' in merged_df.columns else '地区'
        d_col = 'release_date' if 'release_date' in merged_df.columns else '发布日期'
        for _, row in merged_df.iterrows():
            title = str(row[t_col])[:60]
            lines.append(f'- [{row[r_col]}] {title} ({row[d_col]})')
    else:
        lines.append('- （无命中）')
    md_path = os.path.join(date_dir(start_date), f'summary_{range_key}.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'summary saved: {md_path}')


# Excel 导出统一使用中文列名（内部字段保持英文）
CN_EXPORT_COLS = {
    'region': '地区', 'href': '公告链接', 'title': '商机标题',
    'release_date': '发布日期', 'crawl_date': '抓取日期',
    'html': '商机详情', 'truncated': '详情截断',
}


def export_to_excel(df, path):
    """按中文列名导出 DataFrame（只映射存在的列）"""
    df = df.rename(columns={c: e for c, e in CN_EXPORT_COLS.items() if c in df.columns})
    df.to_excel(path, index=False)


def merge_region_excels(start_date, regions):
    """合并该日各地区数据为汇总 DataFrame（按日期目录归档）。

    数据源以 ES 为准（权威全量）：单地区 Excel 汇总后会被 cleanup_region_excels
    清理、补抓后也只含本次新增，直接读 Excel 合并会漏掉之前已入库的数据
    （重复 summarize / 补抓后重跑场景），故优先从 ES 按 release_date + regions
    全量导出；ES 不可用时回退读单地区 Excel（原行为）。
    """
    rows = _load_region_rows_from_es(start_date, regions)
    if rows:
        return pd.DataFrame(rows)
    dfs = []
    region_dir = os.path.join('output', start_date)
    for region in regions:
        for path in glob.glob(os.path.join(region_dir, f'date_{start_date}_{region}.xlsx')):
            d = pd.read_excel(path)
            # 丢弃可能残留的默认索引列（历史文件可能带 Unnamed: 0）
            if 'Unnamed: 0' in d.columns:
                d = d.drop(columns=['Unnamed: 0'])
            dfs.append(d)
    if not dfs:
        return None
    return pd.concat(dfs, ignore_index=True)


def _load_region_rows_from_es(start_date, regions):
    """从 ES 按发布日期前缀 + 地区列表查询该日记录，转为统一行字典（html 转纯文本）"""
    from utils.es import ESConnection
    from crawler.base_crawler import BaseCrawler as _BC
    es = ESConnection()
    client = es._client()
    if not client:
        return []
    query = {
        'query': {
            'bool': {
                'must': [
                    {'prefix': {'release_date': start_date}},
                    {'terms': {'region': regions}},
                ]
            }
        },
        'sort': ['_doc'],
    }
    records = []
    try:
        resp = client.search(index='tenders', body=query, scroll='2m', size=1000, _source=True)
        sid = resp['_scroll_id']
        hits = resp['hits']['hits']
        records.extend(hits)
        while hits:
            resp = client.scroll(scroll_id=sid, scroll='2m')
            hits = resp['hits']['hits']
            records.extend(hits)
        try:
            client.clear_scroll(scroll_id=sid)
        except Exception:
            pass
    except Exception as e:
        print(f'[merge] ES 导出失败，回退单地区 Excel: {e}')
        return []
    rows = []
    for h in records:
        src = h.get('_source', {})
        html = src.get('html', '') or ''
        html_text = _BC._html_to_text(html)
        rows.append({
            'region': src.get('region', ''),
            'href': src.get('href', ''),
            'title': src.get('title', ''),
            'release_date': src.get('release_date', ''),
            'html': html_text,
            'crawl_date': src.get('crawl_date', ''),
            'truncated': '是' if len(html_text) > 32767 else '否',
        })
    return rows


def run_date_mode(start_date, end_date=None, regions=None, summary_path=None, no_summary=False):
    """顺序跑全部/指定地区按日期抓取，然后生成汇总 Excel + 摘要报告 + 需求洞察报告。

    并行批跑（xargs -P 4）时应传 no_summary=True：只抓取并追加 summary_path（JSONL），
    不做 merge/export/analyze/cleanup，且保留各地区单地区 Excel，由随后的 summarize 统一汇总，
    避免多进程互相覆盖汇总文件。
    """
    end_date = end_date or start_date
    regions = regions or list(CRAWLERS)
    records = []
    for region in regions:
        records.append(run_region_by_date(region, start_date, end_date, summary_path))
    if no_summary:
        return records
    merged = merge_region_excels(start_date, regions)
    if merged is not None:
        range_key = start_date if start_date == end_date else f'{start_date}_{end_date}'
        export_to_excel(merged, os.path.join(date_dir(start_date), f'date_{range_key}.xlsx'))
        print(f'merged excel saved: output/{start_date}/date_{range_key}.xlsx')
    build_summary(start_date, end_date, records, merged)
    # 自动生成需求洞察报告
    try:
        from analyze_today import run_analysis
        run_analysis(start_date)
    except Exception as e:
        print(f'[run_date_mode] 需求洞察报告生成失败: {e}')
    # 自动生成个人业务机会分析
    try:
        from analyze_opportunities import run_opportunities
        run_opportunities(start_date)
    except Exception as e:
        print(f'[run_date_mode] 机会分析生成失败: {e}')
    # 汇总完成，清理单地区 Excel（数据已在 ES，随时可重导出）
    cleanup_region_excels(start_date)


def cleanup_region_excels(start_date):
    """汇总完成后清理该日的单地区 Excel（date_<date>_<region>.xlsx，按日期目录归档）。

    数据已合并进 date_<date>.xlsx 且全量在 ES 中（可用 db_date 随时重导出），
    单地区文件无需保留，避免 output 目录无限增长。
    """
    import glob as _g
    removed = 0
    for path in _g.glob(os.path.join('output', start_date, f'date_{start_date}_*.xlsx')):
        if path.endswith(f'date_{start_date}.xlsx'):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    if removed:
        print(f'[cleanup] 已清理 {removed} 个单地区 Excel（{start_date}）')


def summarize_command(date_key, end_date=None):
    """读取 output/date_run_<date>.jsonl 与各地区 Excel，生成汇总输出，并自动生成需求洞察报告。"""
    start_date = date_key
    if end_date is None:
        end_date = start_date
    jsonl_path = os.path.join('output', start_date, f'date_run_{start_date}.jsonl')
    records = []
    if os.path.exists(jsonl_path):
        with open(jsonl_path, encoding='utf-8') as f:
            for line in f:
                records.append(json.loads(line))
    if not records:
        print(f'no records in {jsonl_path}')
        return
    regions = [r['region'] for r in records]
    merged = merge_region_excels(start_date, regions)
    if merged is not None:
        range_key = start_date if start_date == end_date else f'{start_date}_{end_date}'
        export_to_excel(merged, os.path.join(date_dir(start_date), f'date_{range_key}.xlsx'))
    build_summary(start_date, end_date, records, merged)
    # 自动生成需求洞察报告
    try:
        from analyze_today import run_analysis
        run_analysis(start_date)
    except Exception as e:
        print(f'[summarize] 需求洞察报告生成失败: {e}')
    # 自动生成个人业务机会分析
    try:
        from analyze_opportunities import run_opportunities
        run_opportunities(start_date)
    except Exception as e:
        print(f'[summarize] 机会分析生成失败: {e}')
    # 汇总完成，清理单地区 Excel（数据已在 ES，随时可重导出）
    cleanup_region_excels(start_date)


def query_date_from_es(date_key, with_opportunities=True):
    """从 Elasticsearch 按 release_date 查询某天的全部公告，导出统一格式 Excel 并生成洞察报告。

    与 by_date（重新爬网站）不同：本命令只查库，不访问任何网站。
    产物：output/<date>/date_<date>.xlsx + output/<date>/需求洞察报告_<date>.md
    with_opportunities=False 时跳过个人机会分析（today 第一步用：第二步汇总会再做，
    避免重复精读/重复生成）。
    """
    from utils.es import ESConnection
    es = ESConnection()
    client = es._client()
    if not client:
        print(f'[db_date] ES 连接失败，无法查询')
        return 0
    # 用 scroll 拉取全部命中（release_date 为 keyword；部分站点存完整时间戳
    # 如 hainan '2026-09-22 22:55:37'，term 精确匹配会漏掉，故用 prefix 兼容两种格式）
    query = {'query': {'prefix': {'release_date': date_key}}, 'sort': ['_doc']}
    records = []
    try:
        resp = client.search(index='tenders', body=query, scroll='2m', size=1000, _source=True)
        sid = resp['_scroll_id']
        hits = resp['hits']['hits']
        records.extend(hits)
        while hits:
            resp = client.scroll(scroll_id=sid, scroll='2m')
            hits = resp['hits']['hits']
            records.extend(hits)
        try:
            client.clear_scroll(scroll_id=sid)
        except Exception:
            pass
    except Exception as e:
        print(f'[db_date] 查询失败: {e}')
        return 0
    total = resp.get('hits', {}).get('total', {}).get('value', len(records))
    if not records:
        print(f'[db_date] {date_key} 在 ES 中无数据')
        return 0
    rows = []
    from crawler.base_crawler import BaseCrawler as _BC
    for h in records:
        src = h.get('_source', {})
        html = src.get('html', '') or ''
        # ES 中存的是原始 HTML，导出前转纯文本（与爬虫侧 save_tenders_to_excel 一致）
        html_text = _BC._html_to_text(html)
        rows.append({
            'region': src.get('region', ''),
            'href': src.get('href', ''),
            'title': src.get('title', ''),
            'release_date': src.get('release_date', ''),
            'html': html_text,
            'crawl_date': src.get('crawl_date', ''),
            'truncated': '是' if len(html_text) > 32767 else '否',
        })
    df = pd.DataFrame(rows)
    out = os.path.join(date_dir(date_key), f'date_{date_key}.xlsx')
    export_to_excel(df, out)
    print(f'[db_date] {date_key} ES 命中 {total} 条，导出 {out}')
    # 自动生成需求洞察报告
    try:
        from analyze_today import run_analysis
        run_analysis(date_key)
    except Exception as e:
        print(f'[db_date] 需求洞察报告生成失败: {e}')
    # 自动生成个人业务机会分析（today 第一步跳过，避免第二步汇总重复精读）
    if with_opportunities:
        try:
            from analyze_opportunities import run_opportunities
            run_opportunities(date_key)
        except Exception as e:
            print(f'[db_date] 机会分析生成失败: {e}')
    return len(rows)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else 'tianjin'

    # today_db：只查库整理今日已入库商机，不爬网站
    if cmd == 'today_db':
        query_date_from_es(today_str())
        return

    # 日期模式：today / by_date <start> [end] [--regions a,b] [--summary path]
    if cmd in ('today', 'by_date'):
        if cmd == 'today':
            start = today_str()
            end = None
            rest = args[1:]
        else:
            if len(args) < 2:
                print('用法: python fast_run.py by_date <YYYY-MM-DD> [YYYY-MM-DD] [--regions a,b] [--summary path]')
                sys.exit(1)
            start = args[1]
            end = args[2] if len(args) > 2 and not args[2].startswith('--') else None
            rest = args[2:] if end else args[1:]
        regions = None
        summary_path = None
        no_summary = False
        i = 0
        while i < len(rest):
            if rest[i] == '--regions' and i + 1 < len(rest):
                regions = [r.strip() for r in rest[i + 1].split(',') if r.strip()]
                i += 2
            elif rest[i] == '--summary' and i + 1 < len(rest):
                summary_path = rest[i + 1]
                i += 2
            elif rest[i] == '--no-summary':
                no_summary = True
                i += 1
            else:
                i += 1
        # today 两段式：先查库整理今日已有商机（不爬网站，秒出），再增量爬取补新增
        if cmd == 'today':
            print(f'[today] 第一步：从 ES 整理今日已入库商机...')
            # 第一步只查库+洞察报告，跳过机会分析（第二步汇总会再做，避免重复精读）
            query_date_from_es(start, with_opportunities=False)
            print(f'[today] 第二步：增量爬取网站，补上最新发布的公告...')
        run_date_mode(start, end, regions=regions, summary_path=summary_path, no_summary=no_summary)
        return

    if cmd == 'db_date':
        # 从 ES 数据库查询历史某天的商机（不爬网站）
        if len(args) < 2:
            print('用法: python fast_run.py db_date <YYYY-MM-DD>')
            sys.exit(1)
        query_date_from_es(args[1])
        return

    if cmd == 'summarize':
        if len(args) < 2:
            print('用法: python fast_run.py summarize <YYYY-MM-DD> [end]')
            sys.exit(1)
        end = args[2] if len(args) > 2 else None
        summarize_command(args[1], end)
        return

    # 原有模式：all / <region>
    arg = args[0]
    if arg == 'all':
        for region in CRAWLERS:
            print(f'\n===== 开始爬取 {region} =====')
            try:
                run_region(region)
            except Exception as e:
                print(f'[ERROR] {region} 爬取失败: {e}')
                continue
    elif arg in CRAWLERS:
        run_region(arg)
    else:
        print(f'unknown region: {arg}')
        print('支持:', ', '.join(CRAWLERS))
        sys.exit(1)


if __name__ == '__main__':
    main()
