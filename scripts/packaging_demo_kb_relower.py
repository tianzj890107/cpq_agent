"""把包装演示 mock 的单价重定为「酒盒实际 BOM 最接近材料的同族低值」。默认只打印计划。

规则（Spec `packaging-demo-mock-lower-of.md` §1）：
  1. 每一条 `MAT-PKG-*` 演示材料先锚到**酒盒实际 BOM**（`master_data.md_clm_material_base_info`，
     `YT-JW-XR21-700ML-*` 22 件）里**最接近**的那条材料（逐字同名优先）；
  2. 由那条 BOM 材料定它的**材质族**（不按名字里的 PET/光银关键字猜），
     再取两张业务工作簿里**同族吨价的低值**（`报价逻辑-0903` vs `成本测算明细`）；
  3. BOM 里找不到对口（衬纸 / 特种纸 / 纸管）的保持现值，并写明「无对口，保持」。

只追加一条带新来源 (`packaging_demo_20261014_bom_anchor`) 与 `valid_from` 的价格行，
不覆盖历史；不碰成本公式、`kb_cost_rate` 费率与任何权威记录。`packaging_demo_20261014_lower_of`
（按关键字族取低）被本批取代：同日、`valid_from` 更晚，读侧按最新一条取值。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cpq_kb

SOURCE = "packaging_demo_20261014_bom_anchor"
EFFECTIVE_FROM = "2026-10-14 12:00:00"
RULE = "closest_wine_box_bom_material"

_GSTAMP = "报价逻辑-0903.xlsx/报价-工费率!"
_MSTAMP = "成本测算明细.xlsx/报价-行业标准!"

#: 材质族 → 两张工作簿里的同族吨价（元/吨）。逐格引用，取低值时按 ref 一并留痕。
WORKBOOK_TON_PRICES = {
    "greyboard": ((_GSTAMP + "N3", 3950.0), (_MSTAMP + "M3", 3950.0)),
    "face_paper": ((_GSTAMP + "N2", 6300.0), (_MSTAMP + "M2", 12050.0)),
    "white_card": ((_GSTAMP + "N4", 6700.0), (_MSTAMP + "M4", 10200.0)),
}

#: 酒盒实际 BOM（`YT-JW-XR21-700ML-*`）里的**材料 → 材质族 + 该材料走的工序**。
#: 逐字来自 `master_data.md_clm_material_base_info."desc"` 的「材料：…；工艺：…」两段。
WINE_BOX_BOM = {
    "1.8MM双灰裱225G太阳铜版底PET光银": ("greyboard", "开料-印刷1专(内裱纸)-裱纸-模切"),
    "225G太阳铜版底PET光银": ("face_paper", "开料-UV印刷-19um即涂防刮花哑胶(佳泓)-模切-包盒"),
    "255G太阳铜版底PET光银": ("face_paper", "开料-UV印刷1专-19um即涂防刮花哑胶(佳泓)-模切-包盒"),
    "2.5MM富业双灰": ("greyboard", "开料-模切"),
    "2.5MM富业灰板": ("greyboard", "开料-模切"),
    "2MM单面富业滑灰板": ("greyboard", "开料-模切-V槽90度-包盒"),
    "2MM富业单面滑灰板": ("greyboard", "开料-模切-V槽90度-包盒"),
    "2MM富业双灰对裱11层共22MM厚": ("greyboard", "开料-模切-对裱-包盒"),
    "350G玖龙粉灰": ("greyboard", "开料-模切-粘于里层灰板"),
    "325G白卡底PET光银": ("white_card", "开料-UV印刷1专-19um即涂防刮花哑胶(佳泓)-模切-包盒"),
    "325G白卡底PET光银+3层300G白卡+225G铜版底PET光银": (
        "white_card", "开料-UV印刷K+4专-19um即涂防刮花哑胶(佳泓)-丝印UV-浮雕击凸-模切-粘"),
    "235G白卡底PET光银+A9（E坑）130/120": (
        "white_card", "开料-UV印刷1专-19um即涂防刮花哑胶(佳泓)-裱坑-模切-组装"),
    "38度A级白色EVA 125×54×35MM异形": ("eva", "外购"),
    "长方形镀锌双面磁铁侧吸3500GS 15×5×2MM": ("magnet", "外购"),
}

#: 每条演示材料锚到的**最接近酒盒 BOM 材料**（`packaging_material_match.score_candidate`
#: 逐条复核后固定，可复跑复核）；空串 = BOM 里没有对口 → 保持现值。
CLOSEST_BOM_MATERIAL = {
    "MAT-PKG-GREYBOARD": "2.5MM富业灰板",
    "MAT-PKG-FACEPAPER": "255G太阳铜版底PET光银",
    "MAT-PKG-LINER": "",
    "MAT-PKG-SPECIAL": "",
    "MAT-PKG-EVA": "38度A级白色EVA 125×54×35MM异形",
    "MAT-PKG-DEMO-FACE225": "225G太阳铜版底PET光银",
    "MAT-PKG-DEMO-FACE325": "325G白卡底PET光银",
    "MAT-PKG-DEMO-LAMINATED": "325G白卡底PET光银+3层300G白卡+225G铜版底PET光银",
    "MAT-PKG-DEMO-CARD300": "325G白卡底PET光银",
    "MAT-PKG-DEMO-BOARD18": "1.8MM双灰裱225G太阳铜版底PET光银",
    "MAT-PKG-DEMO-BOARD20": "2MM富业单面滑灰板",
    "MAT-PKG-DEMO-BOARD25": "2.5MM富业灰板",
    "MAT-PKG-DEMO-BOARD-SILVER": "1.8MM双灰裱225G太阳铜版底PET光银",
    "MAT-PKG-DEMO-EVA35": "38度A级白色EVA 125×54×35MM异形",
    "MAT-PKG-DEMO-TUBE": "",
    "MAT-PKG-DEMO-LINER": "",
    "MAT-PKG-DEMO-MAGNET": "长方形镀锌双面磁铁侧吸3500GS 15×5×2MM",
    "MAT-PKG-DEMO-GENERAL": "2.5MM富业灰板",
    "MAT-PKG-DEMO-SUN225": "225G太阳铜版底PET光银",
    "MAT-PKG-DEMO-SUN255": "255G太阳铜版底PET光银",
    "MAT-PKG-DEMO-WHITE325": "325G白卡底PET光银",
    "MAT-PKG-DEMO-GREY350": "350G玖龙粉灰",
    "MAT-PKG-DEMO-FUYE25": "2.5MM富业双灰",
    "MAT-PKG-DEMO-FUYE25GREY": "2.5MM富业灰板",
    "MAT-PKG-DEMO-FUYE20SLIP": "2MM单面富业滑灰板",
    "MAT-PKG-DEMO-FUYE20SLIP2": "2MM富业单面滑灰板",
    "MAT-PKG-DEMO-BOARD22": "2MM富业双灰对裱11层共22MM厚",
    "MAT-PKG-DEMO-LAMINATEDWHITE": "325G白卡底PET光银+3层300G白卡+225G铜版底PET光银",
    "MAT-PKG-DEMO-CORRUGATED": "235G白卡底PET光银+A9（E坑）130/120",
    "MAT-PKG-DEMO-BOARD18SUN": "1.8MM双灰裱225G太阳铜版底PET光银",
}

#: 写入工具只认这些「包装演示」材料（5 条基础包材 + 25 条 MAT-PKG-DEMO-*）。
#: 值是 2026-10-14 只读读到的**原始**现价（元/kg）；apply() 复核真库现值。
SNAPSHOT = {
    "MAT-PKG-GREYBOARD": ("灰板（双灰纸板）", 6.5),
    "MAT-PKG-FACEPAPER": ("铜版纸面纸", 12.8),
    "MAT-PKG-LINER": ("内衬纸（牛皮衬纸）", 9.6),
    "MAT-PKG-SPECIAL": ("特种纸（充皮纸）", 26.0),
    "MAT-PKG-EVA": ("EVA 片材", 18.5),
    "MAT-PKG-DEMO-FACE225": ("225G铜版底PET光银", 12.8),
    "MAT-PKG-DEMO-FACE325": ("325G PET光银", 12.8),
    "MAT-PKG-DEMO-LAMINATED": ("325G PET光银+3层300G白卡+225gPET光银", 12.8),
    "MAT-PKG-DEMO-CARD300": ("300G白卡", 11.5),
    "MAT-PKG-DEMO-BOARD18": ("1.8mm灰板", 6.5),
    "MAT-PKG-DEMO-BOARD20": ("2mm灰板", 6.5),
    "MAT-PKG-DEMO-BOARD25": ("2.5MM灰板", 6.5),
    "MAT-PKG-DEMO-BOARD-SILVER": ("1.8mm灰板裱光银纸", 12.8),
    "MAT-PKG-DEMO-EVA35": ("35mm厚EVA", 25.0),
    "MAT-PKG-DEMO-TUBE": ("纸管", 9.0),
    "MAT-PKG-DEMO-LINER": ("衬纸", 9.6),
    "MAT-PKG-DEMO-MAGNET": ("磁铁", 12.0),
    "MAT-PKG-DEMO-GENERAL": ("包装通用演示纸板", 6.5),
    "MAT-PKG-DEMO-SUN225": ("225G太阳铜版底PET光银", 12.8),
    "MAT-PKG-DEMO-SUN255": ("255G太阳铜版底PET光银", 12.8),
    "MAT-PKG-DEMO-WHITE325": ("325G白卡底PET光银", 12.8),
    "MAT-PKG-DEMO-GREY350": ("350G玖龙粉灰", 12.0),
    "MAT-PKG-DEMO-FUYE25": ("2.5MM富业双灰", 6.5),
    "MAT-PKG-DEMO-FUYE25GREY": ("2.5MM富业灰板", 6.5),
    "MAT-PKG-DEMO-FUYE20SLIP": ("2MM单面富业滑灰板", 6.5),
    "MAT-PKG-DEMO-FUYE20SLIP2": ("2MM富业单面滑灰板", 6.5),
    "MAT-PKG-DEMO-BOARD22": ("2MM富业双灰对裱11层共22MM厚", 6.5),
    "MAT-PKG-DEMO-LAMINATEDWHITE": (
        "325G白卡底PET光银+3层300G白卡+225G铜版底PET光银", 12.8),
    "MAT-PKG-DEMO-CORRUGATED": ("235G白卡底PET光银+A9（E坑）130/120", 12.8),
    "MAT-PKG-DEMO-BOARD18SUN": ("1.8MM双灰裱225G太阳铜版底PET光银", 12.8),
}


def anchor_of(code: str) -> dict:
    """一条演示材料锚到的酒盒实际 BOM 材料：族、工序、来源。纯函数。"""
    bom = CLOSEST_BOM_MATERIAL.get(code, "")
    family, process = WINE_BOX_BOM.get(bom, ("", ""))
    return {"bom_material": bom, "family": family, "process": process,
            "source": ("master_data.md_clm_material_base_info/YT-JW-XR21-700ML"
                       if bom else "")}


def decide(*, code: str, name: str, current_price_kg: float) -> dict:
    """一条材料的重定裁决：锚 → 族 → min(KB 现值, 同族工作簿吨价)。无对口保持现值。"""
    anchor = anchor_of(code)
    current_ton = float(current_price_kg) * 1000.0
    candidates = [{"ref": "kb_material_price/%s" % code, "ton_price": current_ton}]
    for ref, ton in WORKBOOK_TON_PRICES.get(anchor["family"], ()):
        candidates.append({"ref": ref, "ton_price": float(ton)})
    chosen = min(candidates, key=lambda item: (item["ton_price"], item["ref"]))
    return {
        "material_code": code,
        "name": name,
        "bom_material": anchor["bom_material"],
        "bom_process": anchor["process"],
        "anchor_source": anchor["source"],
        "family": anchor["family"],
        "current_price_kg": float(current_price_kg),
        "target_price_kg": round(chosen["ton_price"] / 1000.0, 6),
        "chosen_ref": chosen["ref"],
        "rejected": [item for item in candidates if item is not chosen],
        "changed": chosen["ton_price"] < current_ton - 1e-9,
    }


def plan(current=None) -> dict:
    """默认用离线快照，输出「要追加哪些价格行 + 每条为什么」。不连库。"""
    rows, decisions = [], []
    for code, (name, price) in (current or SNAPSHOT).items():
        row = decide(code=code, name=name, current_price_kg=price)
        decisions.append(row)
        if not row["changed"]:
            continue
        rejected = "、".join("%s=%s元/吨" % (item["ref"], item["ton_price"])
                            for item in row["rejected"])
        why = ("锚 %s（走 %s）→ %s 族；" % (row["bom_material"], row["bom_process"],
                                          row["family"])) if row["bom_material"] else "BOM 无对口；"
        rows.append({
            "material_code": code,
            "price": row["target_price_kg"],
            "currency": "CNY",
            "unit": "kg",
            "price_type": "market",
            "valid_from": EFFECTIVE_FROM,
            "source_name": SOURCE,
            "evidence": "%s：%s采用 %s（%s 元/吨）；对照 %s"
                        % (RULE, why, row["chosen_ref"],
                           row["target_price_kg"] * 1000.0, rejected),
            "confidence": 0.3,
            "created_by": "demo_relower",
            "created_at": EFFECTIVE_FROM,
        })
    return {"kb_material_price": rows, "decisions": decisions}


def _kb_current() -> dict:
    conn = cpq_kb._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT ON (material_code) material_code, price, unit
                           FROM %s.kb_material_price
                           ORDER BY material_code, valid_from DESC, price_id DESC""" % cpq_kb.SCHEMA)
            return {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    finally:
        conn.close()


def apply(rows, backup) -> dict:
    from psycopg import sql
    snapshot = cpq_kb.snapshot()
    with Path(backup).open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False, default=str)
    current = {code: price for code, (price, _unit) in _kb_current().items()}
    changes = 0
    conn = cpq_kb._connect()
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (2026101402,))
            for record in rows:
                if abs(float(current.get(record["material_code"], -1)) - float(record["price"])) < 1e-9:
                    continue  # 库里现值已经是这条低值，不重复追加
                cur.execute(sql.SQL(
                    "SELECT 1 FROM {}.kb_material_price WHERE material_code=%s AND source_name=%s"
                    "").format(sql.Identifier(cpq_kb.SCHEMA)),
                    (record["material_code"], SOURCE))
                if cur.fetchone():
                    continue
                cols = list(record)
                cur.execute(sql.SQL("INSERT INTO {}.kb_material_price ({}) VALUES ({})").format(
                    sql.Identifier(cpq_kb.SCHEMA),
                    sql.SQL(",").join(map(sql.Identifier, cols)),
                    sql.SQL(",").join(sql.Placeholder() for _ in cols)),
                    [record[key] for key in cols])
                changes += cur.rowcount
            version = cpq_kb._bump_version(cur, changes)
    finally:
        conn.close()
    return {"changed": changes, "kb_version": version, "backup": str(backup), "source": SOURCE}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    planned = plan()
    print(json.dumps(apply(planned["kb_material_price"], args.backup)
                     if args.apply else planned, ensure_ascii=False, default=str))
