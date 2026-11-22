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
    # 字段行冒号格式：`供应商名称：XX公司`（辽宁/吉林等站点；无表格，直接取冒号后公司名，
    # 遇"供应商地址/成交金额/中标金额"等下一字段停止；字段词前允许空白/换行）
    r'供应商名称\s*[:：]\s*([^\s，。,；;、|\n]{2,60}?)(?=[\s\n]*(?:供应商地址|成交金额|中标金额|联系电话|联系人))',
    # 正文表格表头：`供应商名称|供应商地址|...` 后数据行第一格（云南/山西等站点，带序号；
    # 表头行不含冒号，避免误取字段行格式"供应商名称：XX"的下一行）
    r'供应商名称[^:：\n]*\n[^\n]*\n\s*[\d一二三四五六七八九十]+\|[ ]*([^|\n，。；;]+)',
    # 正文表格表头（无序号）：`供应商名称 | ...` 后分隔行 + 数据行第一格（内蒙古等站点，
    # 数据行直接以公司名开头，如"中兴华会计师事务所（特殊普通合伙）内蒙古分所 | ..."）
    # 注意：表头行不能含冒号（字段行格式"供应商名称：XX"由上方冒号模式处理，避免误取下一行）
    r'供应商名称[^:：\n]*\n[^\n]*\n\s*([^\s|，。；;][^|\n，。；;]{2,60})',
    # 通用冒号格式（中标供应商/成交供应商/中标人 等）
    r'(?:中标供应商|成交供应商|中标单位|成交单位|中标人|成交人)\s*[（(]?[^）)]{0,8}[)）]?\s*[:：]\s*([^\s，。,；;、|\n]{2,60})',
]


_ORG_SUFFIX = re.compile(
    r'(有限公司|股份有限公司|有限责任公司|集团|研究院|研究所|设计院|事务所|中心|医院|学校|学院|大学|'
    r'银行|合作社|联合社|工作室|服务部|商行|厂|站|队|院|社|馆|分公司|支公司|个体工商户|店)$')


def _clean_company(val):
    """清洗中标单位：去掉前缀噪声；无合法单位后缀的（如项目名）返回空串。"""
    if not val:
        return ''
    v = val.strip()
    v = re.sub(r'^(?:牵头供应商|投标联合体|联合体供应商|联合体)[：:]\s*', '', v)
    v = re.sub(r'[；;]\s*$', '', v).strip()
    # 去 html2text 加粗/斜体标记与尾部包号、"（个体工商户）" 等噪声
    v = v.strip('*_ ').strip()
    v = re.sub(r'[（(]\d+包[）)]$', '', v).strip()
    v = re.sub(r'[（(]个体工商户[）)]$', '', v).strip()
    for noise in ('原公告的合同包', '合同包', '采购包', '分包'):
        if v.startswith(noise):
            return ''
    if not _ORG_SUFFIX.search(v):
        return ''
    return v


def _table_cell_after_header(text, header_kw):
    """在 markdown 表格里按表头列位置取数据行单元格（河南等站点：供应商名称不在第一列）。

    例：
        | 包号 | 采购内容 | 供应商名称 | 地址 | 中标金额 |
        |---|---|---|---|---|
        | XX-1 | 内容 | 河南鼎实建筑工程有限公司 | ... |
    """
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if header_kw in line and '|' in line:
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            col = next((j for j, c in enumerate(cells) if header_kw in c), None)
            if col is None:
                continue
            for data in lines[i + 1:i + 6]:
                if '|' not in data:
                    continue
                dc = [c.strip() for c in data.strip().strip('|').split('|')]
                if len(dc) > col:
                    val = dc[col]
                    # 跳过表头分隔行（---）与空单元格
                    if val and not re.fullmatch(r'[-:\s]*', val):
                        return val
    return ''


def extract_win_company(html):
    """从正文提取中标单位名称，未提取到返回空串。

    支持两类来源格式：
    - 冒号格式（"中标供应商：XX公司" / "供应商名称：XX"）
    - PDF 表格格式（表头"中标人"后数据区取公司名，兼容断行拼接）
    """
    text = _to_text(html)
    text = text.replace('\u3000', ' ')
    for idx, pat in enumerate(_COMPANY_PATS):
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
                cleaned = _clean_company(val)
                if cleaned:
                    return cleaned
                # 表格类模式（idx 3/4）可能取错列（如"包号"列），清洗失败时继续尝试后续模式
                if idx in (3, 4):
                    continue
                return ''
    # markdown 表格按表头列位置取（河南等站点：包号/内容在前，供应商名称在第 3 列）
    for kw in ('供应商名称', '成交供应商', '中标供应商', '中标单位', '中标人'):
        cell = _table_cell_after_header(text, kw)
        if cell:
            cleaned = _clean_company(cell)
            if cleaned:
                return cleaned
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
            cleaned = _clean_company(m2.group(1))
            if cleaned:
                return cleaned
    return ''


def extract_win_amount(html):
    """提取中标/成交金额，兼容 元/万元，返回万元 float；未提取到返回 None。

    支持冒号格式（"中标金额：X"）与 PDF 表格格式（表头"中标价（元）"后数据区金额）。
    """
    text = _to_text(html)
    # 覆盖「中标（成交）金额 / 中标(成交)金额 / 中标成交金额 / 总成交金额」变体
    kws = ['中标（成交）金额', '中标(成交)金额', '中标成交金额', '总成交金额', '中标金额', '成交金额']
    for kw in kws:
        m = re.search(re.escape(kw) + r'[（(]?万元[)）]?[:：]?\s*([\d,，.]+)', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', ''))
        m = re.search(re.escape(kw) + r'[（(]?元[)）]?[:：]?\s*([\d,，.]+)', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', '')) / 10000
        m = re.search(re.escape(kw) + r'[:：]\s*\**\s*([\d,，.]+)\s*\**\s*万元', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', ''))
        # 湖北等站点格式：`中标（成交）金额：47.37(万元)`（数字后括号单位）
        m = re.search(re.escape(kw) + r'[:：]\s*([\d,，.]+)\s*[（(]万元[)）]', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', ''))
        # 数字+元（兼容 html2text 加粗标记 `**` 干扰，如湖南 `金额：**1545469.56**元`）
        m = re.search(re.escape(kw) + r'[:：]\s*\**\s*([\d,，.]+)\s*\**\s*元', text)
        if m:
            return float(m.group(1).replace(',', '').replace('，', '')) / 10000
        # 湖南等站点：`中标成交金额：696650.0000`（无括号无单位，大额数字按元折算）
        m = re.search(re.escape(kw) + r'[:：]\s*([\d,，.]+)(?!\s*(?:元|万元|[（(]))', text)
        if m:
            val = float(m.group(1).replace(',', '').replace('，', ''))
            if val >= 10000:
                return val / 10000
    # 宁夏等站点：中文大写金额 + 括号内数字元，`中标金额：陆佰玖拾壹万...伍角捌分(￥:6913924.58)`
    m = re.search(r'中标金额[：:][^\n]{0,80}?[（(]\s*[￥¥]?\s*[:：]?\s*([\d,，.]+)\s*[)）]', text)
    if m:
        return float(m.group(1).replace(',', '').replace('，', '')) / 10000
    # 云南正文表格：`报价：2444384.23（元）`（单位元 -> 万元）
    m = re.search(r'报价[:：]\s*([\d,，.]+)\s*[（(]?元[)）]?', text)
    if m:
        return float(m.group(1).replace(',', '').replace('，', '')) / 10000
    # 福建等站点表格：`中标（成交）金额 | 评审价格` 表头后数据行金额（`1,549,900.00元`）
    m = re.search(r'中标[（(]成交[)）]金额', text)
    if m:
        seg = text[m.end():m.end() + 250]
        m2 = re.search(r'([\d,，.]+)\s*元', seg)
        if m2:
            return float(m2.group(1).replace(',', '').replace('，', '')) / 10000
    # PDF 表格兜底：表头"中标价（元）/中标价(元)/中标金额（元）"后数据区金额（单位元 -> 万元）
    m = re.search(r'中标价[（(]?元[)）]?|中标金额[（(]?元[)）]?', text)
    if m:
        seg = text[m.end():m.end() + 80].replace('\n', '')
        m2 = re.search(r'([\d,，.]+)', seg)
        if m2:
            return float(m2.group(1).replace(',', '').replace('，', '')) / 10000
    return None


def extract_void_reason(html):
    """从废标/流标/终止公告正文提取废标原因，未提取到返回空串。"""
    text = _to_text(html).replace('\u3000', ' ')
    for kw in ('废标原因', '流标原因', '终止原因', '失败原因', '废标情形', '流标情形'):
        m = re.search(re.escape(kw) + r'\s*[:：]?\s*([^\n]{2,120})', text)
        if m:
            val = m.group(1).strip().strip('。;；')
            if val:
                return val
    return ''


_CONTACT_PATS = [
    r'(?:中标供应商|成交供应商|中标单位|中标人)[^\n]{0,40}?(?:联系人|项目联系人)\s*[:：]\s*([^\s，。,；;\n]{2,20})',
    r'(?:中标供应商|成交供应商|中标单位|中标人)[^\n]{0,40}?(?:联系电话|电话|联系方式)\s*[:：]\s*([\d\-()（）转\s]{7,25})',
    r'(?:联系人|项目联系人)\s*[:：]\s*([^\s，。,；;\n]{2,20})',
    r'(?:联系电话|联系方式|电话)\s*[:：]\s*([\d\-()（）转\s]{7,25})',
]


def extract_contact(html):
    """提取联系人与联系电话，返回 (person, phone, subject)。

    subject：命中"中标供应商/成交供应商/中标单位/中标人"前置上下文的记为"中标供应商"，
    否则（采购人/代理机构段落）记为"采购人/代理"。未提取到返回空串。
    """
    text = _to_text(html).replace('\u3000', ' ')
    person = phone = ''
    subject = ''
    # 第一轮：中标供应商前置（优先）
    for pat in _CONTACT_PATS[:2]:
        m = re.search(pat, text)
        if m:
            val = re.sub(r'\s+', '', m.group(1)).strip()
            if '联系' in pat or '电话' in pat:
                phone = phone or val
            else:
                person = person or val
            subject = subject or '中标供应商'
    # 第二轮：全文兜底（采购人/代理）
    if not person:
        m = re.search(_CONTACT_PATS[2], text)
        if m:
            person = re.sub(r'\s+', '', m.group(1)).strip()
            subject = subject or '采购人/代理'
    if not phone:
        m = re.search(_CONTACT_PATS[3], text)
        if m:
            phone = re.sub(r'\s+', '', m.group(1)).strip()
            subject = subject or '采购人/代理'
    return person, phone, subject
