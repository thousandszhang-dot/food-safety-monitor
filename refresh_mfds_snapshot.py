#!/usr/bin/env python3
"""Embed a freshly scraped MFDS public-list snapshot without company fields."""
import gzip
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "index.html"
DATA = ROOT / "data" / "mfds_import_noncompliant.json"

page = PAGE.read_text(encoding="utf-8")
for old, new in {
    "搜索公司、品名或不合格项目": "搜索品名或不合格项目",
    "<th>公司名</th>": "",
    "company:r.company||'未公布',": "",
    "company:'未公布（RASFF 未提供）',": "",
    "n.company,n.product": "n.product",
    "<td>${escapeHtml(n.company||'未公布')}</td>": "",
    'colspan="11"': 'colspan="10"',
}.items():
    page = page.replace(old, new)
page = re.sub(r",company:'[^']*'", "", page)
pattern = r'(<script id="mfds-snapshot" type="application/json">)(.*?)(</script>)'
match = re.search(pattern, page, re.DOTALL)
if not match:
    raise SystemExit("未找到 MFDS 快照插入位置")

# A previous collector revision accidentally appended the old payload. Recover
# translations from any valid JSON payloads in the script block before replacing it.
old_payload = match.group(2)
translations = {}
decoder = json.JSONDecoder()
offset = 0
while offset < len(old_payload):
    while offset < len(old_payload) and old_payload[offset].isspace():
        offset += 1
    if offset >= len(old_payload):
        break
    try:
        old, end = decoder.raw_decode(old_payload, offset)
    except json.JSONDecodeError:
        break
    for row in old.get("records", []):
        if row.get("id") and row.get("productNameZh"):
            translations[row["id"]] = row["productNameZh"]
    offset = end

snapshot = json.loads(DATA.read_text(encoding="utf-8"))
for row in snapshot.get("records", []):
    row.pop("company", None)
    if row.get("id") in translations:
        row["productNameZh"] = translations[row["id"]]
dates = [row["noticeDate"] for row in snapshot.get("records", []) if row.get("noticeDate")]
snapshot["dateCoverage"] = {"from": min(dates) if dates else None, "to": max(dates) if dates else None}
payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
page = page[:match.start()] + match.group(1) + payload + match.group(3) + page[match.end():]

# Show source-specific collection dates so a fresh MFDS snapshot cannot make an
# older RASFF snapshot appear current as well.
old_status = "const mfdsAt=snapshot.collectedAt?new Date(snapshot.collectedAt):null,rasffAt=rasffSnapshot.collectedAt?new Date(rasffSnapshot.collectedAt):null,collected=[mfdsAt,rasffAt].filter(d=>d&&!isNaN(d)).sort((a,b)=>b-a)[0];document.querySelector('#collectedMetric').textContent=collected?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(collected):'未提供';document.querySelector('#collectedNote').textContent='MFDS 与 RASFF 快照中较新的采集时间'"
new_status = "const mfdsAt=snapshot.collectedAt?new Date(snapshot.collectedAt):null,rasffAt=rasffSnapshot.collectedAt?new Date(rasffSnapshot.collectedAt):null,fmtSourceTime=d=>d&&!isNaN(d)?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d):'未取得';document.querySelector('#collectedMetric').innerHTML=`韩国 ${fmtSourceTime(mfdsAt)}<br>欧盟 ${fmtSourceTime(rasffAt)}`;document.querySelector('#collectedMetric').style.fontSize='15px';document.querySelector('#collectedNote').textContent='分别显示韩国 MFDS 与欧盟 RASFF 的采集时间'"
if old_status in page:
    page = page.replace(old_status, new_status, 1)
elif new_status not in page:
    raise SystemExit("未找到来源更新时间显示代码，未写入网页")

# Keep the existing RASFF data source embedded as a separate snapshot block.
rasff_pattern = r'<script id="rasff-snapshot" type="application/json">.*?</script>'
if not re.search(rasff_pattern, page, re.DOTALL):
    marker = "</script><script>\nconst notices=["
    if marker not in page:
        raise SystemExit("未找到主程序脚本位置，不能恢复 RASFF 快照占位符")
    placeholder = "</script><script id=\"rasff-snapshot\" type=\"application/json\">{\"records\":[]}</script><script>\nconst notices=["
    page = page.replace(marker, placeholder, 1)
PAGE.write_text(page, encoding="utf-8")
(ROOT / "public" / "index.html").write_text(page, encoding="utf-8")
with (ROOT / "index.html.gz").open("wb") as out:
    out.write(gzip.compress(page.encode("utf-8"), mtime=0))

print(f"MFDS 快照：{len(snapshot['records'])} 条，日期 {snapshot['dateCoverage']['from']} 至 {snapshot['dateCoverage']['to']}，采集于 {snapshot['collectedAt']}")
