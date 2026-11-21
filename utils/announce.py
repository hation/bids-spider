"""中标/废标公告分类与提取工具

- classify_announce_type: 按标题关键词把公告归为 招标公告 / 中标公告 / 废标公告
- extract_win_company: 从正文提取中标单位名称（中标供应商/成交单位/中标人 等）
- extract_win_amount: 从正文提取中标金额（兼容 元/万元），返回万元 float 或 None
"""
import re

import html2text


def _to_text(html):
    """HTML 源码转可读纯文本；若输入已是纯文本（无标签）则原样返回，避免二次转换破坏表格结构。"""
    h = html2text.HTML2Text()
    h.ignore_links = True
    h.ignore_images = True
    h.body_width = 0
    text = h.handle(html or '')
    # 无标签的纯文本（如 _enrich_meta 传入的 _html_to_text 结果）二次转换会破坏
    # `|` 表格与 `****` 加粗标记，识别到无 HTML 标签时直接用原文
    if '<' not in str(html)[:200]:
        return html or ''
    return text


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
    # 公告信息表字段行：`成交供应商 | XX公司;`（云南等站点，先于通用冒号模式，避免误命中）
    r'成交供应商\s*\|[ ]*([^;\n，。]+?)(?:;|$)',
    # 字段行冒号格式：`供应商名称：XX公司`（辽宁等站点；无表格，直接取冒号后公司名）
    r'供应商名称\s*[:：]\s*([^\s，。,；;、|\n]{2,60})',
    # 正文表格表头：`供应商名称|供应商地址|...` 后数据行第一格（云南/山西等站点，带序号）
    r'供应商名称[^\n]*\n[^\n]*\n\s*[\d一二三四五六七八九十]+\|[ ]*([^|\n，。；;]+)',
    # 正文表格表头（无序号）：`供应商名称 | ...` 后分隔行 + 数据行第一格（内蒙古等站点，
    # 数据行直接以公司名开头，如"中兴华会计师事务所（特殊普通合伙）内蒙古分所 | ..."）
    r'供应商名称[^\n]*\n[^\n]*\n\s*([^\s|，。；;][^|\n，。；;]{2,60})',
    # 通用冒号格式（中标供应商/成交供应商/中标人 等）
    r'(?:中标供应商|成交供应商|中标单位|成交单位|中标人|成交人)\s*[（(]?[^）)]{0,8}[)）]?\s*[:：]\s*([^\s，。,；;、|\n]{2,60})',
]


def extract_win_company(html):
    """从正文提取中标单位名称，未提取到返回空串。

    支持两类来源格式：
    - 冒号格式（"中标供应商：XX公司" / "供应商名称：XX"）
    - PDF 表格格式（表头"中标人"后数据区取公司名，兼容断行拼接）
    """
    text = _to_text(html)
    text = text.replace('\u3000', ' ')
    for pat in _COMPANY_PATS:
        m = re.search(pat, text)
        if m:
            val = m.group(1).strip()
            # 清洗"牵头供应商：""投标联合体：""联合体："等前缀与尾部分号，避免夹带说明文字
            val = re.sub(r'^(?:牵头供应商|投标联合体|联合体供应商|联合体)[：:]\s*', '', val)
            val = re.sub(r'[；;]\s*$', '', val).strip()
            # 联合体/多公司：仅当结果含多公司标记（投标联合体/、/，）时截断到第一个公司名后缀，
            # 否则原样返回（避免"中兴华会计师事务所（特殊普通合伙）内蒙古分所"被后缀提前截断丢失括号注释）
            if re.search(r'(投标联合体|联合体|、|，)', val):
                m3 = re.search(
                    r'[\u4e00-\u9fa5A-Za-z（）()·]{2,40}?(?:有限公司|股份有限公司|集团|公司|研究院|事务所|中心|医院|学校|大学|银行)',
                    val)
                if m3:
                    val = m3.group(0).strip()
            if val:
                return val
    # PDF 表格兜底：表头（中标人/中标单位/中标供应商）后的数据区取公司名
    # 先拼接断行并去掉空格（PDF 表格公司名可能被拆成"XX有 限公司"），
    # 再剔除常见表头词（中标价/工期/服务期/质量承诺/项目负责人），避免被整体吞入匹配
    m = re.search(r'(?:中标人|中标单位|中标供应商|成交供应商)', text)
    if m:
        seg = text[m.end():m.end() + 150].replace('\n', '').replace(' ', '')
        for head in ('中标价（元）', '中标价(元)', '中标价', '工期/服务期（天）质量承诺', '工期/服务期（天）',
                     '服务期（天）质量承诺', '工期', '服务期', '质量承诺', '项目负责人', '中标内容'):
            seg = seg.replace(head, '')
        m2 = re.search(
            r'([\u4e00-\u9fa5A-Za-z（）()·]{2,40}?(?:有限公司|股份有限公司|集团|公司|研究院|事务所|中心|医院|学校|大学|银行))',
            seg)
        if m2:
            return m2.group(1).strip()
    return ''


def extract_win_amount(html):
    """提取中标/成交金额，兼容 元/万元，返回万元 float；未提取到返回 None。

    支持冒号格式（"中标金额：X"）与 PDF 表格格式（表头"中标价（元）"后数据区金额）。
    """
    text = _to_text(html)
    # 覆盖「中标（成交）金额 / 中标(成交)金额 / 总成交金额」变体
    kws = ['中标（成交）金额', '中标(成交)金额', '总成交金额', '中标金额', '成交金额']
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
    # 云南正文表格：`报价：2444384.23（元）`（单位元 -> 万元）
    m = re.search(r'报价[:：]\s*([\d,，.]+)\s*[（(]?元[)）]?', text)
    if m:
        return float(m.group(1).replace(',', '').replace('，', '')) / 10000
    # PDF 表格兜底：表头"中标价（元）/中标价(元)/中标金额（元）"后数据区金额（单位元 -> 万元）
    m = re.search(r'中标价[（(]?元[)）]?|中标金额[（(]?元[)）]?', text)
    if m:
        seg = text[m.end():m.end() + 80].replace('\n', '')
        m2 = re.search(r'([\d,，.]+)', seg)
        if m2:
            return float(m2.group(1).replace(',', '').replace('，', '')) / 10000
    return None
