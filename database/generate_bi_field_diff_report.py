from __future__ import annotations

import collections
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "bi字段_vs_数据库实际字段差异.json"
OUT_PATH = BASE_DIR / "BI及关键属性_vs_数据库实际字段差异报告.md"


def top_counter(rows: list[dict], field: str, limit: int = 12) -> list[tuple[str, int]]:
    return collections.Counter(row.get(field) or "" for row in rows).most_common(limit)


def sample_rows(rows: list[dict], limit: int = 20) -> list[str]:
    lines = []
    for row in rows[:limit]:
        lines.append(
            f"| {row.get('业务对象') or ''} | {row.get('逻辑实体') or ''} | {row.get('业务属性名称') or ''} | {row.get('示例') or ''} |"
        )
    return lines


def main() -> None:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    s = data["summary"]

    lines = [
        "# BI及关键属性 vs 数据库实际字段差异报告",
        "",
        "## 1. 对比口径",
        "",
        "- Excel 来源：`报价业务流程.xlsx` / `BI及关键属性`。",
        "- 数据库来源：`database/亿纬锂能_da.sqlite`。",
        "- “数据库实际字段”指 SQLite 中已经创建出来的物理表字段。",
        "- DA 字段目录单独判断：如果只在 `da_fields` / `quote_assistant_fields` 中存在，但没有物理表字段，算“在 DA 但未落物理表”。",
        "",
        "## 2. 总体结论",
        "",
        f"- BI 有效字段行：{s['bi_rows']} 行。",
        f"- BI 去重业务属性：{s['bi_unique_attributes']} 个。",
        f"- BI 涉及逻辑实体：{s['bi_logic_entities']} 个。",
        f"- 当前 SQLite 物理表：{s['actual_tables']} 张，物理字段：{s['actual_columns']} 个。",
        f"- BI 字段行真正落到当前物理字段：{s['bi_rows_realized_as_physical_fields']} 行。",
        f"- BI 字段行在 DA 中存在但没有物理字段：{s['bi_rows_in_da_but_not_physical']} 行。",
        f"- BI 字段行连 DA 字段目录也没有：{s['bi_rows_not_in_da']} 行。",
        f"- BI 中文属性名与数据库物理列名直接相同：{s['bi_rows_directly_equal_actual_column_name']} 行。",
        "",
        "> 结论：当前库主要已经落了 BOM 与规则相关物理表；报价业务流程中的 BI 字段大部分还只是字段目录或尚未进入 DA，尚未形成报价域物理表。",
        "",
        "## 3. 已落到物理字段的 BI 字段",
        "",
        "| 业务对象 | 逻辑实体 | BI 属性 | 示例 |",
        "|---|---|---|---|",
    ]
    lines.extend(sample_rows(data["bi_realized"], 30))

    lines.extend(
        [
            "",
            "## 4. 在 DA 中存在但没有物理字段的主要实体",
            "",
            "| 逻辑实体 | 字段行数 |",
            "|---|---:|",
        ]
    )
    for entity, count in top_counter(data["bi_in_da_but_not_physical"], "逻辑实体", 20):
        lines.append(f"| {entity} | {count} |")

    lines.extend(
        [
            "",
            "典型字段样例：",
            "",
            "| 业务对象 | 逻辑实体 | BI 属性 | 示例 |",
            "|---|---|---|---|",
        ]
    )
    lines.extend(sample_rows(data["bi_in_da_but_not_physical"], 30))

    lines.extend(
        [
            "",
            "## 5. BI 中有、DA 中也没有的主要实体",
            "",
            "| 逻辑实体 | 字段行数 |",
            "|---|---:|",
        ]
    )
    for entity, count in top_counter(data["bi_not_in_da"], "逻辑实体", 20):
        lines.append(f"| {entity} | {count} |")

    lines.extend(
        [
            "",
            "典型字段样例：",
            "",
            "| 业务对象 | 逻辑实体 | BI 属性 | 示例 |",
            "|---|---|---|---|",
        ]
    )
    lines.extend(sample_rows(data["bi_not_in_da"], 30))

    lines.extend(
        [
            "",
            "## 6. DA 已定义物理表但当前未建表",
            "",
            "| DA 物理表 | 字段数 | 说明 |",
            "|---|---:|---|",
        ]
    )
    table_counts = collections.Counter(row.get("physical_table") for row in data["da_physical_not_created"])
    for table, count in table_counts.most_common():
        lines.append(f"| `{table}` | {count} | DA 中已有字段定义，但 SQLite 尚未创建该物理表。 |")

    lines.extend(
        [
            "",
            "## 7. 建议补齐方向",
            "",
            "1. 先补报价域核心物理表：测算基本信息、目的地信息、产品信息、产品技术参数、付款信息、物流信息、价格偏差。",
            "2. 再补调价/价格发布域：价格发布数据.价格基本信息、价格发布明细、整单 PV 调价参考信息、产品 PV 调价参考信息。",
            "3. 当前 `md_clm_material_cost_cnf`、`md_clm_material_feature_cnf`、`bd_clm_feature` 已在 DA 中定义，应作为规则/成本域优先建表。",
            "4. 物理字段建议使用英文 `field_code`，同时保留 DA 字段目录作为中文属性映射，避免直接用中文 BI 属性名建列。",
            "",
        ]
    )

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(OUT_PATH)


if __name__ == "__main__":
    main()
