#!/usr/bin/env python3
"""解析高水平期刊目录，生成结构化JSON供网站使用"""
from docx import Document
import json

doc = Document('/Users/code-007/Downloads/高水平五类期刊目录/高水平期刊目录-正式发文-20230919.docx')

Tiers = {
    0: '高水平1类',   # 国际顶级科技期刊
    1: '高水平2类',   # 国际一流科技期刊
    2: '高水平3类',   # 国际高水平科学期刊
    3: '高水平4类',   # 国际重要科技期刊
    4: '高水平5A类',  # 具有国际影响力国内期刊（英文）
    5: '高水平5B类',  # 具有国际影响力国内期刊
}

journals = {}  # name_lower -> {name, issn, eissn, tier, category}

for ti, tbl in enumerate(doc.tables):
    tier_name = Tiers[ti]
    for ri, row in enumerate(tbl.rows):
        if ri == 0:  # header
            headers = [c.text.strip().lower() for c in row.cells]
            continue
        cells = [c.text.strip() for c in row.cells]
        if len(cells) >= 2 and cells[0]:
            name = cells[0]
            issn = cells[1] if len(cells) > 1 else ''
            eissn = cells[2] if len(cells) > 2 else ''
            # store by name and issn
            key = name.lower().replace(' ', '')
            journals[key] = {
                'name': name,
                'issn': issn,
                'eissn': eissn,
                'tier': tier_name,
                'tier_level': ti,
            }
            if issn:
                journals[issn] = journals[key]
            if eissn:
                journals[eissn] = journals[key]

print(f"共解析期刊数量: {len(set(k for k in journals if len(k) > 5))}")

# 额外：读取xlsx文件补充
import subprocess
try:
    import openpyxl
    xlsx_path = '/Users/code-007/Downloads/高水平五类期刊目录/国际高质量期刊五类目录汇总表-终稿-20231008.xlsx'
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    added = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and row[0]:
                name = str(row[0]).strip()
                issn = str(row[1]).strip() if len(row) > 1 and row[1] else ''
                eissn = str(row[2]).strip() if len(row) > 2 and row[2] else ''
                tier_str = str(ws.title).strip()
                tier_map = {'1类':0,'2类':1,'3类':2,'4类':3,'5A类':4,'5B类':5}
                tier_level = tier_map.get(tier_str, -1)
                if tier_level >= 0 and name:
                    key = name.lower().replace(' ', '')
                    journals[key] = {
                        'name': name,
                        'issn': issn,
                        'eissn': eissn,
                        'tier': Tiers[tier_level],
                        'tier_level': tier_level,
                    }
                    if issn and issn != 'None':
                        journals[issn] = journals[key]
                    if eissn and eissn != 'None':
                        journals[eissn] = journals[key]
                    added += 1
    print(f"从xlsx补充期刊: {added}")
except Exception as e:
    print(f"xlsx读取失败: {e}")

# 保存
out_path = '/Users/code-007/Downloads/啵啵龙/文稿/论文评价网站/journals_data.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(journals, f, ensure_ascii=False, indent=2)
print(f"已保存: {out_path}")
print(f"总计期刊条目: {len(journals)}")
