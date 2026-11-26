# 论文情况评价

这是一个 Streamlit 应用。上传 PDF 简历后，应用通过 MiniMax 视觉模型提取论文列表、生成综合评价，并提供 JSON 下载。启动应用无需先生成或更新期刊目录。上传后的论文分析需要网络和可用的 MiniMax API 配置。

## 安装与启动

在仓库根目录执行：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

最后一条命令启动 Streamlit。浏览器中打开终端显示的地址。应用直接用 Streamlit 显示界面、PyMuPDF 渲染 PDF、Requests 请求 API、pandas 整理论文表格。

### 配置 MiniMax API

`app.py` 按以下顺序读取密钥，前一项未提供可用值时再尝试下一项：

1. Streamlit secrets 的 `MINIMAX_API_KEY`。
2. 运行 Streamlit 的进程环境变量 `MINIMAX_API_KEY`。
3. 用户目录下已有 OpenClaw 本地配置的 `providers.minimax.apiKey`。

API 基址默认是 `https://api.minimax.chat/v1`。使用 Streamlit secrets 时可同时设置 `MINIMAX_API_BASE`。环境变量和 OpenClaw 回退分支只读取密钥。请把真实密钥留在私密配置中。

## 上传后的处理流程

1. 上传 PDF 后，运行 Streamlit 的主机用 PyMuPDF 将所有页面按 150 DPI 渲染成 PNG，并编码为 Base64。
2. 应用向配置的 API 基址下的 `/text/chatcompletion_v2` 发送页面图像及论文提取提示词。代码调用的默认模型为 `MiniMax-VL-01`。应用解析返回的论文列表并显示论文信息及作者位置统计。
3. 论文提取和解析成功后，应用向同一端点再次发送页面图像和综合评价提示词。分析结束后可下载 `论文评价结果.json`，其中记录论文列表、筛选结果、作者统计和综合评价结果或失败状态。

每次分析最多发出两次模型请求。两阶段如有执行，均会向所配置的 API 服务发送同一批完整页面图像及各自的提示词。未配置密钥时不发送模型请求。首轮请求或论文列表解析失败时，流程会停止。代码未对同一上传文件缓存模型调用，Streamlit 重跑且上传仍保留时，可能再次发出请求。请使用获准提交的材料。界面的「高水平期刊论文」按模型返回的数值型影响因子 ≥ 5 筛选，字符串形式的影响因子不会进入该筛选。当前 `app.py` 不读取 `journals_data.json`，期刊级别和影响因子应按业务要求另行核对。

## 离线维护期刊目录

在核对的提交 `a4482c66fabec1e5353ae715011c198affacdac5` 中，仓库包含 `journals_data.json`。两份离线脚本均指向 `高水平期刊目录-正式发文-20230919.docx` 和 `国际高质量期刊五类目录汇总表-终稿-20231008.xlsx`。`20230919` 和 `20231008` 是输入文件名中的日期标记。脚本会将本次读到的记录写为 JSON。如果两个脚本指向同一输出位置，后运行且执行到写出步骤的脚本会覆盖前一次输出。

| 脚本 | 输入与合并方式 | 输出 |
| --- | --- | --- |
| `1_parse_journals.py` | 先按 DOCX 表格顺序读取 1、2、3、4、5A、5B 类，再遍历 XLSX 中按类别命名的工作表补充。使用小写并去除空格的刊名及 ISSN、eISSN 建索引，遇到相同键时后写入的值可以覆盖原值。XLSX 读取失败时会提示，并继续尝试写出已有数据。 | `journals_data.json`，包含期刊名、ISSN、eISSN、类别和类别层级。 |
| `2_merge_journals.py` | 以 DOCX 中的 1–4 类和 5A、5B 类表格为基础，读取 XLSX「总表」中的 A 档、B 档记录。用已有 ISSN、eISSN 和小写刊名键检查重复，已有记录保留，补充识别为新增的 XLSX 记录。 | `journals_data.json`，包含类别、来源及代码读取到的学科或分区等字段。 |

运行前，准备对应的原始 DOCX 和 XLSX，在选定脚本中把硬编码的输入路径和 JSON 输出路径改成自己的文件位置。核对的提交根目录未列出上述原始 DOCX 和 XLSX。两个脚本均用到 `python-docx`。若运行第一个脚本的 XLSX 补充部分，或运行第二个脚本，请另装仓库依赖表中未列出的 `openpyxl`。

```sh
python -m pip install openpyxl
# 根据需要选择一条
python 1_parse_journals.py
python 2_merge_journals.py
```

脚本运行到写出步骤才会重建目录。所核对的代码和提交清单未说明仓库内现有 JSON 是由哪份脚本、哪一版输入生成的，也未提供生成日期或最近数据更新时间。当前代码中没有发现应用启动或分析时自动更新目录的机制。
