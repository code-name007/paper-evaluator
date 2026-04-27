#!/usr/bin/env python3
"""
论文情况评价 & 高水平期刊匹配系统
哈尔滨医科大学人事处专用
"""
import streamlit as st
import json
import re
import time
import pandas as pd
import requests
from pathlib import Path
import io
import sys

# ===== PDF 扫描件识别 =====
def extract_text_from_pdf(file_bytes, filename=""):
    """
    从 PDF 中提取文字。
    优先使用 pdfplumber 直接提取文字；
    若提取文字少于 100 字符（疑似扫描件），则用 pymupdf 渲染页面 + pytesseract OCR。
    返回提取到的纯文本，是否为扫描件。
    """
    import tempfile, os
    text = ""
    is_scanned = False

    # ---- 方案1：pdfplumber 直接提取 ----
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            page_texts = []
            for page in pdf.pages:
                t = page.extract_text() or ""
                page_texts.append(t)
            text = "\n".join(page_texts)
    except Exception:
        pass

    # ---- 判断是否为扫描件（提取文字过少） ----
    if len(text.strip()) < 100:
        is_scanned = True
        text = ""
        tmp_path = None
        try:
            import fitz
            import pytesseract
            from PIL import Image

            # 写入临时文件（fitz.open 不支持 BytesIO）
            tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
            tmp.write(file_bytes)
            tmp.close()
            tmp_path = tmp.name

            with fitz.open(tmp_path) as doc:
                ocr_pages = []
                for page_num, page in enumerate(doc):
                    mat = fitz.Matrix(2, 2)   # 2x zoom ≈ 144 DPI，兼顾速度与质量
                    pix = page.get_pixmap(matrix=mat)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    ocr_text = pytesseract.image_to_string(
                        img, lang='chi_sim+eng', config='--psm 6'
                    )
                    if ocr_text.strip():
                        ocr_pages.append(ocr_text.strip())
                text = "\n".join(ocr_pages)
        except Exception as e:
            sys.stderr.write(f"OCR failed: {e}\n")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return text, is_scanned


st.set_page_config(
    page_title="论文评价系统 | 哈医大人事处",
    page_icon="📖",
    layout="wide"
)

# ===== 加载期刊数据 =====
@st.cache_data
def load_journals():
    path = Path(__file__).parent / "journals_data.json"
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # 按 name 建索引
    name_map = {}
    issn_map = {}
    for k, v in data.items():
        if len(k) > 5:
            name_map[k.lower().strip()] = v
        elif '-' in k or k.replace(' ', '').isdecimal():
            issn_map[k.strip()] = v
    return name_map, issn_map

TIER_COLORS = {
    '高水平1类': '🔴',
    '高水平2类': '🟠',
    '高水平3类': '🟡',
    '高水平4类': '🟢',
    '高水平5A类': '🔵',
    '高水平5B类': '🔵',
}

TIER_BG = {
    '高水平1类': '#ffe5e5',
    '高水平2类': '#fff3e0',
    '高水平3类': '#fffde7',
    '高水平4类': '#e8f5e9',
    '高水平5A类': '#e3f2fd',
    '高水平5B类': '#e3f2fd',
}

def get_tier_label(v):
    return v['tier'] if v else None

def match_journal(journal_name, name_map, issn_map, session):
    """匹配期刊，返回匹配结果"""
    name_clean = journal_name.lower().strip().replace(' ', '')
    # 精确匹配
    if name_clean in name_map:
        return name_map[name_clean]
    # 模糊匹配（包含）
    for key, val in name_map.items():
        if name_clean in key or key in name_clean:
            return val
    # 去掉冠词再试
    for alt in [name_clean.replace('the',''), name_clean.replace('-',' '), name_clean.replace(':','')]:
        if alt in name_map:
            return name_map[alt]
    return None

def get_sci_zone_from_crossref(journal_name, session):
    """通过CrossRef API获取期刊Impact Factor，推断SCI分区"""
    try:
        url = 'https://api.crossref.org/journals'
        params = {'query': journal_name, 'rows': 3}
        resp = session.get(url, params=params, timeout=8)
        if resp.status_code == 200:
            items = resp.json().get('message', {}).get('items', [])
            for item in items:
                if item.get('title'):
                    return {
                        'journal': item['title'][0] if item.get('title') else journal_name,
                        'doi': item.get('DOI',''),
                        'if': item.get('metrics', {}).get(' cites-per-doc', 0),
                    }
    except Exception:
        pass
    return None

def parse_papers(text):
    """从文本中解析论文列表"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    papers = []
    current = None

    for line in lines:
        # 跳过明显非论文行
        if len(line) < 10:
            continue
        # 新论文开始（通常是年份开头或序号）
        if re.match(r'^\d+[\.、\)]', line) or re.match(r'^\[\d+\]', line):
            if current:
                papers.append(current)
            current = {'raw': line, 'parts': [line]}
        elif current:
            current['parts'].append(line)
            current['raw'] += ' | ' + line

    if current:
        papers.append(current)

    return papers

def extract_paper_info(paper):
    """从解析的论文块中提取期刊名、作者位置、年、卷、期、页、标题"""
    raw = paper.get('raw', '')
    full_text = ' '.join(paper.get('parts', []))

    # ---- 期刊名识别 ----
    journal_patterns = [
        r'《([^》]+)》',
        r'in\s+([A-Za-z\s&:\']+?)(?:\.|,|;|\d|\n|$)',
        r'\[([^\]]+)\]',
        r'发表[于在：:]+([^\s，。,；；]+)',
        r'收录[于在：:]+([^\s，。,；；]+)',
        r'期刊[：:]\s*([^，,。\n]+)',
    ]
    journal_name = ''
    for pat in journal_patterns:
        m = re.search(pat, full_text)
        if m:
            candidate = m.group(1).strip()
            if 3 < len(candidate) < 200 and not candidate.isdigit():
                journal_name = candidate
                break

    # ---- 年、卷、期、页码识别 ----
    year_match = re.search(r'20[12]\d[年]?|19[89]\d[年]?', full_text)
    year = year_match.group() if year_match else ''
    vol_match = re.search(r'卷\s*\.?\s*(\d+)', full_text)
    vol = vol_match.group(1) if vol_match else ''
    issue_match = re.search(r'期\s*\.?\s*(\d+)', full_text)
    issue = issue_match.group(1) if issue_match else ''
    pages_match = re.search(r'(\d+)[-–](\d+)', full_text)
    pages = f"{pages_match.group(1)}-{pages_match.group(2)}" if pages_match else ''

    # ---- 论文标题识别 ----
    title = ''
    title_patterns = [
        r'《([^》]{5,80})》',      # 中文标题在《》
        r'^([A-Z][A-Za-z\s:,\-]{10,150})$',  # 英文标题（单独一行）
        r'论文[：:]\s*(.{5,80})',
    ]
    for pat in title_patterns:
        m = re.search(pat, full_text, re.MULTILINE)
        if m:
            candidate = m.group(1).strip()
            if 5 < len(candidate) < 150:
                title = candidate
                break

    # ---- 作者位置识别 ----
    is_first = any(kw in full_text for kw in ['第一作者', '排名第一', '第一通讯', '共一', '共同第一'])
    is_corresponding = any(kw in full_text for kw in ['通讯作者', 'Corresponding', 'correspondence', '联系作者', '通信作者', 'Corresponding author'])
    is_valid = is_first or is_corresponding

    position = []
    if is_first: position.append('第一作者')
    if is_corresponding: position.append('通讯作者')

    return {
        'journal_name': journal_name,
        'title': title,
        'year': year,
        'volume': vol,
        'issue': issue,
        'pages': pages,
        'raw': raw[:200],
        'is_valid': is_valid,
        'position': '、'.join(position) if position else '其他',
        'parts': paper.get('parts', []),
    }

def main():
    st.title("📖 论文情况评价 & 高水平期刊匹配系统")
    st.caption("哈尔滨医科大学人事处 · 仅统计第一作者和通讯作者")

    name_map, issn_map = load_journals()

    with st.sidebar:
        st.header("📋 输入论文信息")
        input_mode = st.radio("输入方式", ["粘贴文本", "上传文件"], horizontal=True)

        text_input = ""
        if input_mode == "粘贴文本":
            text_input = st.text_area(
                "粘贴简历中的论文列表",
                height=400,
                placeholder="支持格式示例：\n1. 张三, 李四. 论文标题[J]. 期刊名, 2024, 35(2): 123-130.（第一作者）\n2. 王五, 等. 论文标题[J]. 期刊名, 2023.（通讯作者）\n3. [1] 赵六. 论文标题[J]. 期刊名, 2022.（第一作者）"
            )
        else:
            uploaded = st.file_uploader(
                "上传简历文件",
                type=['txt', 'docx', 'pdf'],
                help="支持 txt / docx / pdf（含扫描件）"
            )
            if uploaded:
                try:
                    file_bytes = uploaded.read()
                    if uploaded.name.endswith('.docx'):
                        from docx import Document
                        import tempfile, os
                        # docx 需要临时文件
                        tmp = tempfile.NamedTemporaryFile(suffix='.docx', delete=False)
                        tmp.write(file_bytes)
                        tmp.close()
                        doc = Document(tmp.name)
                        os.unlink(tmp.name)
                        text_input = '\n'.join([p.text for p in doc.paragraphs if p.text.strip()])
                    elif uploaded.name.endswith('.pdf'):
                        with st.spinner("🔍 正在识别 PDF，请稍候（扫描件约需 10-30 秒）..."):
                            text_input, is_scanned = extract_text_from_pdf(file_bytes, uploaded.name)
                        if is_scanned:
                            st.success("✅ PDF 已作为扫描件完成 OCR 文字识别")
                        else:
                            st.success("✅ PDF 文字提取完成")
                        if not text_input.strip():
                            st.error("❌ 无法从 PDF 中提取文字，请确认文件内容")
                    else:
                        text_input = file_bytes.decode('utf-8', errors='ignore')
                except Exception as e:
                    st.error(f"读取失败: {e}")

        analyze_btn = st.button("🔍 开始分析", type="primary", use_container_width=True)

        st.divider()
        st.markdown("**高水平期刊目录说明**")
        st.markdown("""
        - 🔴 **高水平1类**：国际顶级科技期刊（CNS级别）
        - 🟠 **高水平2类**：国际一流科技期刊
        - 🟡 **高水平3类**：国际高水平科学期刊
        - 🟢 **高水平4类**：国际重要科技期刊
        - 🔵 **高水平5类**：具有国际影响力国内期刊
        """)

    if not analyze_btn and not text_input:
        st.info("👈 请在左侧输入论文信息后点击「开始分析」")
        return
    if not text_input.strip():
        st.warning("请输入论文内容")
        return

    with st.spinner("正在解析与匹配期刊..."):
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; PaperEvalBot/1.0)'})

        papers_raw = parse_papers(text_input)
        parsed = [extract_paper_info(p) for p in papers_raw]

        results = []
        session_count = 0
        for p in parsed:
            if not p['is_valid']:
                continue
            jname = p['journal_name']
            matched = match_journal(jname, name_map, issn_map, session) if jname else None
            sci_info = None
            if not matched and jname and session_count < 20:
                session_count += 1
                time.sleep(0.3)
                sci_info = get_sci_zone_from_crossref(jname, session)
            tier = matched['tier'] if matched else None
            tier_level = matched['tier_level'] if matched else 99
            # 组合论文出处信息
            pub_info = ''
            if p.get('year') or p.get('volume') or p.get('issue') or p.get('pages'):
                parts = [p.get('year',''), p.get('volume',''), p.get('issue',''), p.get('pages','')]
                pub_info = ', '.join(x for x in parts if x)

            results.append({
                '论文标题': p.get('title','') or '（未识别标题）',
                '期刊名称': jname if jname else '（未识别）',
                '作者位置': p['position'],
                '目录认定': tier if tier else ('SCI期刊（待核实）' if sci_info else '非目标期刊'),
                '目录级别': tier_level,
                'ISSN': matched.get('issn','') if matched else '',
                'eISSN': matched.get('eissn','') if matched else '',
                '中科院分区': (matched.get('zone','')+'区') if matched and matched.get('zone') else '',
                '学科': matched.get('subject','') if matched else '',
                '发表信息': pub_info,
                '原文': p['raw'][:200],
            })

    if not results:
        st.warning("未识别到有效的第一作者/通讯作者论文，请检查输入格式")
        return

    results.sort(key=lambda x: (x['目录级别'], -len(x['期刊名称'])))
    df = pd.DataFrame(results)

    # ===== 统计概览 =====
    st.divider()
    col1, col2, col3, col4 = st.columns(4)

    total = len(df)
    in_list = len(df[df['目录级别'] < 99])
    first_author = len(df[df['作者位置'].str.contains('第一作者')])
    corresponding = len(df[df['作者位置'].str.contains('通讯作者')])

    col1.metric("有效论文（第一/通讯）", f"{total} 篇")
    col2.metric("高水平目录匹配", f"{in_list} 篇")
    col3.metric("第一作者", f"{first_author} 篇")
    col4.metric("通讯作者", f"{corresponding} 篇")

    # ===== 高水平目录匹配明细（逐条详细列出） =====
    matched_rows = df[df['目录级别'] < 99].to_dict('records')
    if matched_rows:
        st.subheader(f"✅ 高水平期刊目录匹配结果（共 {len(matched_rows)} 篇）")

        tier_order = ['高水平1类','高水平2类','高水平3类','高水平4类',
                      '领军期刊（高水平4类）','高水平5A类','高水平5类',
                      '重点期刊（高水平5类）','高水平5B类']
        tier_colors = {
            '高水平1类':       ('🔴','#ffe5e5'),
            '高水平2类':       ('🟠','#fff3e0'),
            '高水平3类':       ('🟡','#fffde7'),
            '高水平4类':       ('🟢','#e8f5e9'),
            '领军期刊（高水平4类）': ('🟢','#e8f5e9'),
            '高水平5A类':      ('🔵','#e3f2fd'),
            '高水平5类':        ('🔵','#e3f2fd'),
            '重点期刊（高水平5类）': ('🔵','#cce5ff'),
            '高水平5B类':       ('🔵','#f0f8ff'),
        }

        # 按层级分组展示
        for tier in tier_order:
            tier_rows = [r for r in matched_rows if r['目录认定'] == tier]
            if not tier_rows:
                continue
            emoji, bg_color = tier_colors.get(tier, ('⚪','#ffffff'))

            with st.container():
                st.markdown(f"#### {emoji} {tier}（{len(tier_rows)} 篇）")
                for i, row in enumerate(tier_rows, 1):
                    # 提取期刊信息
                    journal_info = row.get('期刊名称', '')
                    pos = row.get('作者位置', '')
                    original_text = row.get('原文', row.get('期刊名称',''))
                    issn_val = row.get('ISSN', '')
                    zone_val = row.get('中科院分区', '')

                    title_val = row.get('论文标题', '')
                    issn_val = row.get('ISSN', '')
                    eissn_val = row.get('eISSN', '')
                    zone_val = row.get('中科院分区', '')
                    subject_val = row.get('学科', '')
                    pub_info = row.get('发表信息', '')
                    original_text = row.get('原文', journal_info)

                    issn_display = issn_val
                    if eissn_val and eissn_val != issn_val:
                        issn_display = f"{issn_val} / {eissn_val}"
                    elif eissn_val:
                        issn_display = eissn_val

                    card_md = f"""
                    <div style="background:{bg_color}; border-radius:8px; padding:14px 18px; 
                                 margin:8px 0; border-left:4px solid #1e3a5f;">
                    <table style="width:100%; border-collapse:collapse; font-size:14px;">
                    <tr>
                        <td style="width:95px; color:#555; font-weight:bold; vertical-align:top;">篇序</td>
                        <td style="font-weight:bold; font-size:15px; color:#1e3a5f;">第{i}篇</td>
                    </tr>
                    <tr>
                        <td style="color:#555; font-weight:bold; vertical-align:top;">论文标题</td>
                        <td><i>{title_val}</i></td>
                    </tr>
                    <tr>
                        <td style="color:#555; font-weight:bold; vertical-align:top;">期刊名称</td>
                        <td><b style="font-size:15px;">{journal_info}</b></td>
                    </tr>
                    <tr>
                        <td style="color:#555; font-weight:bold; vertical-align:top;">目录级别</td>
                        <td><b>{tier}</b></td>
                    </tr>
                    <tr>
                        <td style="color:#555; font-weight:bold; vertical-align:top;">作者位置</td>
                        <td><b>{pos}</b></td>
                    </tr>
                    <tr>
                        <td style="color:#555; font-weight:bold; vertical-align:top;">ISSN</td>
                        <td>{issn_display if issn_display else '—'}</td>
                    </tr>
                    <tr>
                        <td style="color:#555; font-weight:bold; vertical-align:top;">中科院分区</td>
                        <td>{zone_val if zone_val else '—'}{' | ' + subject_val if subject_val else ''}</td>
                    </tr>
                    <tr>
                        <td style="color:#555; font-weight:bold; vertical-align:top;">发表信息</td>
                        <td>{pub_info if pub_info else '—'}</td>
                    </tr>
                    <tr>
                        <td style="color:#555; font-weight:bold; vertical-align:top;">原始条目</td>
                        <td style="color:#555; font-size:12px;">{original_text}</td>
                    </tr>
                    </table>
                    </div>
                    """
                    st.markdown(card_md, unsafe_allow_html=True)
    else:
        st.info("无匹配到高水平目录的论文")

    # ===== 全部论文明细（不含高水平目录的） =====
    other_rows = df[df['目录级别'] >= 99].to_dict('records')
    if other_rows:
        st.subheader(f"⚠️ 非目标期刊 / 待核实（共 {len(other_rows)} 篇）")
        for i, row in enumerate(other_rows, 1):
            journal_info = row.get('期刊名称', '')
            pos = row.get('作者位置', '')
            tier_label = row.get('目录认定', '')
            original_text = row.get('原文', '')
            with st.container():
                title_val = row.get('论文标题', '')
                pub_info = row.get('发表信息', '')
                card_md = f"""
                <div style="background:#f8f8f8; border-radius:8px; padding:12px 18px;
                             margin:6px 0; border-left:3px solid #ccc;">
                <b style="font-size:14px;">❓ 第{i}篇</b> &nbsp;
                <b>{journal_info}</b> &nbsp;|&nbsp; <b>{pos}</b>
                {f'<br><i style="color:#444;font-size:13px;">{title_val}</i>' if title_val else ''}
                {f'<br><span style="color:#555;font-size:12px;">{pub_info}</span>' if pub_info else ''}
                <br><span style="color:#888; font-size:12px;">原始：{original_text[:100]}</span>
                <br><span style="color:#888; font-size:12px;">认定：{tier_label}</span>
                </div>
                """
                st.markdown(card_md, unsafe_allow_html=True)

    # ===== 全部论文明细表 =====
    with st.expander("📋 全部论文明细（表格视图）"):
        disp_df = df[['期刊名称','作者位置','目录认定','目录级别']].copy()
        disp_df.columns = ['期刊名称','作者位置','目录级别','级别值']
        disp_df = disp_df.sort_values('级别值')
        st.dataframe(
            disp_df.style.apply(
                lambda row: [f'background-color: {TIER_BG.get(row["目录级别"], "#fff")}'] * len(row),
                axis=1
            ),
            use_container_width=True,
            hide_index=True
        )

    st.divider()
    st.caption("⚠️ 本系统结果仅供参考，最终认定以学校学术委员会评审为准")

if __name__ == "__main__":
    main()
