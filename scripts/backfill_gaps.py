"""历史缺口智能补抓：扫描 ES 按地区×日期找断档，逐段调用 fast_run by_date 补抓。

用法：
  # 只扫描出缺口清单（不抓取）
  .venv/bin/python scripts/backfill_gaps.py scan --regions neimenggu,guizhou

  # 扫描并补抓（默认按 7 天一段）
  .venv/bin/python scripts/backfill_gaps.py run --regions neimenggu,guizhou --seg-days 7 --parallel 2

  # 从某地区补（指定日期范围）
  .venv/bin/python scripts/backfill_gaps.py run --regions tianjin --start 2026-10-23 --end 2026-11-08
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from elasticsearch import Elasticsearch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, '.venv', 'bin', 'python')
OUT_DIR = os.path.join(ROOT, 'output', 'backfill')
WINDOW_START = '2026-08-01'  # 扫描窗口下界：早于此日不判缺口（避免历史零散数据算出上千天空档）


def client():
    return Elasticsearch(['http://127.0.0.1:9200'], request_timeout=60)


def _fmt(d):
    return d.strftime('%Y-%m-%d')


def _parse(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


def scan_gaps(regions):
    """返回 {region: [日期字符串...]}，覆盖区间 = max(地区首次入库日, 窗口下界) ~ 昨天。"""
    es = client()
    yesterday = date.today() - timedelta(days=1)
    upper = _fmt(yesterday) + ' 23:59:59'  # 兼容 release_date 的纯日期与带时分秒两种格式
    # 每地区首次入库日（release_date 是 keyword，不能用 min 聚合，改用 terms asc size 1）
    first = es.search(index='tenders', body={
        'size': 0,
        # 排除 release_date 为空串的历史脏数据（否则 terms asc 首桶为空字符串）
        'query': {'bool': {'must_not': [{'term': {'release_date': ''}}]}},
        'aggs': {'r': {'terms': {'field': 'region', 'size': 50},
                       'aggs': {'first': {'terms': {'field': 'release_date', 'size': 1,
                                                    'order': {'_key': 'asc'}}}}}},
    })
    first_day = {}
    for b in first['aggregations']['r']['buckets']:
        fb = b['first']['buckets']
        if fb:
            first_day[b['key']] = _parse(str(fb[0]['key'])[:10])

    # 地区×日期覆盖
    have = defaultdict(set)
    after = None
    while True:
        comp = {'size': 5000, 'sources': [
            {'region': {'terms': {'field': 'region'}}},
            {'day': {'terms': {'field': 'release_date'}}},
        ]}
        if after:
            comp['after'] = after
        body = {'size': 0, 'query': {'range': {'release_date': {'gte': WINDOW_START, 'lte': upper}}},
                'aggs': {'c': {'composite': comp}}}
        resp = es.search(index='tenders', body=body)
        buckets = resp['aggregations']['c']['buckets']
        for b in buckets:
            r = b['key']['region']
            d = str(b['key']['day'])[:10]
            if WINDOW_START <= d <= _fmt(yesterday):
                have[r].add(d)
        after = resp['aggregations']['c'].get('after_key')
        if not buckets or not after:
            break

    gaps = {}
    for r in (regions or first_day.keys()):
        if r not in first_day:
            continue
        start = max(first_day[r], _parse(WINDOW_START))
        miss = []
        d = start
        while d <= yesterday:
            if _fmt(d) not in have[r]:
                miss.append(_fmt(d))
            d += timedelta(days=1)
        if miss:
            gaps[r] = miss
    return gaps


def to_segments(days, seg_days):
    """把日期列表压成连续日期段 [(start,end), ...]，每段不超过 seg_days 天。"""
    if not days:
        return []
    ds = sorted(_parse(x) for x in days)
    segs = []
    cur_s = cur_e = ds[0]
    for d in ds[1:]:
        if d == cur_e + timedelta(days=1) and (d - cur_s).days < seg_days - 1:
            cur_e = d
        else:
            segs.append((cur_s, cur_e))
            cur_s = cur_e = d
    segs.append((cur_s, cur_e))
    return [(_fmt(s), _fmt(e)) for s, e in segs]


def _read_status(log_path, region):
    """从单段日志解析 fast_run 的结果状态（ok / no_match / no_date / error: ...）。

    fast_run 无论站点成功失败都 exit 0（内部捕获异常），故熔断不能看退出码，必须看日志状态。
    """
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        for line in content.splitlines():
            if f'[{region}]' in line and ', kept=' in line:
                return line.split(f'[{region}]', 1)[1].strip().split(', kept=')[0].strip()
    except Exception:
        pass
    return 'unknown'


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_scan = sub.add_parser('scan')
    p_scan.add_argument('--regions', default='')

    p_run = sub.add_parser('run')
    p_run.add_argument('--regions', default='')
    p_run.add_argument('--start', default='')
    p_run.add_argument('--end', default='')
    p_run.add_argument('--seg-days', type=int, default=7)
    p_run.add_argument('--parallel', type=int, default=2)
    p_run.add_argument('--max-fail', type=int, default=3)

    args = ap.parse_args()
    regions = [r.strip() for r in args.regions.split(',') if r.strip()]

    os.makedirs(OUT_DIR, exist_ok=True)
    gaps = scan_gaps(regions or None)
    if not gaps:
        print('无缺口')
        return
    print('缺口概览:')
    for r, days in sorted(gaps.items()):
        print(f'  {r:12s} {len(days):3d} 天  {days[0]} ~ {days[-1]}')

    if args.cmd == 'scan':
        return

    run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    progress_path = os.path.join(OUT_DIR, f'{run_id}.jsonl')

    # 显式日期范围覆盖
    if args.start and args.end:
        d = _parse(args.start)
        endp = _parse(args.end)
        days = []
        while d <= endp:
            days.append(_fmt(d))
            d += timedelta(days=1)
        gaps = {r: days for r in regions}

    tasks = []
    for r, days in gaps.items():
        for (s, e) in to_segments(days, args.seg_days):
            tasks.append((r, s, e))
    print(f'共 {len(tasks)} 个补抓段，并发 {args.parallel}')

    fail_count = defaultdict(int)
    done = 0
    while tasks:
        # 组装本批：跨地区并发，但同一地区最多一个任务在跑（避免同站并发触发风控）
        batch = []
        rest = []
        used_regions = set()
        for item in tasks:
            r, s, e = item
            if len(batch) >= args.parallel:
                rest.append(item)
                continue
            if r in used_regions:
                rest.append(item)
                continue
            if fail_count[r] >= args.max_fail:
                print(f'  [skip] {r} 连续失败 {fail_count[r]} 次，熔断跳过 {s}~{e}')
                continue
            batch.append(item)
            used_regions.add(r)
        tasks = rest
        if not batch:
            break
        procs = []
        for r, s, e in batch:
            log_path = os.path.join(ROOT, 'logs', f'backfill_{run_id}_{r}_{s}_{e}.log')
            logf = open(log_path, 'w', encoding='utf-8')
            procs.append((r, s, e, log_path, logf, subprocess.Popen(
                [PY, 'fast_run.py', 'by_date', s, e, '--regions', r, '--no-summary'],
                cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT)))
        for r, s, e, log_path, logf, p in procs:
            p.wait()
            logf.close()
            done += 1
            status = _read_status(log_path, r)
            if status.startswith('error'):
                fail_count[r] += 1
            else:
                fail_count[r] = 0
            rec = {'region': r, 'start': s, 'end': e, 'status': status}
            with open(progress_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            print(f'  [{done}/{done + len(tasks)}] {r} {s}~{e} -> {status}')
    print(f'补抓完成，进度: {progress_path}')


if __name__ == '__main__':
    main()
