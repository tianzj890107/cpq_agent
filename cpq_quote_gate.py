# -*- coding: utf-8 -*-
"""报价步骤完成的纯函数门禁，供 Agent API 与卡片写接口共用。"""
from __future__ import annotations

def _gate_num(value):
    """宽松取数：None / "" / 非数字 → None（**不把缺失当成 0**，Spec §4 最后一句）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _gate_rows(data, *section_ids):
    """从步骤快照里取某个分区的表行（`{section: {"数据": [...]}}`，键名兼容 data）。"""
    if not isinstance(data, dict):
        return []
    for sid in section_ids:
        section = data.get(sid)
        if not isinstance(section, dict):
            continue
        rows = section.get("数据", section.get("data"))
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _gate_detail_rows(data, *section_ids):
    """取某个分区的**明细行**，并把两种形状分开（Spec §2.1）。

    返回 ``(table_rows, summary_rows)``：
      · 表行 = 页面/模型填的明细表行（**不带**「项目」列）；
      · 汇总行 = 定价引擎写进第 5 步的那份汇总 —— `kind:"summary"` 的 `rows`，
        或表信封里带「项目」列的行。汇总行**不当表行**，不参与逐行（数量/单价/复算）判据。
    """
    if not isinstance(data, dict):
        return [], []
    for sid in section_ids:
        section = data.get(sid)
        if not isinstance(section, dict):
            continue
        rows = []
        raw = section.get("数据", section.get("data"))
        if isinstance(raw, list):
            rows = [row for row in raw if isinstance(row, dict)]
        if not rows:
            reported = section.get("rows")
            if isinstance(reported, list):
                rows = [row for row in reported if isinstance(row, dict)]
        table = [row for row in rows if "项目" not in row]
        summary = [row for row in rows if "项目" in row]
        return table, summary
    return [], []


def _gate_summary_rows_are_detail(rows):
    """汇总形状的「明细非空」口径：至少 1 行，每行「项目」与「值」都非空（Spec §2.1）。"""
    if not rows:
        return False
    for row in rows:
        if not str(row.get("项目") or "").strip():
            return False
        if not str(row.get("值") or "").strip():
            return False
    return True


def _gate_step5_currency(data):
    """第 5 步的币种只有一个界面来源：**同一步**「报价基本信息」（`s5_basic`）（Spec §2.1）。

    报价明细表的列定义里没有「币种」（DA 本体全行业共用），所以行里没有币种时按这里兜底；
    兜底只读同一步（不跨步、不默认成「人民币」、不把空白当有值）。
    """
    if not isinstance(data, dict):
        return ""
    section = data.get("s5_basic")
    if not isinstance(section, dict):
        return ""
    form = section.get("数据", section.get("data"))
    if isinstance(form, dict):
        got = str(form.get("币种") or "").strip()
        if got:
            return got
    fields = section.get("fields")
    if isinstance(fields, list):
        for item in fields:
            if not isinstance(item, dict):
                continue
            if "币种" in str(item.get("label") or item.get("key") or ""):
                got = str(item.get("value") or "").strip()
                if got:
                    return got
    return ""


def _row_number(row, *keywords):
    """按**列名关键字**取这一行里第一个能解析成数的值（列名是业务中文名，不写死具体列）。"""
    for key, value in (row or {}).items():
        if not any(word in str(key) for word in keywords):
            continue
        got = _gate_num(value)
        if got is not None:
            return got
    return None


def _gate_blocker(code, message, action="", *, fixable_by_fill=True):
    return {"code": code, "message": message, "action": action,
            "fixable_by_fill": bool(fixable_by_fill)}


def _packaging_cost_evidence_problem(package: dict) -> bool:
    """正数局部费用不能掩盖材料为零或暂定成本。"""
    cost = package.get("cost") if isinstance(package.get("cost"), dict) else {}
    readiness = cost.get("readiness") if isinstance(cost.get("readiness"), dict) else {}
    return ((_gate_num(cost.get("material_total")) or 0) <= 0
            or readiness.get("verdict") == "provisional")


def quote_step_completion_gate(step_no, data, *, quote_fingerprint: str = "",
                               detail_fingerprint: str = "", business_case_id: str = "") -> dict:
    """第 3–6 步的完成门禁（Spec §4）：返回 ``{ok, step_no, code, message, action, blocking}``。

    判定只看**已落地的数据**（派上来的步骤快照），不做任何推断、不补默认值：
    「强行填满」与前端提示都不能代替这条（§4 最后一句）。
    """
    step = int(step_no or 0)
    blockers = []
    # 包装工艺回传不产生通用产品行；它的事实源是第 2 步交接包和确定性定价结果。
    # 不让包装落进下方电池/通用产品行门禁，也不让缺口草稿靠一张汇总表通过第 6 步。
    package = data.get("packaging_package") if isinstance(data, dict) else None
    packaging_quote = data.get("packaging_quote") if isinstance(data, dict) else None
    if isinstance(package, dict) and package.get("industry") == "packaging" and 3 <= step <= 6:
        cost = package.get("cost") if isinstance(package.get("cost"), dict) else {}
        valid_quote = (isinstance(packaging_quote, dict)
                       and packaging_quote.get("industry") == "packaging"
                       and (_gate_num(packaging_quote.get("net_unit_price")) or 0) > 0
                       and (_gate_num(packaging_quote.get("quote_quantity")) or 0) > 0)
        if not valid_quote:
            blockers.append(_gate_blocker(
                "packaging_quote_missing", "包装定价结果尚未生成，不能完成本步。",
                "在包装定价面板输入明确的毛利率并重算，核对来源与缺口后再确认。",
                fixable_by_fill=False))
        elif step == 6 and (packaging_quote.get("draft") or packaging_quote.get("publish_blocked")
                            or packaging_quote.get("gap_count")
                            or cost.get("has_gaps") or package.get("gaps")):
            blockers.append(_gate_blocker(
                "packaging_draft_not_publishable",
                "包装成本仍有缺口，当前仅是内部草稿，不能完成正式报价单。",
                "回技术工艺补齐并确认成本证据，重新回传和定价。",
                fixable_by_fill=False))
        elif step == 6 and _packaging_cost_evidence_problem(package):
            blockers.append(_gate_blocker(
                "packaging_cost_evidence_missing",
                "包装材料成本为零或成本仍是暂定状态，不能生成正式报价单。",
                "回技术工艺核对材料单价、用量及成本确认状态。",
                fixable_by_fill=False))
        head = blockers[0] if blockers else {}
        return {"ok": not blockers, "step_no": step,
                "code": head.get("code", ""),
                "message": head.get("message", "第 %d 步可以完成。" % step),
                "action": head.get("action", ""),
                "business_case_id": str(business_case_id or ""), "blocking": blockers,
                "fixable_by_fill": bool(blockers) and all(b.get("fixable_by_fill") for b in blockers)}
    if step == 3:
        rows = _gate_rows(data, "s3_products", "s2_products", "s1_products")
        if not rows:
            blockers.append(_gate_blocker(
                "no_product_rows", "本单还没有产品行（第 1 步未完成产品匹配）。",
                "回第 1 步完成需求配置与产品匹配；产品行由系统匹配产生，不能靠填表造出来。",
                fixable_by_fill=False))
        elif not any((_row_number(row, "基础成本", "成本", "价格") or 0) > 0 for row in rows):
            blockers.append(_gate_blocker(
                "no_base_cost", "产品行没有正数基础成本，无法确认第 3 步。",
                "点「强行填满本步骤」或「重算利润加成」，让基础成本落地后再确认。"))
    elif step == 4:
        rows = _gate_rows(data, "s4_products", "s3_products", "s2_products", "s1_products")
        if not rows:
            blockers.append(_gate_blocker(
                "no_product_rows", "本单还没有产品行，无法确认第 4 步。",
                "回第 1 步完成产品匹配。", fixable_by_fill=False))
        else:
            explainable = []
            for row in rows:
                price = _row_number(row, "价格")
                base = _row_number(row, "基础成本", "成本")
                has_evidence = any(("加价" in str(k) or "规则" in str(k)) and str(v or "").strip()
                                   for k, v in (row or {}).items())
                if price is not None and price > 0 and (base is not None or has_evidence):
                    explainable.append(row)
            if not explainable:
                blockers.append(_gate_blocker(
                    "no_markup_evidence", "产品行没有可解释的加价结果（价格或加价依据为空）。",
                    "点「强行填满本步骤」按定价规则重算加价，或人工填写加价依据。"))
    elif step == 5:
        table_rows, summary_rows = _gate_detail_rows(data, "s5_detail")
        if table_rows:
            # 币种兜底只认**同一步**「报价基本信息」（明细表的列里根本没有「币种」）。
            basic_currency = _gate_step5_currency(data)
            for index, row in enumerate(table_rows, start=1):
                tag = "第 %d 行" % index
                qty = _row_number(row, "数量")
                unit = _row_number(row, "报价", "单价")
                after = _row_number(row, "折后价格", "折后价")
                total = _row_number(row, "总金额", "金额")
                currency = str(row.get("币种") or "").strip() or basic_currency
                if qty is None or qty <= 0:
                    blockers.append(_gate_blocker("invalid_quantity", tag + "数量无效。",
                                                  "填写正数数量。"))
                if unit is None or unit <= 0:
                    blockers.append(_gate_blocker("invalid_unit_price", tag + "单价（报价）无效。",
                                                  "填写正数单价。"))
                if not currency:
                    blockers.append(_gate_blocker("invalid_currency", tag + "币种为空。",
                                                  "填写币种（如 人民币）。"))
                if after is None or total is None:
                    blockers.append(_gate_blocker("total_not_recomputable",
                                                  tag + "总金额/折后价格缺失，无法复算。",
                                                  "点「报价方案」重算明细，让总金额按公式生成。"))
                elif abs(total - after * (qty if qty is not None else 1.0)) > 0.01:
                    blockers.append(_gate_blocker(
                        "total_not_recomputable",
                        tag + "总金额 %.4f ≠ 折后价格 %.4f × 数量 %s。" % (total, after, qty),
                        "点「报价方案」重算明细。"))
        elif not _gate_summary_rows_are_detail(summary_rows):
            # 两种形状都取不到可用行才算「没有明细」；第 5 步自己有入口（「报价方案」就是
            # 把明细生出来的那个动作），所以不许扣 fixable_by_fill:false 把按钮当场拒掉。
            blockers.append(_gate_blocker(
                "no_detail_rows", "报价明细为空：至少要有 1 行。",
                "点「报价方案」重算明细（或点「强行填满本步骤」），明细由定价引擎按第 4 步的"
                "产品行生成；也可以点「重算明细」再试。"))
    elif step == 6:
        table_rows, summary_rows = _gate_detail_rows(data, "s5_detail")
        has_detail = bool(table_rows) or _gate_summary_rows_are_detail(summary_rows)
        if not has_detail:
            blockers.append(_gate_blocker("no_detail_rows", "报价明细为空，不能生成报价单。",
                                          "回到第 5 步补全报价明细。"))
        if has_detail and quote_fingerprint and detail_fingerprint and quote_fingerprint != detail_fingerprint:
            blockers.append(_gate_blocker(
                "detail_changed_after_step5_confirm",
                "报价明细在第 5 步确认之后被改动过，不能照旧生成报价单。",
                "回第 5 步核对明细后重新确认第 5 步，再生成报价单。",
                fixable_by_fill=False))
    head = blockers[0] if blockers else {}
    return {"ok": not blockers,
            "step_no": step,
            "code": "" if not blockers else head.get("code", ""),
            "message": ("第 %d 步可以完成。" % step) if not blockers
                       else head.get("message", "这一步还不能完成。"),
            "action": "" if not blockers else head.get("action", ""),
            "business_case_id": str(business_case_id or ""),
            "blocking": blockers,
            "fixable_by_fill": bool(blockers) and all(b.get("fixable_by_fill") for b in blockers)}
