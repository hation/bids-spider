"""存量数据回填：为 ES 中缺少 announce_type 的公告按标题分类标记，中标公告补提取中标单位/金额。

三种模式：
    python scripts/backfill_announce_type.py                 # 分类回填：只处理 announce_type 缺失的，按标题分类写入；中标公告同时提取中标单位/金额
    python scripts/backfill_announce_type.py --meta          # 补提取：对 announce_type=中标公告 但中标单位/金额缺失的重新提取（提取模式增强后用于存量）
    python scripts/backfill_announce_type.py --void          # 补提取：对 announce_type=废标公告 但废标原因缺失的重新提取
    参数：可加数字 N 限制每次处理的条数（分批）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elasticsearch import helpers

from utils.es import ESConnection
from utils.announce import (
    classify_announce_type,
    extract_win_company,
    extract_win_amount,
    extract_void_reason,
    extract_contact,
    _to_text,
)
from analyze_today import extract_money

# 正文废标信号：命中则把误分类的"中标公告"改判废标公告并提取废标原因
_VOID_SIG = __import__('re').compile(
    r'(结果类型[:：]\s*(废标|流标|终止)|废标原因|流标原因|终止原因|废标情形|流标情形|'
    r'无供应商(递交|投标)|无有效(投标|供应商)|投标人不足三家)')


def _safe_tuple(fn, html, default):
    """多返回值提取的容错包装。"""
    try:
        return fn(html)
    except Exception:
        return default


def _safe(fn, html, default):
    """单条提取失败（如正则命中空数字导致 ValueError）不影响整批回填。"""
    try:
        return fn(html)
    except Exception:
        return default


def backfill(batch=None, meta_only=False, void_only=False, contact_only=False, amount_only=False):
    es = ESConnection()
    client = es._client()
    if not client:
        print('[backfill_announce_type] ES 连接失败')
        return

    if void_only:
        # 废标原因回填：废标公告但废标原因尚未提取的（覆盖空串与字段缺失两种空态）
        query = {
            "query": {
                "bool": {
                    "must": [{"term": {"announce_type": "废标公告"}}],
                    "should": [
                        {"term": {"void_reason.keyword": ""}},
                        {"bool": {"must_not": [{"exists": {"field": "void_reason"}}]}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": ["_doc"],
        }
    elif contact_only:
        # 联系人回填：contact_person 尚为空的全部公告
        query = {
            "query": {
                "bool": {
                    "should": [
                        {"term": {"contact_person": ""}},
                        {"bool": {"must_not": [{"exists": {"field": "contact_person"}}]}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": ["_doc"],
        }
    elif amount_only:
        # 项目金额回填：amount_wan 缺失或为 0 的全部公告
        query = {
            "query": {
                "bool": {
                    "should": [
                        {"bool": {"must_not": [{"exists": {"field": "amount_wan"}}]}},
                        {"term": {"amount_wan": 0}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": ["_doc"],
        }
    elif meta_only:
        # 补提取模式：中标公告但中标单位/金额尚未提取的（覆盖空串与字段缺失两种空态）
        query = {
            "query": {
                "bool": {
                    "must": [{"term": {"announce_type": "中标公告"}}],
                    "should": [
                        {"term": {"win_company": ""}},
                        {"bool": {"must_not": [{"exists": {"field": "win_company"}}]}},
                        {"bool": {"must_not": [{"exists": {"field": "win_amount_wan"}}]}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": ["_doc"],
        }
    else:
        # 分类回填模式：只处理 announce_type 缺失的文档（幂等：已标记的一次更新后收敛）
        query = {
            "query": {
                "bool": {
                    "must_not": [{"exists": {"field": "announce_type"}}]
                }
            },
            "sort": ["_doc"],
        }

    seen = 0
    updated = 0
    actions = []
    try:
        resp = client.search(index='tenders', body=query, scroll='10m', size=1000, _source=True)
        sid = resp['_scroll_id']
        hits = resp['hits']['hits']
        while hits:
            for h in hits:
                seen += 1
                src = h.get('_source', {})
                doc_id = h.get('_id')
                title = src.get('title', '')
                if contact_only:
                    html = src.get('html', '') or ''
                    p_, ph_, sub_ = _safe_tuple(extract_contact, html, ('', '', ''))
                    doc = {'contact_person': p_, 'contact_phone': ph_, 'contact_subject': sub_}
                elif amount_only:
                    # 仅提取成功时写入；失败跳过，避免反复扫描无金额公告
                    html = src.get('html', '') or ''
                    unit, val = _safe_tuple(extract_money, _to_text(html), (None, None))
                    if val is None:
                        continue
                    doc = {'amount_wan': val if unit == '万元' else round(val / 10000, 4)}
                elif void_only:
                    html = src.get('html', '') or ''
                    doc = {'void_reason': _safe(extract_void_reason, html, '')}
                elif meta_only:
                    # 只补当前为空的字段，避免把已有值覆盖成空（提取会随正文差异波动）
                    html = src.get('html', '') or ''
                    doc = {}
                    text = _to_text(html)
                    if _VOID_SIG.search(text):
                        # 误分类修正：正文是废标/流标结果的，改判废标公告并提取原因
                        doc['announce_type'] = '废标公告'
                        doc['win_company'] = ''
                        doc['win_amount_wan'] = None
                        doc['void_reason'] = _safe(extract_void_reason, html, '')
                    else:
                        if not (src.get('win_company') or '').strip():
                            doc['win_company'] = _safe(extract_win_company, html, '')
                        if src.get('win_amount_wan') is None:
                            doc['win_amount_wan'] = _safe(extract_win_amount, html, None)
                    if not doc:
                        continue  # 两字段都已有值，跳过不更新
                else:
                    atype = classify_announce_type(title)
                    doc = {'announce_type': atype}
                    if atype == '中标公告':
                        html = src.get('html', '') or ''
                        doc['win_company'] = _safe(extract_win_company, html, '')
                        doc['win_amount_wan'] = _safe(extract_win_amount, html, None)
                actions.append({
                    "_op_type": "update",
                    "_index": "tenders",
                    "_id": doc_id,
                    "doc": doc,
                })
                updated += 1
                if batch and updated >= batch:
                    break
            if batch and updated >= batch:
                break
            resp = client.scroll(scroll_id=sid, scroll='10m')
            hits = resp['hits']['hits']
        try:
            client.clear_scroll(scroll_id=sid)
        except Exception:
            pass
    except Exception as e:
        print(f'[backfill_announce_type] 扫描失败: {e}')
        return

    if actions:
        try:
            success, errors = helpers.bulk(client, actions, chunk_size=500, raise_on_error=False)
            print(f'[backfill_announce_type] 更新成功 {success} 条，失败 {len(errors) if isinstance(errors, list) else 0} 条')
        except Exception as e:
            print(f'[backfill_announce_type] bulk 更新失败: {e}')
    print(f'[backfill_announce_type] 扫描 {seen} 条，提交更新 {len(actions)} 条')


if __name__ == '__main__':
    args = sys.argv[1:]
    meta_only = '--meta' in args
    void_only = '--void' in args
    contact_only = '--contact' in args
    amount_only = '--amount' in args
    batch = next((int(a) for a in args if a.isdigit()), None)
    backfill(batch, meta_only=meta_only, void_only=void_only, contact_only=contact_only, amount_only=amount_only)
