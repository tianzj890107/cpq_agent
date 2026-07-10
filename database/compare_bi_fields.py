from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR.parent
XLSX_PATH = WORKSPACE_DIR / "报价业务流程.xlsx"
DB_PATH = BASE_DIR / "亿纬锂能_da.sqlite"
OUT_PATH = BASE_DIR / "bi字段_vs_数据库实际字段差异.json"


def clean(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def load_bi() -> pd.DataFrame:
    df = pd.read_excel(XLSX_PATH, sheet_name="BI及关键属性")
    df = df.dropna(how="all").copy()
    df.columns = [clean(c) for c in df.columns]
    for col in ("业务对象", "逻辑实体"):
        if col in df.columns:
            df[col] = df[col].ffill()
    df["业务属性名称"] = df["业务属性名称"].map(clean)
    df = df[df["业务属性名称"].notna()].copy()
    return df


def load_actual_columns(conn: sqlite3.Connection) -> list[dict]:
    rows = []
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in tables:
        for col in conn.execute(f"PRAGMA table_info([{table}])"):
            rows.append(
                {
                    "table": table,
                    "column": col[1],
                    "type": col[2],
                    "pk": bool(col[5]),
                }
            )
    return rows


def main() -> None:
    bi = load_bi()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    actual_columns = load_actual_columns(conn)
    actual_col_set = {(r["table"], r["column"]) for r in actual_columns}
    actual_col_names = {r["column"] for r in actual_columns}

    da_fields = [
        dict(row)
        for row in conn.execute(
            """
            SELECT assistant, business_object, logic_entity, physical_table,
                   attribute_name, field_code
            FROM da_fields
            """
        )
    ]
    da_attr_map: dict[str, list[dict]] = {}
    for row in da_fields:
        attr = clean(row.get("attribute_name"))
        if attr:
            da_attr_map.setdefault(attr, []).append(row)

    realized_da = []
    unrealized_da = []
    for row in da_fields:
        physical_table = row.get("physical_table")
        field_code = row.get("field_code")
        if physical_table and field_code:
            if (physical_table, field_code) in actual_col_set:
                realized_da.append(row)
            else:
                unrealized_da.append(row)

    bi_realized = []
    bi_da_only = []
    bi_missing_da = []
    bi_direct_column_name = []

    for _, item in bi.iterrows():
        record = {
            "业务对象": clean(item.get("业务对象")),
            "逻辑实体": clean(item.get("逻辑实体")),
            "业务属性名称": clean(item.get("业务属性名称")),
            "示例": clean(item.get("示例")),
        }
        attr = record["业务属性名称"]
        candidates = da_attr_map.get(attr, [])
        direct_cols = [col for col in actual_columns if col["column"] == attr]
        if direct_cols:
            bi_direct_column_name.append({**record, "actual_columns": direct_cols})
        if not candidates:
            bi_missing_da.append(record)
            continue

        physical_matches = []
        for candidate in candidates:
            physical_table = candidate.get("physical_table")
            field_code = candidate.get("field_code")
            if physical_table and field_code and (physical_table, field_code) in actual_col_set:
                physical_matches.append(
                    {
                        "assistant": candidate.get("assistant"),
                        "physical_table": physical_table,
                        "field_code": field_code,
                    }
                )
        if physical_matches:
            bi_realized.append({**record, "physical_matches": physical_matches})
        else:
            bi_da_only.append(
                {
                    **record,
                    "da_candidates": [
                        {
                            "assistant": c.get("assistant"),
                            "physical_table": c.get("physical_table"),
                            "field_code": c.get("field_code"),
                        }
                        for c in candidates
                    ],
                }
            )

    bi_attrs = {clean(v) for v in bi["业务属性名称"].tolist() if clean(v)}
    da_attrs = {clean(r.get("attribute_name")) for r in da_fields if clean(r.get("attribute_name"))}

    result = {
        "summary": {
            "bi_rows": int(len(bi)),
            "bi_unique_attributes": len(bi_attrs),
            "bi_logic_entities": int(bi["逻辑实体"].nunique()),
            "actual_tables": len({r["table"] for r in actual_columns}),
            "actual_columns": len(actual_columns),
            "da_fields": len(da_fields),
            "da_realized_physical_fields": len(realized_da),
            "da_unrealized_physical_fields": len(unrealized_da),
            "bi_attrs_in_da": len(bi_attrs & da_attrs),
            "bi_attrs_not_in_da": len(bi_attrs - da_attrs),
            "bi_rows_realized_as_physical_fields": len(bi_realized),
            "bi_rows_in_da_but_not_physical": len(bi_da_only),
            "bi_rows_not_in_da": len(bi_missing_da),
            "bi_rows_directly_equal_actual_column_name": len(bi_direct_column_name),
        },
        "bi_entity_counts": bi["逻辑实体"].value_counts().to_dict(),
        "actual_tables": sorted({r["table"] for r in actual_columns}),
        "actual_columns": actual_columns,
        "bi_realized": bi_realized,
        "bi_in_da_but_not_physical": bi_da_only,
        "bi_not_in_da": bi_missing_da,
        "bi_direct_column_name": bi_direct_column_name,
        "da_physical_not_created": unrealized_da,
    }

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("top_entities")
    for entity, count in list(result["bi_entity_counts"].items())[:20]:
        print(f"{entity}: {count}")
    print(f"wrote: {OUT_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
