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
