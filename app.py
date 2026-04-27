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
    """从解析的论文块中提取期刊名、作者位置"""
    raw = paper.get('raw', '')
    full_text = ' '.join(paper.get('parts', []))

    # 期刊名识别：常见模式
    journal_patterns = [
        r'《([^》]+)》',
        r'\[([^\]]+)\]',
        r'in\s+([A-Za-z\s&:]+?)(?:\.|,|\d|\n|$)',
        r'发表[于在]+([^\s，。,]+)',
        r'收录[于在]+([^\s，。,]+)',
    ]
    journal_name = ''
    for pat in journal_patterns:
        m = re.search(pat, full_text)
        if m:
            candidate = m.group(1).strip()
            if 3 < len(candidate) < 200:
                journal_name = candidate
                break

    # 作者位置识别
    is_first = any(kw in full_text for kw in ['第一作者', '排名第一', '第一通讯', '共一'])
    is_corresponding = any(kw in full_text for kw in ['通讯作者', 'Corresponding', 'correspondence', '联系作者', '通信作者'])
    is_valid = is_first or is_corresponding

    position = []
    if is_first: position.append('第一作者')
    if is_corresponding: position.append('通讯作者')

    return {
        'journal_name': journal_name,
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
            uploaded = st.file_uploader("上传简历文件", type=['txt', 'docx', 'pdf'])
            if uploaded:
                try:
                    if uploaded.name.endswith('.docx'):
                        from docx import Document
                        doc = Document(uploaded)
                        text_input = '\n'.join([p.text for p in doc.paragraphs if p.text.strip()])
                    else:
                        text_input = uploaded.read().decode('utf-8', errors='ignore')
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
            results.append({
                '期刊名称': jname if jname else '（未识别）',
                '作者位置': p['position'],
                '目录认定': tier if tier else ('SCI期刊（待核实）' if sci_info else '非目标期刊'),
                '目录级别': tier_level,
                'Impact Factor': sci_info['if'] if sci_info else None,
                '原文': p['raw'][:150],
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

    # ===== 分级统计 =====
    st.subheader("📊 期刊级别分布")
    tier_counts = df[df['目录级别'] < 99]['目录认定'].value_counts()
    if len(tier_counts):
        cols = st.columns(len(tier_counts))
        for ci, (tier, cnt) in enumerate(tier_counts.items()):
            with cols[ci]:
                st.metric(tier, f"{cnt} 篇")
    else:
        st.info("无匹配到高水平目录的论文")

    # ===== 高水平目录匹配明细 =====
    if in_list > 0:
        st.subheader("✅ 高水平期刊目录匹配结果")
        matched_df = df[df['目录级别'] < 99][['期刊名称', '作者位置', '目录认定', 'Impact Factor']].copy()
        st.dataframe(
            matched_df.style.apply(lambda _: [f'background-color: {TIER_BG.get(row["目录认定"], "#fff")}'], axis=1),
            use_container_width=True,
            hide_index=True
        )

    # ===== 全部论文明细 =====
    st.subheader("📋 全部论文明细（仅第一作者/通讯作者）")
    disp_cols = ['期刊名称', '作者位置', '目录认定']
    st.dataframe(
        df[disp_cols].style.apply(lambda _: [f'background-color: {TIER_BG.get(row["目录认定"], "#fff")}'], axis=1),
        use_container_width=True,
        hide_index=True
    )

    # ===== 未匹配期刊（可手动核实） =====
    unmatched = df[df['目录级别'] >= 99]
    if len(unmatched):
        with st.expander(f"⚠️ 待核实期刊（共{len(unmatched)}篇，可手动确认级别）"):
            for _, row in unmatched.iterrows():
                st.markdown(f"- **{row['期刊名称']}** ｜ {row['作者位置']} ｜ {row['目录认定']}")

    st.divider()
    st.caption("⚠️ 本系统结果仅供参考，最终认定以学校学术委员会评审为准")

if __name__ == "__main__":
    main()
