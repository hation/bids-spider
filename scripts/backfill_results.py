"""结果公告（中标/废标）历史补抓编排：按地区×7天段调用 fast_run.py results。

用法：
  .venv/bin/python scripts/backfill_results.py --start 2026-08-22 --end 2026-11-21 --parallel 2
  .venv/bin/python scripts/backfill_results.py --regions guangdong,hainan --start 2026-08-22 --end 2026-11-21
"""
import argparse, json, os, subprocess, sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, '.venv', 'bin', 'python')
sys.path.insert(0, ROOT)
from crawler.result_collector import RESULT_CRAWLERS

OUT = os.path.join(ROOT, 'output', 'backfill')

def _parse(s): return datetime.strptime(s, '%Y-%m-%d').date()
def _fmt(d): return d.strftime('%Y-%m-%d')

def segments(start, end, seg_days=7):
    segs, cur = [], start
    while cur <= end:
        seg_end = min(cur + timedelta(days=seg_days - 1), end)
        segs.append((_fmt(cur), _fmt(seg_end)))
        cur = seg_end + timedelta(days=1)
    return segs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--regions', default='')
    ap.add_argument('--seg-days', type=int, default=7)
    ap.add_argument('--parallel', type=int, default=2)
    ap.add_argument('--gap', type=int, default=20, help='段间间隔秒数（防风控）')
    args = ap.parse_args()

    regions = [r.strip() for r in args.regions.split(',') if r.strip()] or list(RESULT_CRAWLERS)
    # 过滤未注册结果采集器的地区（否则 fast_run 只打印"未知结果地区"并空跑，被误记为 ok）
    unknown = [r for r in regions if r not in RESULT_CRAWLERS]
    if unknown:
        print(f'⚠️ 以下地区未注册结果采集器，已跳过：{", ".join(unknown)}', flush=True)
    regions = [r for r in regions if r in RESULT_CRAWLERS]
    if not regions:
        print('❌ 没有任何已注册的结果采集器地区，退出', flush=True)
        sys.exit(1)
    os.makedirs(OUT, exist_ok=True)
    run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    progress = os.path.join(OUT, f'results_{run_id}.jsonl')
    tasks = [(r, s, e) for r in regions for (s, e) in segments(_parse(args.start), _parse(args.end), args.seg_days)]
    print(f'共 {len(tasks)} 段，并发 {args.parallel}', flush=True)

    import time
    idx = 0
    while idx < len(tasks):
        batch = tasks[idx: idx + args.parallel]; idx += args.parallel
        procs = []
        for r, s, e in batch:
            log = os.path.join(ROOT, 'logs', f'results_{run_id}_{r}_{s}_{e}.log')
            f = open(log, 'w', encoding='utf-8')
            procs.append((r, s, e, f, subprocess.Popen(
                [PY, 'fast_run.py', 'results', s, e, '--regions', r, '--no-summary'],
                cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)))
        for r, s, e, f, p in procs:
            p.wait(); f.close()
            status = 'ok' if p.returncode == 0 else f'exit{p.returncode}'
            rec = {'region': r, 'start': s, 'end': e, 'status': status}
            with open(progress, 'a', encoding='utf-8') as pf:
                pf.write(json.dumps(rec, ensure_ascii=False) + '\n')
            print(f'  {r} {s}~{e} -> {status}', flush=True)
        time.sleep(args.gap)
    print(f'完成，进度 {progress}')

if __name__ == '__main__':
    main()
