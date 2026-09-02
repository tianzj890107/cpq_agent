# -*- coding: utf-8 -*-
"""DA 本体的 Markdown 载体 —— 生成、解析、与 xlsx 对账。

事实源仍然是《亿纬锂能DA梳理.xlsx》，但**运行时读的是它生成出来的 .md**：

  · 报价助手的模型会自己去看这份本体（业务对象 → 逻辑实体 → 属性）。让它读 xlsx
    意味着每次都要把一个二进制表格塞进上下文，读得慢、读不全，还常常把合并单元格
    串行；换成 Markdown 之后是纯文本、有表头、按实体分节，模型一眼就能定位。
  · 服务端解析也不再需要 openpyxl —— 少一个运行时依赖，容器里装不上也不影响启动。

生成：  python cpq_ontology.py            （从 xlsx 重新生成 .md）
对账：  python cpq_ontology.py --check    （md 与 xlsx 不一致就非零退出）

格式刻意简单到"能用 grep 读"：一级标题分三个助手，`### 表名` 起一个逻辑实体，
下面两行写业务对象与逻辑实体中文名，再跟一张属性表。换行与竖线在单元格里转义，
解析时还原 —— 贸易术语那种带多行说明的备注就是靠这个存下来的。
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MD_PATH = SCRIPT_DIR / "亿纬锂能DA梳理.md"

# agent key -> (章节标题, xlsx 里的 sheet 名)
SECTIONS = (("quote", "报价助手"), ("config", "配置助手"), ("rule", "规则助手"))
_COLUMNS = ("code", "name", "type", "pk", "fk", "note")
_HEADER = "| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |"
_DIVIDER = "| --- | --- | --- | --- | --- | --- |"


# --------------------------------------------------------------------------- #
# 转义：表格单元格不能出现裸的竖线与换行
# --------------------------------------------------------------------------- #
def _esc(value) -> str:
    return (str(value or "").replace("\\", "\\\\")
            .replace("|", "\\|").replace("\n", "\\n").replace("\r", ""))


def _unesc(text: str) -> str:
    out, escaped = [], False
    for ch in text:
        if escaped:
            out.append({"n": "\n", "|": "|", "\\": "\\"}.get(ch, ch))
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    if escaped:
        out.append("\\")
    return "".join(out)


# --------------------------------------------------------------------------- #
# 生成
# --------------------------------------------------------------------------- #
def dump_md(onto: dict) -> str:
    lines = [
        "# 亿纬锂能 DA 本体",
        "",
        "> 由《亿纬锂能DA梳理.xlsx》生成，**请勿手工编辑** —— 改动请改 xlsx，",
        "> 再运行 `python cpq_ontology.py` 重新生成（`--check` 可对账）。",
        "> 三个助手各一节；每个逻辑实体一张属性表，表名即物理表名。",
        "",
    ]
    for key, title in SECTIONS:
        section = onto.get(key) or {}
        entities = section.get("entities") or []
        lines += [f"## {title}（{key}）", "",
                  f"共 {len(entities)} 个逻辑实体。", ""]
        for entity in entities:
            lines += [
                f"### {entity['table']}",
                "",
                f"- 业务对象：{entity.get('business_object') or '—'}",
                f"- 逻辑实体：{entity.get('cn') or '—'}",
                "",
                _HEADER,
                _DIVIDER,
            ]
            for attr in entity.get("attrs") or []:
                cells = " | ".join(_esc(attr.get(column)) for column in _COLUMNS)
                lines.append(f"| {cells} |")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def _split_cells(line: str) -> list:
    """按未转义的 | 切分一行表格。"""
    cells, buffer, escaped = [], [], False
    for ch in line.strip().strip("|") if line.strip().startswith("|") else line:
        if escaped:
            buffer.append("\\" + ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append("".join(buffer))
            buffer = []
        else:
            buffer.append(ch)
    if escaped:
        buffer.append("\\")
    cells.append("".join(buffer))
    return [_unesc(cell.strip()) for cell in cells]


def parse_md(text: str) -> dict:
    """把 .md 还原成 cpq_db._load_ontology() 的结构。"""
    by_key = {key: [] for key, _ in SECTIONS}
    title_to_key = {title: key for key, title in SECTIONS}
    current_key = None
    entity = None
    in_table = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            head = line[3:].strip()
            # "报价助手（quote）" —— 括号里的 key 是权威，标题只是给人看的
            key = head.split("（")[-1].rstrip("）").strip() if "（" in head else ""
            current_key = key if key in by_key else title_to_key.get(head)
            entity, in_table = None, False
            continue
        if line.startswith("### "):
            entity = {"table": line[4:].strip(), "cn": "", "business_object": "", "attrs": []}
            if current_key:
                by_key[current_key].append(entity)
            in_table = False
            continue
        if entity is None:
            continue
        if line.startswith("- 业务对象："):
            value = line.split("：", 1)[1].strip()
            entity["business_object"] = "" if value == "—" else value
        elif line.startswith("- 逻辑实体："):
            value = line.split("：", 1)[1].strip()
            entity["cn"] = "" if value == "—" else value
        elif line.startswith("|"):
            cells = _split_cells(line)
            joined = "".join(cells).replace("-", "").replace(" ", "")
            if cells[:1] == ["属性编码"] or not joined:
                in_table = True            # 表头与分隔行都不是数据
                continue
            if in_table and len(cells) >= len(_COLUMNS):
                entity["attrs"].append(dict(zip(_COLUMNS, cells[:len(_COLUMNS)])))
        elif not line:
            continue
        else:
            in_table = False

    result = {}
    for key, _ in SECTIONS:
        entities = by_key[key]
        index = {}
        if key == "quote":
            for item in entities:
                index[(item["business_object"], item["cn"])] = item["attrs"]
        result[key] = {"entities": entities, "bi_index": index}
    return result


def load_md() -> dict | None:
    """读 .md；文件不在或读不动就返回 None，由调用方回落到 xlsx。"""
    try:
        return parse_md(MD_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# CLI：生成 / 对账
# --------------------------------------------------------------------------- #
def _shape(onto: dict) -> dict:
    """只比较有语义的部分（bi_index 是 entities 派生出来的，不用比）。"""
    return {key: [{"table": e["table"], "cn": e["cn"],
                   "business_object": e["business_object"],
                   "attrs": [{c: (a.get(c) or "") for c in _COLUMNS} for a in e["attrs"]]}
                  for e in (onto.get(key) or {}).get("entities") or []]
            for key, _ in SECTIONS}


def main(argv: list) -> int:
    import cpq_db

    source = cpq_db._load_ontology_xlsx()
    text = dump_md(source)
    if "--check" in argv:
        if not MD_PATH.exists():
            print(f"缺少 {MD_PATH.name}，请运行 python cpq_ontology.py 生成", file=sys.stderr)
            return 1
        current = parse_md(MD_PATH.read_text(encoding="utf-8"))
        if _shape(current) != _shape(source):
            print(f"{MD_PATH.name} 与 xlsx 不一致，请重新生成", file=sys.stderr)
            return 1
        print(f"{MD_PATH.name} 与 xlsx 一致")
        return 0
    MD_PATH.write_text(text, encoding="utf-8")
    counts = "；".join(
        f"{title} {len((source.get(key) or {}).get('entities') or [])} 实体 / "
        f"{sum(len(e['attrs']) for e in (source.get(key) or {}).get('entities') or [])} 属性"
        for key, title in SECTIONS)
    print(f"已写入 {MD_PATH}（{counts}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
