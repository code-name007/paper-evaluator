#!/usr/bin/env python3
"""
论文情况评价 & 高水平期刊匹配系统 v2.0
哈尔滨医科大学人事处专用
新版思路：网站只做媒介，核心解析全部交给 M2.7 视觉模型处理
"""
import streamlit as st
import tempfile
import os
import base64
import requests
import json
import re
import fitz
import pandas as pd

# ==================== MiniMax API 调用 ====================

# ==================== MiniMax API 配置 ====================
# 优先级：st.secrets（Streamlit Cloud）> 环境变量 > 本地配置文件

def get_minimax_config():
    """获取 MiniMax API 配置，兼容本地和 Streamlit Cloud"""
    api_key = ""
    api_base = "https://api.minimax.chat/v1"

    # 1. 尝试 st.secrets（Streamlit Cloud 部署时使用）
    try:
        api_key = st.secrets["MINIMAX_API_KEY"]
        api_base = st.secrets.get("MINIMAX_API_BASE", api_base)
    except Exception:
        pass

    # 2. 尝试环境变量
    if not api_key:
        api_key = os.environ.get("MINIMAX_API_KEY", "")

    # 3. 尝试本地配置文件
    if not api_key:
        config_path = os.path.expanduser("~/.openclaw/config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    config = json.load(f)
                    api_key = config.get("providers", {}).get("minimax", {}).get("apiKey", "")
            except Exception:
                pass

    return api_key, api_base


MINIMAX_API_KEY, MINIMAX_API_BASE = get_minimax_config()


def pdf_to_images(pdf_bytes, dpi=150):
    """将 PDF 转换为图片列表"""
    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(dpi/72, dpi/72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        images.append(img_bytes)
    doc.close()
    return images


def call_minimax_vision(image_base64_list, prompt, model="MiniMax-VL-01"):
    """调用 MiniMax 视觉模型"""
    if not MINIMAX_API_KEY:
        return None, "API Key 未配置"
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }
    content = []
    for b64 in image_base64_list:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    content.append({"type": "text", "text": prompt})
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 8000,
        "temperature": 0.3
    }
    try:
        response = requests.post(
            f"{MINIMAX_API_BASE}/text/chatcompletion_v2",
            headers=headers, json=payload, timeout=300
        )
        if response.status_code == 200:
            try:
                result = response.json()
                if result is None:
                    return None, f"API 返回空JSON: {response.text[:200]}"
                choices = result.get("choices")
                if not choices:
                    return None, f"API 无 choices 返回: {str(result)[:200]}"
                msg = choices[0].get("message", {})
                return msg.get("content", ""), None
            except Exception as e2:
                return None, f"解析响应失败: {str(e2)}, 响应内容: {response.text[:200]}"
        return None, f"API 失败 ({response.status_code}): {response.text[:200]}"
    except Exception as e:
        return None, f"请求异常: {str(e)}"


PAPER_EXTRACT_PROMPT = """你是一位专业的学术论文评审专家。请仔细阅读这份简历中的论文列表，并提取每篇论文的详细信息。

请严格按以下JSON数组格式输出，JSON之外不要写任何文字：

```json
[
  {
    "序号": 1,
    "论文标题": "论文完整标题",
    "期刊名称": "期刊全称",
    "发表年份": 2024,
    "影响因子": 5.0,
    "作者位置": "第一作者/通讯作者/共同作者/其他",
    "期刊级别": "SCI/SSCI/核心/中文核心/普刊",
    "备注": "如为综述请说明"
  }
]
```

评分参考：- 第一作者或通讯作者权重最高- 高影响因子期刊（如Nature、Science、Cell及其子刊）显著加分
- SCI期刊优先于中文核心期刊
- 综述论文请在备注标注"综述"
"""


EVALUATION_PROMPT = """请作为医学人才引进评估专家，对以下候选人的论文发表情况进行综合评价。

请严格按以下JSON格式输出，JSON之外不要写任何文字：

```json
{
  "总体评价": "一段话综合评价",
  "学术水平": "优秀/良好/一般/不足",
  "核心优势": ["优势1", "优势2", "优势3"],
  "存在问题": ["问题1（如有）"],
  "引进建议": "一段话建议",
  "详细分析": "详细分析，至少200字"
}
```
"""


# ==================== Streamlit 界面 ====================

st.set_page_config(page_title="论文评价系统 v2.0", page_icon="📖", layout="wide")
st.title("📖 论文情况评价 & 高水平期刊匹配系统")
st.caption("哈尔滨医科大学人事处 · AI 智能解析 · 仅统计第一作者和通讯作者")

# API Key 检查
if not MINIMAX_API_KEY:
    st.warning("⚠️ MiniMax API Key 未配置，请联系管理员配置。")

# 上传文件
uploaded_file = st.file_uploader(
    "📄 上传简历文件（PDF）",
    type=["pdf"],
    help="支持 PDF 文件（扫描件或电子版均可）"
)

if not uploaded_file:
    st.info("👆 请上传一份 PDF 格式的简历，系统将自动解析论文列表并生成评价报告。")
    st.stop()

# ===== PDF 处理 =====
with st.spinner("📄 PDF 转换中..."):
    pdf_bytes = uploaded_file.read()
    images = pdf_to_images(pdf_bytes, dpi=150)
    b64_images = [base64.b64encode(img).decode("utf-8") for img in images]

st.success(f"✅ 已提取 {len(images)} 页，正在发送给 AI 分析...")

# ===== 第1步：解析论文列表 =====
progress_bar = st.progress(0)

if True:
    progress_bar.progress(20)
    with st.spinner("🔍 正在解析论文列表..."):
        paper_result, paper_error = call_minimax_vision(b64_images, PAPER_EXTRACT_PROMPT)

    if paper_error:
        st.error(f"❌ 论文解析失败：{paper_error}")
        st.stop()

    progress_bar.progress(50)

    # 尝试解析 JSON
    json_match = re.search(r'\[.*\]', paper_result, re.DOTALL)
    if not json_match:
        st.error("❌ AI 返回格式异常，无法解析论文列表")
        with st.expander("🔧 原始输出（调试用）"):
            st.text(paper_result[:2000] if paper_result else "无")
        st.stop()

    try:
        papers = json.loads(json_match.group())
    except json.JSONDecodeError:
        st.error("❌ JSON 解析失败，请重试")
        with st.expander("🔧 原始输出（调试用）"):
            st.text(paper_result[:2000] if paper_result else "无")
        st.stop()

    st.session_state["papers"] = papers

    # ===== 显示论文列表 =====
    st.subheader(f"📋 论文列表（共 {len(papers)} 篇）")

    df_data = []
    for p in papers:
        title = p.get("论文标题", "")
        df_data.append({
            "序号": p.get("序号", ""),
            "论文标题": title[:60] + ("..." if len(title) > 60 else ""),
            "期刊": p.get("期刊名称", ""),
            "年份": p.get("发表年份", ""),
            "IF": p.get("影响因子", ""),
            "作者位置": p.get("作者位置", ""),
            "级别": p.get("期刊级别", ""),
        })

    st.dataframe(pd.DataFrame(df_data), use_container_width=True, hide_index=True)

    # ===== 高水平期刊论文 =====
    st.subheader("🌟 高水平期刊论文（影响因子 ≥ 5）")
    high_impact = [
        p for p in papers
        if isinstance(p.get("影响因子"), (int, float)) and p.get("影响因子", 0) >= 5
    ]

    if high_impact:
        for p_item in high_impact:
            t = p_item.get("论文标题", "")
            j = p_item.get("期刊名称", "")
            y = p_item.get("发表年份", "")
            if_val = p_item.get("影响因子", 0)
            col1, col2, col3 = st.columns([3, 2, 1])
            with col1:
                st.markdown(f"**{t}**")
            with col2:
                st.caption(f"{j} · {y}")
            with col3:
                st.metric("IF", f"{if_val:.1f}" if isinstance(if_val, (int, float)) else "—")
    else:
        st.info("暂无影响因子≥5的论文记录")

    # ===== 统计摘要 =====
    first_author = [p for p in papers if "第一作者" in p.get("作者位置", "")]
    corr_author = [p for p in papers if "通讯作者" in p.get("作者位置", "")]
    co_author = [p for p in papers
                 if "共同作者" in p.get("作者位置", "")
                 and "第一作者" not in p.get("作者位置", "")]

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("第一作者", len(first_author))
    with col_b:
        st.metric("通讯作者", len(corr_author))
    with col_c:
        st.metric("共同作者", len(co_author))

    st.divider()

    # ===== 第2步：综合评价 =====
    progress_bar.progress(60)
    with st.spinner("📊 正在生成综合评价..."):
        eval_result, eval_error = call_minimax_vision(b64_images, EVALUATION_PROMPT)

    if eval_error:
        st.warning(f"⚠️ 综合评价生成失败：{eval_error}")
        progress_bar.progress(100)
    else:
        progress_bar.progress(100)
        st.success("🎉 分析完成！")

        st.subheader("📊 综合评价")

        eval_json_match = re.search(r'\{.*\}', eval_result, re.DOTALL)
        if eval_json_match:
            try:
                eval_data = json.loads(eval_json_match.group())
                level = eval_data.get("学术水平", "未知")
                if level == "优秀":
                    st.success(f"学术水平：**{level}**")
                elif level == "良好":
                    st.info(f"学术水平：**{level}**")
                else:
                    st.warning(f"学术水平：**{level}**")

                st.markdown(f"**总体评价：** {eval_data.get('总体评价', '')}")

                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("✅ **核心优势**")
                    for adv in eval_data.get("核心优势", []):
                        st.write(f"- {adv}")
                with col2:
                    issues = eval_data.get("存在问题", [])
                    if issues:
                        st.markdown("⚠️ **存在问题**")
                        for issue in issues:
                            st.write(f"- {issue}")
                    else:
                        st.markdown("✅ **无明显问题**")

                st.divider()
                st.markdown(f"**📌 引进建议：**\n\n{eval_data.get('引进建议', '')}")
                st.divider()
                st.markdown(f"**📝 详细分析：**\n\n{eval_data.get('详细分析', eval_result)}")
            except json.JSONDecodeError:
                st.markdown(eval_result)
        else:
            st.markdown(eval_result)

    # ===== 下载结果 =====
    st.divider()
    full_result = {
        "论文列表": papers,
        "综合评价": eval_result if not eval_error else "评价生成失败",
        "高水平论文": high_impact,
        "统计": {
            "第一作者": len(first_author),
            "通讯作者": len(corr_author),
            "共同作者": len(co_author)
        }
    }

    st.download_button(
        "📥 下载完整分析结果（JSON）",
        data=json.dumps(full_result, ensure_ascii=False, indent=2),
        file_name="论文评价结果.json",
        mime="application/json"
    )

    # 原始输出（调试用）
    with st.expander("🔧 原始 AI 输出（调试用）"):
        st.text(paper_result[:3000] if paper_result else "无")
