"""关键词商机检索：标题+正文加权搜索，关联中标/废标情况，导出 Excel + Markdown。

用法：
  .venv/bin/python scripts/search_tenders.py "服务器"                      # 默认近3个月
  .venv/bin/python scripts/search_tenders.py "信创 数据库" --start 2026-09-01 --end 2026-11-22
  .venv/bin/python scripts/search_tenders.py "算力" --all                  # 全库
  .venv/bin/python scripts/search_tenders.py "服务器" --size 5000
"""
import argparse
import os
import re
import sys
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from elasticsearch import Elasticsearch

OUTPUT_BASE = os.path.join('output', '关键词检索')

# 项目编号正则（河北 Z13xxx、通用字母+数字、JG 等）
_CODE_PATTERNS = [
    re.compile(r'([A-Z]{1,3}\d{10,})'),
    re.compile(r'([A-Z]{2,6}[-_]?[A-Z0-9]{6,})'),
    re.compile(r'(JG[A-Z0-9\-]{4,})'),
]
# 标题后缀（归一化时去除）
_SUFFIX = re.compile(
    r'(中标（成交）结果公告|中标\(成交\)结果公告|中标结果公告|中标候选人公示|成交结果公告|'
    r'结果更正公告|结果公告|中标公告|成交公告|废标公告|终止公告|流标公告|采购公告|招标公告|'
    r'竞争性磋商公告|竞争性谈判公告|询价公告|单一来源公告|第[一二三四五六七八九十\d]+次|'
    r'[（(]第?[一二三四五六七八九十\d]+包[)）]|[（(]第[一二三四五六七八九十\d]+次[)）])')


def extract_code(title):
    t = str(title or '')
    for p in _CODE_PATTERNS:
        m = p.search(t)
        if m:
            return m.group(1)
    return ''


def normalize_title(title):
    t = str(title or '')
    t = _SUFFIX.sub('', t)
    t = re.sub(r'[\s　]+', '', t)
    return t[:24]


def search(keyword, start=None, end=None, size=2000):
    es = Elasticsearch(['http://127.0.0.1:9200'], request_timeout=120)
    must = []
    if start and end:
        must.append({'range': {'release_date': {'gte': start, 'lte': end + ' 23:59:59'}}})
    body = {
        'size': size,
        'query': {
            'bool': {
                'must': must,
                'should': [
                    {'match': {'title': {'query': keyword, 'boost': 3}}},
                    {'match': {'html': {'query': keyword}}},
                ],
                'minimum_should_match': 1,
            }
        },
        'sort': [{'release_date': {'order': 'desc'}}],
        '_source': ['region', 'href', 'title', 'release_date', 'amount_wan', 'bid_deadline',
                    'announce_type', 'win_company', 'win_amount_wan', 'void_reason',
                    'contact_person', 'contact_phone', 'contact_subject'],
    }
    r = es.search(index='tenders', body=body)
    return r['hits']['hits']


def build_rows(hits, keyword):
    """分派招标/中标/废标，关联后输出招标项目行。"""
    tenders, wins, voids = [], [], []
    for h in hits:
        s = h['_source']
        at = s.get('announce_type', '')
        (wins if at == '中标公告' else voids if at == '废标公告' else tenders).append(s)

    # 关联索引：编号 -> 中标/废标
    win_by_code, void_by_code = {}, {}
    win_by_title, void_by_title = {}, {}
    for w in wins:
        c = extract_code(w.get('title'))
        if c:
            win_by_code.setdefault(c, w)
        win_by_title.setdefault(normalize_title(w.get('title')), w)
    for v in voids:
        c = extract_code(v.get('title'))
        if c:
            void_by_code.setdefault(c, v)
        void_by_title.setdefault(normalize_title(v.get('title')), v)

    rows = []
    for t in tenders or []:
        code = extract_code(t.get('title'))
        key = normalize_title(t.get('title'))
        w = win_by_code.get(code) if code else None
        w = w or win_by_title.get(key)
        v = void_by_code.get(code) if code else None
        v = v or void_by_title.get(key)
        rows.append({
            '关键词': keyword,
            '项目标题': t.get('title', ''),
            '招标链接': t.get('href', ''),
            '地区': t.get('region', ''),
            '发布日期': (t.get('release_date') or '')[:10],
            '投标截止': t.get('bid_deadline', ''),
            '项目金额(万)': t.get('amount_wan'),
            '中标单位': (w or {}).get('win_company', ''),
            '中标金额(万)': (w or {}).get('win_amount_wan'),
            '中标时间': ((w or {}).get('release_date') or '')[:10],
            '中标公告链接': (w or {}).get('href', ''),
            '废标原因': (v or {}).get('void_reason', ''),
            '联系人': (w or t).get('contact_person', '') or t.get('contact_person', ''),
            '联系电话': (w or t).get('contact_phone', '') or t.get('contact_phone', ''),
            '联系方式主体': (w or {}).get('contact_subject', '') or t.get('contact_subject', ''),
        })
    # 无招标公告但命中的中标/废标也列出（避免丢失）
    covered = {r['招标链接'] for r in rows}
    for w in wins:
        if w.get('href') in covered:
            continue
        rows.append({
            '关键词': keyword, '项目标题': w.get('title', ''), '招标链接': '',
            '地区': w.get('region', ''), '发布日期': (w.get('release_date') or '')[:10],
            '投标截止': '', '项目金额(万)': None,
            '中标单位': w.get('win_company', ''), '中标金额(万)': w.get('win_amount_wan'),
            '中标时间': (w.get('release_date') or '')[:10], '中标公告链接': w.get('href', ''),
            '废标原因': '', '联系人': w.get('contact_person', ''),
            '联系电话': w.get('contact_phone', ''), '联系方式主体': w.get('contact_subject', ''),
        })
    return rows


def write_outputs(keyword, rows, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    df = pd.DataFrame(rows)
    xlsx = os.path.join(out_dir, 'result.xlsx')
    df.to_excel(xlsx, index=False)
    # Markdown 汇总
    lines = [f'# 关键词「{keyword}」商机检索结果', '',
             f'- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}',
             f'- 命中条数：{len(rows)}',
             f'- 有中标结果：{sum(1 for r in rows if r["中标单位"])}',
             f'- 有废标信息：{sum(1 for r in rows if r["废标原因"])}', '']
    if rows:
        from collections import Counter
        reg = Counter(r['地区'] for r in rows)
        lines += ['## 地区分布', ''] + [f'- {k}: {v}' for k, v in reg.most_common(10)] + ['']
        top = sorted([r for r in rows if r.get('项目金额(万)')], key=lambda x: -x['项目金额(万)'])[:10]
        if top:
            lines += ['## 金额 Top10', '', '| 项目 | 地区 | 金额(万) | 截止 |', '|---|---|---|---|']
            lines += [f"| {r['项目标题'][:40]} | {r['地区']} | {r['项目金额(万)']} | {r['投标截止']} |" for r in top]
            lines.append('')
    md = os.path.join(out_dir, 'summary.md')
    with open(md, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return xlsx, md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('keyword')
    ap.add_argument('--start', default='')
    ap.add_argument('--end', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--size', type=int, default=2000)
    args = ap.parse_args()

    start, end = None, None
    if not args.all:
        end = args.end or date.today().isoformat()
        start = args.start or (date.today() - timedelta(days=90)).isoformat()
    hits = search(args.keyword, start, end, args.size)
    rows = build_rows(hits, args.keyword)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_kw = re.sub(r'[^\w\u4e00-\u9fa5-]', '_', args.keyword)[:30]
    out_dir = os.path.join(OUTPUT_BASE, f'{safe_kw}_{stamp}')
    xlsx, md = write_outputs(args.keyword, rows, out_dir)
    scope = '全库' if args.all else f'{start}~{end}'
    print(f'关键词「{args.keyword}」范围[{scope}] 命中 {len(hits)} 条，导出 {len(rows)} 行')
    print(f'  Excel: {xlsx}')
    print(f'  汇总:  {md}')


if __name__ == '__main__':
    main()
