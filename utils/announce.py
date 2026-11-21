"""中标/废标公告分类与提取工具

- classify_announce_type: 按标题关键词把公告归为 招标公告 / 中标公告 / 废标公告
- extract_win_company: 从正文提取中标单位名称（中标供应商/成交单位/中标人 等）
- extract_win_amount: 从正文提取中标金额（兼容 元/万元），返回万元 float 或 None
"""
import re

import html2text


def _to_text(html):
    h = html2text.HTML2Text()
    h.ignore_links = True
    h.ignore_images = True
    h.body_width = 0
    return h.handle(html or '')


def classify_announce_type(title):
    """标题关键词分类：废标/流标/终止/失败 -> 废标公告；中标/成交/结果 -> 中标公告；其余 -> 招标公告。"""
    t = str(title)
    if any(k in t for k in ('废标', '流标', '终止', '失败')):
        return '废标公告'
    if any(k in t for k in ('中标', '成交', '结果')):
        return '中标公告'
    return '招标公告'


_COMPANY_PATS = [
    r'(?:中标供应商名称|成交供应商名称|中标单位名称|成交单位名称)\s*[:：]\s*([^\s，。,；;、|\n]{2,60})',
    r'(?:中标供应商|成交供应商|中标单位|成交单位|中标人|成交人)\s*[（(]?[^）)]{0,8}[)）]?\s*[:：]\s*([^\s，。,；;、|\n]{2,60})',
    r'供应商名称\s*[:：]\s*([^\s，。,；;、|\n]{2,60})',
]


def extract_win_company(html):
    """从正文提取中标单位名称，未提取到返回空串。"""
    text = _to_text(html)
    text = text.replace('\u3000', ' ')
    for pat in _COMPANY_PATS:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return ''


def extract_win_amount(html):
    """提取中标/成交金额，兼容 元/万元，返回万元 float；未提取到返回 None。"""
    text = _to_text(html)
    # 覆盖「中标（成交）金额 / 中标(成交)金额」变体
    kws = ['中标（成交）金额', '中标(成交)金额', '中标金额', '成交金额']
    for kw in kws:
        m = re.search(kw + r'[（(]?万元[)）]?[:：]?\s*([\d,，.]+)', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', ''))
        m = re.search(kw + r'[（(]?元[)）]?[:：]?\s*([\d,，.]+)', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', '')) / 10000
        m = re.search(kw + r'[:：]\s*([\d,，.]+)\s*万元', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', ''))
        m = re.search(kw + r'[:：]\s*([\d,，.]+)\s*元', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', '')) / 10000
    return None
