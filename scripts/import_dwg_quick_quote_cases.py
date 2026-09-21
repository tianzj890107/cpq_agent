#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把两份 DWG 实样（`酒盒.dwg` / `圆盘盒.dwg`）沉淀成标准报价案例，写进报价侧 PG。

背景：逆向快速报价（`cpq_quick_quote_case.py`）的案例表 `cpq_wf.cpq_qq_standard_case`
一直是 0 行，所以快速报价出不了**真实**价。知识库侧 `cpq_kb.kb_packaging_box_type` 里
已经有这两份 DWG 的 `source_type='dwg_confirmed'` 盒型（连同 25 条零件模板、16 条工序
模板），本脚本把它们按**同一份口径**沉淀成案例行，不再另抄一份数据。

三条纪律：

  · **默认 dry-run**：只读库、只打印"要写什么"，真写要显式 `--confirm`；
  · **幂等**：`case_code` 由盒型编码决定（`QQ-<box_type_code>`）。同一份内容重复执行
    不新增行、不升版本；内容变了才写；
  · **不编数据**：DWG 里没有价格，`standard_price` / `standard_cost` 默认留空并把缺口
    打印出来 —— 快速报价的准入判据会如实报 `missing_fields`，绝不拿一个来路不明的数
    当基准价。价格只能由 `--price` / `--cost` 显式给（业务口径）。

用法：

    # 看一眼要写什么（默认，不写库）
    ./open-claude/.venv/bin/python scripts/import_dwg_quick_quote_cases.py
    ./open-claude/.venv/bin/python scripts/import_dwg_quick_quote_cases.py --json

    # 真写
    ./open-claude/.venv/bin/python scripts/import_dwg_quick_quote_cases.py --confirm

    # 带业务口径的价格 + 审核状态
    ./open-claude/.venv/bin/python scripts/import_dwg_quick_quote_cases.py \\
        --price 23.40 --cost 18.10 --review-status reviewed --confirm

只认 `cpq_kb` 里 `box_type_code` 以 `YT-DWG` 开头的盒型（今天正好是那两份 DWG 实样）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cpq_auth                                  # noqa: E402
import cpq_kb                                    # noqa: E402
import cpq_quick_quote_case as qq_case           # noqa: E402

#: 只认这两份 DWG 实样（知识库侧盒型编码前缀）。
BOX_PREFIX = "YT-DWG"
#: 案例编码 = 前缀 + 盒型编码。前缀进主键，重跑不会产生第二行。
CASE_CODE_PREFIX = "QQ-"
#: 内托口径：DWG 零件表里「内托」那一组的实际用料（EVA / 纸卡）。
INSERT_HINTS = (("EVA", "EVA内托"), ("灰板", "灰板内托"), ("单粉", "纸卡内托"),
                ("瓦楞", "纸卡内托"), ("卡纸", "纸卡内托"))
#: 材料文字里能明确读出「覆膜」的词（哑胶/光胶/触感膜…）。
LAMINATION_WORDS = ("哑胶", "光胶", "覆哑膜", "覆光膜", "覆膜")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _num(value):
    if value is None or value == "":
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def _truthy_word(value) -> bool:
    return _text(value).startswith("是") or "是" == _text(value)


# --------------------------------------------------------------------------- #
# 从知识库盒型 / 零件 / 工序派生案例行（纯函数，可离线断言）
# --------------------------------------------------------------------------- #
def insert_type_of(parts) -> str:
    """内托口径：取零件表里 component 含「内托」的那些件的材料文字推一个口径。

    推不出来就留空（宁可缺，不猜）。返回值同时是快速报价的必填项，缺了会在准入里
    如实报 `missing_fields`。
    """
    materials = " ".join(_text(row.get("material")) for row in parts
                         if "内托" in _text(row.get("component"))
                         or "托" in _text(row.get("name")))
    for hint, label in INSERT_HINTS:
        if hint in materials:
            return label
    return ""


def face_paper_gsm_of(box):
    """面纸克重：取盒型 `face_paper_gsm` 里的第一个数（`225/235/325/350` → 225）。"""
    return _num(box.get("face_paper_gsm"))


def lamination_of(box, parts) -> bool | None:
    """覆膜：只在 DWG 材料文字明确写了「哑胶/覆光膜」这类词时才为 True，否则留空。"""
    blob = " ".join([_text(box.get("face_paper_gsm"))]
                    + [_text(row.get("material")) for row in parts])
    if any(word in blob for word in LAMINATION_WORDS):
        return True
    return None


def summarize_parts(parts, limit=6) -> str:
    names = [_text(row.get("name")) for row in parts if _text(row.get("name"))]
    head = "、".join(names[:limit])
    tail = "" if len(names) <= limit else " 等"
    return "%d 件：%s%s" % (len(names), head, tail)


def summarize_process(steps) -> str:
    return " → ".join(_text(row.get("step_name")) for row in steps
                      if _text(row.get("step_name")))


def case_from_box(box, parts=None, steps=None, *, price=None, cost=None,
                  review_status="draft", today=None) -> dict:
    """把一条 `kb_packaging_box_type` 行派生为标准报价案例（纯函数，不读库）。

    **尺寸取 DWG 标注区间的上限**：盒型表存的是标注区间（如 219.0–220.5），案例需要
    一个确定值；取上限同时写进 `source_ref`，不假装它是精确的配合后内尺寸。
    """
    parts = list(parts or [])
    steps = list(steps or [])
    code = _text(box.get("box_type_code"))
    day = (today or _isodate_today())
    length, width, height = (_num(box.get("size_l_max")), _num(box.get("size_w_max")),
                             _num(box.get("size_h_max")))
    raw_range = "%s-%s × %s-%s × %s-%s" % (
        _text(box.get("size_l_min")), _text(box.get("size_l_max")),
        _text(box.get("size_w_min")), _text(box.get("size_w_max")),
        _text(box.get("size_h_min")), _text(box.get("size_h_max")))
    case = {
        "case_code": CASE_CODE_PREFIX + code,
        "case_version": 1,
        # 客户名不猜：DWG 里没有客户信息，留空比编一个「华东客户A」诚实。
        "customer_masked": "",
        "box_type_code": code,
        "box_family": _text(box.get("family")),
        "closure_type": _text(box.get("closure_type")),
        # 配合间隙：盒型表里是空的（已知缺口），案例这边同样不猜。
        "fit_clearance": None,
        "insert_type": insert_type_of(parts),
        "magnet": None,
        "ribbon": None,
        "window": None,
        "v_groove": True if _truthy_word(box.get("v_groove")) else None,
        "inner_length": length,
        "inner_width": width,
        "inner_height": height,
        "material_code": "",
        # 盒型表里是「板厚 mm」（1.8/2.0/2.5），不是克重：不换算、不填错单位。
        "grey_board_gsm": None,
        "face_paper_gsm": face_paper_gsm_of(box),
        "print_colors": "",
        "lamination": lamination_of(box, parts),
        "hot_stamping": None,
        "quantity_tiers": [],
        "bom_summary": summarize_parts(parts),
        "process_summary": summarize_process(steps),
        # 案例表这两列是 NOT NULL DEFAULT 0，所以「没有价」必须写 0 —— 与
        # `cpq_quick_quote_case._is_blank()` 对价格列的口径一致（0 = 没有基准价），
        # 准入判定会照样报 `missing_fields`，不会把 0 当成一个价。
        "standard_cost": cost if cost is not None else 0,
        "standard_price": price if price is not None else 0,
        "deal_price": None,
        "currency": "CNY",
        "tax_included": False,
        "quote_date": _text(box.get("effective_from")) or day,
        "valid_from": _text(box.get("effective_from")) or day,
        "valid_until": None,
        "source_type": "dwg_confirmed",
        "source_ref": ("DWG 实样 %s（sha256 %s）；原生标注区间 %s，案例取上限；"
                       "价格未由 DWG 提供"
                       % (_text(box.get("source_ref")),
                          _text(box.get("source_sha256"))[:12], raw_range)),
        "review_status": review_status,
        "version": 1,
        "industry": "packaging",
        "source_sha256": _text(box.get("source_sha256")),
        "parser_version": _text(box.get("parser_version")),
        "confirmed_by": _text(box.get("confirmed_by")),
    }
    case = qq_case.normalize_case(case)
    # `normalize_case()` 只保留 CASE_FIELDS，DWG 通道三列会被丢掉：这里显式放回，
    # 由 `write_dwg_provenance()` 单独写库（save_case() 落不了这三列）。
    for key in ("source_sha256", "parser_version", "confirmed_by", "confirmed_at"):
        case[key] = box.get(key)
    return case


def _isodate_today() -> str:
    import datetime as dt
    return dt.date.today().isoformat()


# --------------------------------------------------------------------------- #
# 读知识库
# --------------------------------------------------------------------------- #
def fetch_source(box_prefix=BOX_PREFIX) -> list:
    """读 `cpq_kb`：盒型 + 它的零件模板 + 工序模板（按盒型编码分组）。"""
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn, "SELECT * FROM %s.kb_packaging_box_type WHERE substring(box_type_code,1,%s) = %%s"
                  " ORDER BY box_type_code" % (cpq_kb.SCHEMA, len(box_prefix)), (box_prefix,))
        cols = [d[0] for d in cur.description]
        boxes = [dict(zip(cols, row)) for row in cur.fetchall()]
        out = []
        for box in boxes:
            code = _text(box.get("box_type_code"))
            parts = _rows(conn, "kb_packaging_part_template", code, "seq")
            steps = _rows(conn, "kb_packaging_process_template", code, "seq")
            out.append({"box": box, "parts": parts, "steps": steps})
        return out
    finally:
        conn.close()


def _rows(conn, table, box_code, order) -> list:
    cur = cpq_auth._exec(
        conn, "SELECT * FROM %s.%s WHERE box_type_code = %%s ORDER BY %s"
              % (cpq_kb.SCHEMA, table, order), (box_code,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def resolve_user(username: str) -> dict:
    """把用户名换成带 user_id 的留痕身份（查不到就只带用户名，不伪造 id）。"""
    user = {"username": username}
    if not username:
        return user
    conn = None
    try:
        conn = cpq_auth._connect()
        cur = cpq_auth._exec(
            conn, "SELECT user_id, username FROM %s.cpq_wf_user WHERE username = %%s"
                  % cpq_auth.WF_SCHEMA, (username,))
        for user_id, name in cur.fetchall():
            user["user_id"] = user_id
            break
    except Exception:                                          # noqa: BLE001 - 留痕缺 id 不影响导入
        pass
    finally:
        if conn is not None:
            conn.close()
    return user


# --------------------------------------------------------------------------- #
# 比对 / 写入
# --------------------------------------------------------------------------- #
def diff_of(existing: dict, wanted: dict) -> list:
    """和库里那一行比，列出真正变化的字段（版本与审计列不算变化）。

    两边都先过 `normalize_case()`：库里读回来的是 PG 原生形态（`quantity_tiers` 是
    JSON 文本、日期是 `date` 对象），不归一就会被当成"每跑一次都变了"。
    """
    if not existing:
        return ["<新案例>"]
    existing = qq_case.normalize_case(existing)
    wanted = qq_case.normalize_case(wanted)
    ignored = {"case_version", "version", "updated_at", "created_at", "created_by_user_id"}
    changed = []
    for key in qq_case.CASE_FIELDS:
        if key in ignored:
            continue
        if existing.get(key) != wanted.get(key):
            changed.append(key)
    return changed


def write_dwg_provenance(conn, case) -> None:
    """补齐 DWG 通道三列：`source_sha256` / `parser_version` / `confirmed_by`。

    这三列是批 5 的 DWG 通道列，但 `save_case()` 也只认 `normalize_case()` 输出的
    `CASE_FIELDS`，所以它们**进不了**入库路径（走 `save_case()` 写出来一直是 NULL）。
    这里用参数化 UPDATE 显式补上 —— 不然「这条案例是从哪份 DWG 来的」只能靠
    `source_ref` 里的一段文字。这是**绕开缺口，不是替代**：缺口本身要单独修
    `save_case()` / `normalize_case()`（不在本脚本范围内）。
    """
    cpq_auth._exec(
        conn, "UPDATE %s.%s SET source_sha256 = %%s, parser_version = %%s,"
              " confirmed_by = %%s, confirmed_at = COALESCE(%%s, confirmed_at)"
              " WHERE case_code = %%s"
              % (cpq_auth.WF_SCHEMA, qq_case.CASE_TABLE),
        (case.get("source_sha256") or None, case.get("parser_version") or None,
         case.get("confirmed_by") or None, case.get("confirmed_at"),
         case["case_code"]))


def run(*, box_prefix=BOX_PREFIX, price=None, cost=None, review_status="draft",
        user="wugefei", confirm=False, as_json=False) -> int:
    rows = fetch_source(box_prefix)
    if not rows:
        payload = {"ok": False, "reason": "no_source_box",
                   "message": "知识库里没有 box_type_code 以 %s 开头的盒型；先灌 DWG 盒型再导案例"
                              % box_prefix}
        print(json.dumps(payload, ensure_ascii=False) if as_json else payload["message"])
        return 2

    prepared = []
    for item in rows:
        case = case_from_box(item["box"], item["parts"], item["steps"],
                             price=price, cost=cost, review_status=review_status)
        prepared.append({"case": case, "parts": len(item["parts"]),
                         "steps": len(item["steps"])})

    conn = cpq_auth._connect()
    try:
        existing = {row["case_code"]: row
                    for row in qq_case._fetch_case_rows()}
        planned = []
        for item in prepared:
            case = item["case"]
            changed = diff_of(existing.get(case["case_code"], {}), case)
            verdict = qq_case.quote_eligibility(case)
            planned.append({"case_code": case["case_code"],
                            "box_type_code": case["box_type_code"],
                            "changed": changed,
                            "action": ("insert" if case["case_code"] not in existing
                                       else ("update" if changed else "unchanged")),
                            "eligibility": verdict["reason_code"],
                            "eligibility_reason": verdict["reason"],
                            "missing_required": qq_case.case_missing_fields(case),
                            "parts": item["parts"], "steps": item["steps"]})
        if confirm:
            identity = resolve_user(user)
            for item in planned:
                case = next(row["case"] for row in prepared
                            if row["case"]["case_code"] == item["case_code"])
                if item["action"] != "unchanged":
                    qq_case.save_case(case, conn=conn, user=identity)
                # 内容没变也要补：DWG 通道列不由 save_case() 落库。
                write_dwg_provenance(conn, case)
            conn.commit() if hasattr(conn, "commit") else None
        after = qq_case._fetch_case_rows()
    finally:
        conn.close()

    eligible = [row for row in after if qq_case.quote_eligibility(row)["eligible"]]
    payload = {
        "ok": True,
        "dry_run": not confirm,
        "target": "%s.%s" % (cpq_auth.WF_SCHEMA, qq_case.CASE_TABLE),
        "planned": planned,
        "case_total": len(after),
        "eligible_total": len(eligible),
        "price_given": price is not None,
        "next": ("" if eligible else
                 "还没有可用于快速报价的案例：先给 --price（业务口径的标准单价）"
                 "并把 --review-status 设为 reviewed"),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        _print_report(payload)
    return 0


def _print_report(payload: dict) -> None:
    print("目标库：%s（%s）" % (payload["target"],
                               "dry-run，不写库" if payload["dry_run"] else "已写库"))
    for item in payload["planned"]:
        print("\n· %s ← %s（零件 %s / 工序 %s）"
              % (item["case_code"], item["box_type_code"], item["parts"], item["steps"]))
        print("  动作：%s" % item["action"])
        if item["changed"]:
            print("  变化：%s" % "、".join(item["changed"]))
        print("  准入：%s —— %s" % (item["eligibility"], item["eligibility_reason"]))
        if item["missing_required"]:
            print("  还缺：%s" % "、".join(
                qq_case.FIELD_LABELS.get(key, key) for key in item["missing_required"]))
    print("\n案例表共 %d 行，可用于快速报价 %d 行" % (payload["case_total"],
                                                     payload["eligible_total"]))
    if payload["next"]:
        print("下一步：%s" % payload["next"])


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 DWG 实样盒型沉淀成标准报价案例（默认 dry-run）")
    parser.add_argument("--confirm", action="store_true", help="真的写库（缺省只打印）")
    parser.add_argument("--price", type=float, default=None,
                        help="业务口径的标准单价（DWG 里没有价格，不给就留空）")
    parser.add_argument("--cost", type=float, default=None, help="业务口径的标准成本")
    parser.add_argument("--review-status", default="draft",
                        choices=list(qq_case.CASE_REVIEW_STATUSES),
                        help="draft（缺省，仍需人工审）/ reviewed（可用于快速报价）/ retired")
    parser.add_argument("--user", default="wugefei", help="留痕用户（谁把案例放进了库）")
    parser.add_argument("--box-prefix", default=BOX_PREFIX, help="只导这个前缀的盒型")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run(box_prefix=args.box_prefix, price=args.price, cost=args.cost,
                   review_status=args.review_status, user=args.user,
                   confirm=args.confirm, as_json=args.json)
    except (cpq_auth.BackendUnavailable, qq_case.CaseLibraryUnavailable) as exc:
        print("数据库不可用：%s" % str(exc)[:300])
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
