# -*- coding: utf-8 -*-
"""
离线校验包装成本规则快照 `agent_knowledge/rules/packaging_cost_rules.json`。

为什么需要它：包装成本的公式目前同时存在于四处 —— 运行时目录
（`services/packaging_cost.py` 的 `FORMULA_CATALOG`）、随代码发布的规则快照
（`agent_knowledge/rules/packaging_cost_rules.json`）、知识库表
（`kb_packaging_cost_formula`）与红测里的黄金常量。**抄错但自洽**无法靠代码评审根除，
所以这一版把「工作簿缓存值 / 快照 expected_result / 运行时复算」三方对账做成可执行工具。

只在开发与规则升级时运行；生产运行时不调用它（`tech_app/backend/**` 也不得 import 本文件
或 openpyxl）。判定与 IO 分离：`audit_rules()` 是纯函数（不读文件、不写文件、不连库），
红测直接调用它。

用法（需要装了 openpyxl 的解释器）：
    python tools/extract_packaging_rules.py --workbook ../../裕同包装项目-待开发/报价逻辑-0903.xlsx
    python tools/extract_packaging_rules.py --check      # 默认，只校验，绝不写文件
    python tools/extract_packaging_rules.py --write      # 目标 JSON 是 reviewed 时必须加 --force

退出码：0 通过 / 1 口径不一致 / 2 来源或引用损坏 / 3 工作簿 sha256 变了 / 4 拒绝覆盖 reviewed 快照。

修复第 3 / 4 批追加的判定（都保持纯函数）：

· **单来源/最低收费**（有 `minimum_charge_policy` 块，或条目里出现 `variable_map` /
  `source_formula` / `minimum_charge_source_ref` 时启用）：`minimum_charge_policy_missing`、
  `minimum_charge_policy_unknown`、`minimum_charge_policy_golden_mismatch`、
  `mixed_source_formula`、`minimum_charge_source_missing`、`minimum_charge_not_in_source`、
  `source_cell_mismatch`（逐字等价，判定实现与运行时**同一份**）、`unmapped_variable`、
  `unparsable_formula`（`expression` 或来源原文**至少一侧解析不出规范串** —— 读不懂，
  不是改写；Spec `packaging-rules-audit-unparsable-formula.md` §2.2）、`hidden_sheet_source`；
· **逐列证据**（有 `categories` / `column_evidence` 时启用）：`category_evidence_missing`、
  `evidence_kind_unknown`、`evidence_cell_has_no_formula`、`formula_without_evidence`、
  `invented_formula_for_blank_column`、`column_evidence_incomplete`。
· 两条口径都**按申报启用**：没申报的旧快照保持原结论，不被新检查误伤。
· 健康扫描（`broken_reference` / `cached_without_formula`）只覆盖**快照声明来源的 Sheet**；
  工作簿里与规则无关的汇总表（如 `报价表`）自带的 `#REF!` 记进 `ignored_sheets` 信息，
  不影响 `ok` —— 本工具判的是「规则与其来源是否一致」，不是替客户工作簿做体检。
· 条目显式声明 `verbatim: false`（如按 Spec §2.6.2 改写的 `PKG-C-LABOR`）时，跳过逐字与缓存
  对账，改记 `declared_deviations` —— 申报过的改写不算抄错，但必须留下痕迹。
· 缓存值按该条声明的 `rounding` 取整后比较（工作簿显示的是这一格的取整值）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent                       # tech_app/
CPQ_DIR = ROOT_DIR.parent                         # 仓库根
if str(CPQ_DIR) not in sys.path:
    sys.path.insert(0, str(CPQ_DIR))

RULES_PATH = ROOT_DIR / "agent_knowledge" / "rules" / "packaging_cost_rules.json"
DEFAULT_WORKBOOK = CPQ_DIR / "裕同包装项目-待开发" / "报价逻辑-0903.xlsx"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_BROKEN = 2
EXIT_SOURCE_CHANGED = 3
EXIT_WRITE_REFUSED = 4

#: 公式来源单元格的地址形状（如 `V2` / `AH13`）。
CELL_PATTERN = re.compile(r"^[A-Z]{1,3}[0-9]{1,5}$")
#: 值/公式里的坏引用。
BROKEN_MARK = "#REF!"
#: 复算容差（Spec §2.2：引擎复算 1e-6、缓存值 1e-9）。
RECOMPUTE_TOLERANCE = 1e-6
CACHED_TOLERANCE = 1e-9
#: 缓存值对账容差（修复第 3 批 §4.1：黄金值与工作簿缓存值逐条一致）。
GOLDEN_TOLERANCE = 1e-6

#: 工作簿里**永不允许作为来源**的三个隐藏 Sheet（Spec 修复第 3/4 批）。
HIDDEN_SHEET_NAMES = ("大货最终定价", "大货价核算1", "首批试产毛利核算")
#: 最低收费口径候选闭集（Spec 修复第 3 批 §4.1）。
POLICY_IDS = ("sheet_industry_standard", "sheet_labor_rate", "declared_hybrid")
#: 逐列证据类型闭集（Spec 修复第 4 批 §2.1）。
EVIDENCE_KINDS = ("formula", "hand_filled", "no_formula_in_workbook")


def _ref_parts(ref) -> tuple:
    """`报价逻辑-0903.xlsx/<sheet>/<cell>` → `(<sheet>, <cell>)`；解析不了返回 `("", "")`。"""
    text = str(ref or "").strip()
    if "/" not in text:
        return "", ""
    parts = text.split("/")
    if len(parts) < 3:
        return "", ""
    return parts[-2].strip(), parts[-1].strip()


def _number_text(value) -> str:
    """门限数字在原文里的写法（整数去掉 `.0`）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number == int(number):
        return str(int(number))
    return ("%.12g" % number)


def _cell_formula(cells, sheet: str, cell: str):
    """取某格的公式原文（可见 Sheet 才算来源）。"""
    payload = (cells or {}).get(sheet)
    if not isinstance(payload, dict) or str(payload.get("state")) == "hidden":
        return None
    return (payload.get("formulas") or {}).get(cell)


def _is_hidden(sheet: str, cells) -> bool:
    if sheet in HIDDEN_SHEET_NAMES:
        return True
    payload = (cells or {}).get(sheet)
    return bool(isinstance(payload, dict) and str(payload.get("state")) == "hidden")


def _cached_matches(cached, expected, item) -> bool:
    """缓存值对账（Spec §2.2；修复第 3 批 e1 起按该条声明的 `rounding` 对齐）。

    工作簿单元格显示的是**该条 `rounding` 位**的值（如 AN2 显示 0.4605、精确值
    0.46050199999999997），所以先按 `rounding` 取整再比；没有 `rounding` 时退回 1e-9。
    """
    try:
        rounding = int(item.get("rounding"))
    except (TypeError, ValueError):
        rounding = None
    if rounding is None or not 0 <= rounding <= 9:
        return abs(float(cached) - float(expected)) <= CACHED_TOLERANCE
    return round(float(cached), rounding) == round(float(expected), rounding)


def _category_of_code(code, rules) -> str:
    """`formula_code` → `cost_category`（先查运行时目录，再退回快照条目）。"""
    try:
        from tech_app.backend.services import packaging_cost

        entry = packaging_cost.FORMULA_CATALOG.get(str(code))
        if entry:
            return str(entry.get("cost_category") or "")
    except Exception:  # noqa: BLE001 - 运行时目录不可用时退回快照
        pass
    for item in (rules or {}).get("formulas") or []:
        if str((item or {}).get("formula_code") or "") == str(code):
            return str((item or {}).get("cost_category") or "")
    return ""


def _runtime_catalog_codes_or_empty() -> set:
    """运行时目录的 code 集合；不可用时返回空集合（判定退回快照自身）。"""
    try:
        return runtime_catalog_codes()
    except Exception:  # noqa: BLE001
        return set()


def _runtime_categories() -> dict:
    """运行时类别 → `(label, level)`（Spec 修复第 4 批 §3 的注入默认值）。"""
    from tech_app.backend.services import packaging_cost

    out = {code: (label, "part") for code, label in packaging_cost.COST_CATEGORIES}
    out.update({code: (label, "project")
                for code, label in packaging_cost.PROJECT_COST_CATEGORIES})
    return out


def runtime_catalog_codes() -> set:
    """运行时目录里的全部 `formula_code`。"""
    from tech_app.backend.services import packaging_cost

    return set(packaging_cost.FORMULA_CATALOG)


def recompute(item: dict) -> float:
    """用运行时引擎按 `verify_inputs` 复算该条公式（不读库、不联网）。"""
    from tech_app.backend.services import packaging_cost

    line = packaging_cost.compute_line(item.get("cost_category"),
                                       dict(item.get("verify_inputs") or {}),
                                       formula_code=item.get("formula_code"), rows=[])
    return line.get("amount")


def load_workbook_cells(path) -> dict:
    """只读工作簿，返回 `{sheet: {"state", "formulas", "cached"}}`（Spec §4.1）。

    · 只登记**公式单元格**及其缓存结果：工作簿里的字面量（尺寸、单价…）不是"计算结果"，
      把 `cached_without_formula` 规则套到它们头上会把整张表都判成问题；
    · `state` 为 `visible` / `hidden`；
    · 不写工作簿。
    """
    import openpyxl

    book_formula = openpyxl.load_workbook(str(path), data_only=False)
    book_value = openpyxl.load_workbook(str(path), data_only=True)
    try:
        out: dict = {}
        for name in book_formula.sheetnames:
            sheet_formula = book_formula[name]
            sheet_value = book_value[name]
            formulas: dict = {}
            cached: dict = {}
            for row in sheet_formula.iter_rows():
                for cell in row:
                    value = cell.value
                    if isinstance(value, str) and value.startswith("="):
                        formulas[cell.coordinate] = value
                        cached_value = sheet_value[cell.coordinate].value
                        if cached_value is not None:
                            cached[cell.coordinate] = cached_value
            out[name] = {"state": "hidden" if sheet_formula.sheet_state != "visible"
                         else "visible",
                         "formulas": formulas, "cached": cached}
        return out
    finally:
        book_formula.close()
        book_value.close()


def _problem(problems: list, code: str, where: str, detail: str) -> None:
    problems.append({"code": code, "where": where, "detail": detail})


def _worst(problems: list) -> int:
    """多个问题时退出码取最大值（Spec §4.2：3 > 2 > 1 > 0）。"""
    if not problems:
        return EXIT_OK
    return max(int(item.get("exit_code") or 0) for item in problems)


def verbatim_compare(expression, source_formula, variable_map, source_cell,
                     literals=None) -> dict:
    """逐字等价判定（Spec §5.3）：与运行时**同一份实现**，避免两套判定漂移。"""
    from tech_app.backend.services.packaging_cost import verbatim_compare as _impl

    return _impl(expression, source_formula, variable_map, source_cell, literals)


def verbatim_equivalent(expression, source_formula, variable_map, source_cell,
                        literals=None) -> bool:
    """`expression` 是否与 `source_cell` 原文逐字等价（Spec §5.3 的 bool 形态）。

    工具与运行时都提供这个判定（Spec §5.3：「工具与运行时都提供同一个」），工具侧一律
    转调运行时的同一份实现，不另写一套。
    """
    return bool(verbatim_compare(expression, source_formula, variable_map, source_cell,
                                 literals).get("equivalent"))


def _declares_single_source(rules) -> bool:
    """快照是否申报了「表达式/门限单一来源」契约（修复第 3 批）。

    未申报该契约的旧快照（没有 `minimum_charge_policy`、条目也没有 `variable_map` /
    `source_formula` / `minimum_charge_source_ref`）不走第 3 批的检查 —— 否则每一条旧
    条目都会被判成 `unmapped_variable`。
    """
    if "minimum_charge_policy" in (rules or {}):
        return True
    for item in (rules or {}).get("formulas") or []:
        if not isinstance(item, dict):
            continue
        if ("variable_map" in item or "source_formula" in item
                or "minimum_charge_source_ref" in item):
            return True
    return False


def _declares_evidence(rules) -> bool:
    """快照是否申报了逐列证据（修复第 4 批）：没申报就不查（旧快照保持原结论）。"""
    return ("categories" in (rules or {})) or ("column_evidence" in (rules or {}))


def _audit_evidence(cells, rules, problems: list, add, runtime_categories=None,
                   catalog_codes=None) -> None:
    """逐列证据对账（Spec 修复第 4 批 §3）：纯函数，只看入参。"""
    declared = {str(item.get("cost_category") or ""): item
                for item in (rules or {}).get("categories") or [] if isinstance(item, dict)}
    expect = (dict(runtime_categories) if runtime_categories is not None
              else _runtime_categories())
    if set(declared) != set(expect):
        add("category_evidence_missing", "categories",
            "categories 与运行时类别不一致：缺少 %s；多出 %s"
            % (sorted(set(expect) - set(declared)), sorted(set(declared) - set(expect))),
            EXIT_MISMATCH)
    codes = (set(catalog_codes) if catalog_codes is not None
             else {str(code) for code in _runtime_catalog_codes_or_empty()})
    catalog_categories = {_category_of_code(code, rules) for code in codes}
    catalog_categories.discard("")

    evidence = {str(item.get("source_column") or ""): item
                for item in (rules or {}).get("column_evidence") or []
                if isinstance(item, dict)}
    source_rows = (rules or {}).get("source_rows")

    for code, item in declared.items():
        kind = str(item.get("evidence_kind") or "")
        if kind not in EVIDENCE_KINDS:
            add("evidence_kind_unknown", "categories/%s" % code,
                "evidence_kind=%r 不在闭集 %s 里" % (kind, list(EVIDENCE_KINDS)),
                EXIT_MISMATCH)
            continue
        if kind == "formula":
            sheet_name = str(item.get("source_sheet") or "")
            cell = str(item.get("source_cell") or "")
            if _is_hidden(sheet_name, cells):
                add("hidden_sheet_source", "categories/%s" % code,
                    "证据来源指向隐藏 Sheet %s" % sheet_name, EXIT_MISMATCH)
            elif not _cell_formula(cells, sheet_name, cell):
                add("evidence_cell_has_no_formula", "categories/%s" % code,
                    "%s!%s 没有公式（证据不成立）" % (sheet_name, cell), EXIT_BROKEN)
        if kind == "no_formula_in_workbook":
            if item.get("formula_code"):
                add("invented_formula_for_blank_column", "categories/%s" % code,
                    "无公式列不许挂 formula_code=%r" % item.get("formula_code"),
                    EXIT_MISMATCH)
            if code in catalog_categories:
                add("invented_formula_for_blank_column", "categories/%s" % code,
                    "无公式列不许出现在 FORMULA_CATALOG 里", EXIT_MISMATCH)
            column = str(item.get("source_column") or "")
            row = evidence.get(column)
            if isinstance(row, dict) and int(row.get("formula_rows") or 0) > 0:
                add("invented_formula_for_blank_column", "column_evidence/%s" % column,
                    "该列登记了 %s 条公式，与 no_formula_in_workbook 矛盾"
                    % row.get("formula_rows"), EXIT_MISMATCH)

    for code in catalog_categories:
        item = declared.get(code)
        if item is None or str(item.get("evidence_kind") or "") != "formula":
            add("formula_without_evidence", "categories/%s" % code,
                "FORMULA_CATALOG 里有该类别，但 categories 里没有 formula 证据",
                EXIT_MISMATCH)

    expected_columns = [str(item.get("source_column") or "") for item in declared.values()
                        if str(item.get("level") or "") == "part"]
    missing = [column for column in expected_columns if column not in evidence]
    extra = [column for column in evidence if column not in expected_columns]
    if missing or extra:
        add("column_evidence_incomplete", "column_evidence",
            "未覆盖 %d 个部件级列（缺 %s；多 %s）" % (len(expected_columns), missing, extra),
            EXIT_MISMATCH)
    for column, item in evidence.items():
        try:
            total = (int(item.get("formula_rows") or 0) + int(item.get("blank_rows") or 0)
                     + int(item.get("hand_filled_rows") or 0))
        except (TypeError, ValueError):
            total = -1
        if source_rows is None or total != int(source_rows):
            add("column_evidence_incomplete", "column_evidence/%s" % column,
                "三项行数之和 %r != source_rows %r" % (total, source_rows), EXIT_MISMATCH)


def audit_rules(cells, rules, *, workbook_sha256, workbook_name="", catalog_codes=None,
                runtime_categories=None) -> dict:
    """纯函数对账（Spec §4.2）。不读文件、不写文件、不连库。

    `catalog_codes` / `runtime_categories` 默认取运行时定义，红测可注入小集合。
    """
    problems: list = []
    mismatches: list = []
    hidden_with_formula: list = []
    ignored_sheets: list = []
    declared_deviations: list = []

    def add(code: str, where: str, detail: str, exit_code: int) -> None:
        problems.append({"code": code, "where": where, "detail": detail,
                         "exit_code": exit_code})

    #: 健康扫描（`broken_reference` / `cached_without_formula`）只覆盖**快照声明的来源
    #: Sheet**（`source_sheets` + 各条 `source_sheet` + 逐列证据的 `source_sheet`）：
    #: 这两条规则判的是「规则抄自的那张表是否可信」，工作簿里与规则无关的汇总表
    #: （如 `报价表`）自带的 `#REF!` 只记进 `ignored_sheets`，不影响结论。
    scope_sheets = {str(name) for name in (rules or {}).get("source_sheets") or []}
    for item in (rules or {}).get("formulas") or []:
        scope_sheets.add(str((item or {}).get("source_sheet") or ""))
    for item in (rules or {}).get("categories") or []:
        scope_sheets.add(str((item or {}).get("source_sheet") or ""))
    scope_sheets.discard("")

    # 1) 来源指纹 --------------------------------------------------------- #
    declared_sha = str((rules or {}).get("source_sha256") or "")
    if workbook_sha256 and str(workbook_sha256) != declared_sha:
        add("source_sha256_mismatch", workbook_name or declared_sha,
            "工作簿 sha256 与快照声明不同（来源已变，需人工确认新 rule_set）",
            EXIT_SOURCE_CHANGED)

    # 2) 单元格级健康度 --------------------------------------------------- #
    for sheet_name, payload in sorted((cells or {}).items()):
        state = str((payload or {}).get("state") or "visible")
        formulas = dict((payload or {}).get("formulas") or {})
        cached = dict((payload or {}).get("cached") or {})
        if state == "hidden":
            if formulas:
                hidden_with_formula.append(sheet_name)
            continue
        if scope_sheets and sheet_name not in scope_sheets:
            broken = [cell for cell, text in formulas.items() if BROKEN_MARK in str(text)]
            broken += [cell for cell, value in cached.items()
                       if isinstance(value, str) and BROKEN_MARK in value]
            if broken:
                ignored_sheets.append(sheet_name)
            continue
        for cell, text in sorted(formulas.items()):
            if BROKEN_MARK in str(text):
                add("broken_reference", "%s!%s" % (sheet_name, cell), str(text)[:120],
                    EXIT_BROKEN)
        for cell, value in sorted(cached.items()):
            if isinstance(value, str) and BROKEN_MARK in value:
                add("broken_reference", "%s!%s" % (sheet_name, cell), str(value)[:120],
                    EXIT_BROKEN)
            if cell not in formulas:
                add("cached_without_formula", "%s!%s" % (sheet_name, cell),
                    "只有缓存值没有公式", EXIT_BROKEN)

    # 3) 公式集合 --------------------------------------------------------- #
    codes = {str(item.get("formula_code") or "") for item in (rules or {}).get("formulas") or []}
    expected_codes = (set(catalog_codes) if catalog_codes is not None
                      else runtime_catalog_codes())
    if codes != expected_codes:
        add("formula_set_mismatch", "formulas",
            "快照与运行时目录的 formula_code 不一致：缺少 %s；多出 %s"
            % (sorted(expected_codes - codes), sorted(codes - expected_codes)),
            EXIT_MISMATCH)

    # 4) 逐条公式 --------------------------------------------------------- #
    single_source = _declares_single_source(rules)
    for item in (rules or {}).get("formulas") or []:
        code = str(item.get("formula_code") or "")
        sheet_name = str(item.get("source_sheet") or "")
        cell = str(item.get("source_cell") or "")
        where = "%s(%s!%s)" % (code, sheet_name, cell)
        payload = (cells or {}).get(sheet_name)
        if payload is None:
            add("source_sheet_missing", where, "快照指向的 Sheet 不存在", EXIT_MISMATCH)
            continue
        if str(payload.get("state")) == "hidden":
            add("source_sheet_hidden", where, "快照指向的 Sheet 是隐藏 Sheet", EXIT_MISMATCH)
            continue
        formulas = payload.get("formulas") or {}
        cached = payload.get("cached") or {}
        _declared = item.get("verbatim") is False
        if cell not in formulas:
            add("source_cell_has_no_formula", where, "该单元格没有公式", EXIT_BROKEN)
            continue
        expected = item.get("expected_result")
        cached_value = cached.get(cell)
        if _declared:
            # 已申报的改写（如 PKG-C-LABOR 按 Spec §2.6.2 改写）：不与单元格逐字等价，
            # 缓存值也不该拿来对账 —— 记录成 declared_deviation，不计入问题。
            declared_deviations.append({"formula_code": code, "where": where,
                                        "detail": str(item.get("deviation")
                                                      or item.get("note") or "")})
        elif cached_value is None or not isinstance(cached_value, (int, float)):
            add("cached_value_mismatch", where, "该单元格没有可对账的缓存值",
                EXIT_MISMATCH)
            mismatches.append({"formula_code": code, "where": where,
                               "kind": "cached_value_missing"})
        elif not _cached_matches(cached_value, expected, item):
            add("cached_value_mismatch", where,
                "缓存值 %r != expected_result %r（容差 1e-9）" % (cached_value, expected),
                EXIT_MISMATCH)
            mismatches.append({"formula_code": code, "where": where,
                               "kind": "cached_value_mismatch",
                               "cached": cached_value, "expected": expected})
        got = recompute(item)
        if got is None or abs(float(got) - float(expected)) > RECOMPUTE_TOLERANCE:
            add("recompute_mismatch", where,
                "引擎复算 %r != expected_result %r（容差 1e-6）" % (got, expected),
                EXIT_MISMATCH)
            mismatches.append({"formula_code": code, "where": where,
                               "kind": "recompute_mismatch",
                               "recomputed": got, "expected": expected})

        # 逐字等价（Spec 修复第 3 批 §5.3）：表达式必须与 source_cell 原文等价。
        if single_source and not _declared:
            declared_source = str(item.get("source_formula") or "")
            workbook_source = _cell_formula(cells, sheet_name, cell) or ""
            literals = {key: value for key, value in (item.get("verify_inputs") or {}).items()
                        if key not in (item.get("variable_map") or {})}
            compare = verbatim_compare(str(item.get("expression") or ""), declared_source,
                                       item.get("variable_map") or {}, cell, literals)
            if compare.get("unmapped"):
                add("unmapped_variable", where,
                    "表达式变量 %s 既不在 variable_map 也不在 literals 里"
                    % "、".join(compare["unmapped"]), EXIT_MISMATCH)
            else:
                # 读不懂 ≠ 改写（Spec `packaging-rules-audit-unparsable-formula.md` §2.2）。
                # 来源那一侧的原文有两处：快照申报的 `source_formula` 与**工作簿该格**——
                # 任一处解析不出规范串，这一条就是「我没能校验」，不是「你抄错了」。
                sides = [str(side) for side in (compare.get("unparsable") or [])]
                if workbook_source:
                    cell_check = verbatim_compare(str(item.get("expression") or ""),
                                                  workbook_source,
                                                  item.get("variable_map") or {}, cell, literals)
                    if "source" in (cell_check.get("unparsable") or []) and "source" not in sides:
                        sides.append("source")
                if sides:
                    add("unparsable_formula", where,
                        "表达式与 %s 原文至少一侧解析不出规范串（读不懂，不是改写）：%s"
                        % (cell, "+".join(sides)), EXIT_MISMATCH)
                elif not compare.get("equivalent"):
                    target = workbook_source or declared_source
                    if target:
                        add("source_cell_mismatch", where,
                            "expression 与 %s 原文不逐字等价（改写不等于等价）" % cell,
                            EXIT_BROKEN)

    # 5) 最低收费口径申报（Spec 修复第 3 批 §7） --------------------------- #
    if single_source:
        block = (rules or {}).get("minimum_charge_policy")
        chosen_policy = ""
        hybrid = False
        if not isinstance(block, dict):
            add("minimum_charge_policy_missing", "minimum_charge_policy",
                "快照没有最低收费口径申报块（不许静默按某套口径出货）", EXIT_MISMATCH)
        else:
            candidates = [item for item in (block.get("candidates") or [])
                          if isinstance(item, dict)]
            ids = [str(item.get("policy_id") or "") for item in candidates]
            status = str(block.get("status") or "")
            chosen_policy = str(block.get("chosen") or "")
            decided_by = str(block.get("decided_by") or "")
            hybrid = (status == "chosen" and chosen_policy == "declared_hybrid")
            invalid = (status not in ("pending", "chosen")
                       or len(candidates) != len(POLICY_IDS)
                       or set(ids) != set(POLICY_IDS)
                       or (status == "chosen"
                           and (chosen_policy not in POLICY_IDS or not decided_by))
                       or (status == "pending" and (chosen_policy or decided_by)))
            if invalid:
                add("minimum_charge_policy_unknown", "minimum_charge_policy",
                    "status=%r、chosen=%r、候选 %d 条 —— 申报块本身不合法"
                    % (status, chosen_policy, len(candidates)), EXIT_MISMATCH)
            for item in candidates:
                if item.get("is_declared_hybrid"):
                    continue
                sheet_name = str(item.get("authoritative_sheet") or "")
                for name, row in (item.get("golden") or {}).items():
                    if not isinstance(row, dict):
                        continue
                    cell = str(row.get("cell") or "")
                    cached_value = ((cells or {}).get(sheet_name) or {}).get(
                        "cached", {}).get(cell)
                    if not isinstance(cached_value, (int, float)):
                        continue
                    try:
                        delta = abs(float(cached_value) - float(row.get("unit_amount") or 0))
                    except (TypeError, ValueError):
                        delta = 1.0
                    if delta > GOLDEN_TOLERANCE:
                        add("minimum_charge_policy_golden_mismatch",
                            "%s/%s!%s" % (item.get("policy_id"), sheet_name, cell),
                            "候选黄金值 %r != 工作簿缓存值 %r" % (row.get("unit_amount"),
                                                                cached_value), EXIT_BROKEN)
        for item in (rules or {}).get("formulas") or []:
            code = str((item or {}).get("formula_code") or "")
            minimum = item.get("minimum_charge")
            ref = str(item.get("minimum_charge_source_ref") or "")
            source_ref = str(item.get("source_ref") or "")
            where = "%s(%s)" % (code, ref or source_ref or "-")
            try:
                threshold = float(minimum or 0)
            except (TypeError, ValueError):
                threshold = 0.0
            if threshold > 0:
                if not ref:
                    add("minimum_charge_source_missing", where,
                        "minimum_charge=%r 必须声明 minimum_charge_source_ref" % minimum,
                        EXIT_MISMATCH)
                else:
                    ref_sheet, ref_cell = _ref_parts(ref)
                    source_sheet = _ref_parts(source_ref)[0]
                    if _is_hidden(ref_sheet, cells):
                        add("hidden_sheet_source", where,
                            "门限来源指向隐藏 Sheet %s" % ref_sheet, EXIT_MISMATCH)
                    text = _cell_formula(cells, ref_sheet, ref_cell)
                    if text is None or _number_text(threshold) not in str(text):
                        add("minimum_charge_not_in_source", where,
                            "门限 %s 在 %s!%s 原文里找不到" % (_number_text(threshold),
                                                          ref_sheet, ref_cell),
                            EXIT_MISMATCH)
                    if source_sheet and ref_sheet and source_sheet != ref_sheet and not hybrid:
                        add("mixed_source_formula", where,
                            "门限来自 %s、表达式来自 %s，且未申报 declared_hybrid"
                            % (ref_sheet, source_sheet), EXIT_MISMATCH)
            for field in ("source_sheet",):
                if item.get(field) and _is_hidden(str(item[field]), cells):
                    add("hidden_sheet_source", where, "来源指向隐藏 Sheet", EXIT_MISMATCH)

    # 6) 逐列证据登记（Spec 修复第 4 批 §3） ------------------------------- #
    if _declares_evidence(rules):
        _audit_evidence(cells, rules, problems, add, runtime_categories, catalog_codes)

    exit_code = _worst(problems)
    return {"ok": exit_code == EXIT_OK, "exit_code": exit_code,
            "problems": problems, "mismatches": mismatches,
            "skipped_hidden_sheets": hidden_with_formula,
            "ignored_sheets": sorted(set(ignored_sheets)),
            "declared_deviations": declared_deviations}


def _load_rules(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _print_report(report: dict, workbook: Path, rules_path: Path) -> None:
    print("工作簿：%s" % workbook)
    print("快照  ：%s" % rules_path)
    if report.get("skipped_hidden_sheets"):
        print("隐藏 Sheet（只记录，不影响结论）：%s"
              % "、".join(report["skipped_hidden_sheets"]))
    if report.get("ignored_sheets"):
        print("非来源 Sheet（工作簿自带坏引用，只记录不影响结论）：%s"
              % "、".join(report["ignored_sheets"]))
    for item in report.get("declared_deviations") or []:
        print("  [declared_deviation] %s：%s" % (item.get("where"), item.get("detail")))
    for item in report.get("problems") or []:
        print("  [%s] %s：%s" % (item.get("code"), item.get("where"), item.get("detail")))
    for item in report.get("mismatches") or []:
        print("  差异：%s" % json.dumps(item, ensure_ascii=False))
    if report.get("ok"):
        print("结论：通过（exit_code=0）")
    else:
        print("结论：不通过（exit_code=%d，共 %d 个问题）"
              % (report.get("exit_code"), len(report.get("problems") or [])))


def main(argv=None) -> int:
    """CLI：读文件 → `audit_rules` → 打印报告 → 返回退出码；`--write` 才写 JSON。"""
    parser = argparse.ArgumentParser(
        description="离线校验包装成本规则快照与 0903 工作簿是否一致（只读，除 --write）")
    parser.add_argument("--workbook", default=str(DEFAULT_WORKBOOK),
                        help="报价逻辑-0903.xlsx 路径")
    parser.add_argument("--rules", default=str(RULES_PATH), help="规则快照 JSON 路径")
    parser.add_argument("--check", action="store_true", help="只校验（默认行为，绝不写文件）")
    parser.add_argument("--write", action="store_true", help="校验通过后把 results 写回快照")
    parser.add_argument("--force", action="store_true", help="允许覆盖 reviewed 快照（人工决定）")
    args = parser.parse_args(argv)

    rules_path = Path(args.rules)
    workbook = Path(args.workbook)

    # 写保护必须在读工作簿之前：目标快照是 reviewed 就拒绝覆盖（Spec §4.2 write_refused）。
    if args.write:
        target = _load_rules(rules_path) if rules_path.exists() else {}
        if str(target.get("review_status") or "") == "reviewed" and not args.force:
            print("拒绝写入：%s 的 review_status='reviewed'；改来源需人工决定 + 新 rule_set"
                  "（如确需覆盖，显式加 --force）" % rules_path)
            return EXIT_WRITE_REFUSED

    if not workbook.exists():
        print("工作簿不存在：%s" % workbook)
        return EXIT_BROKEN
    rules = _load_rules(rules_path)
    cells = load_workbook_cells(workbook)
    report = audit_rules(cells, rules, workbook_sha256=_sha256(workbook),
                         workbook_name=workbook.name)
    _print_report(report, workbook, rules_path)

    if args.write and report["ok"]:
        # 只回写 formulas 的结果字段：`review_status` 与 `source_sha256` 逐字保留 ——
        # 换来源是人工决定，工具不许自己把 sha256 改成新值。
        out = dict(rules)
        out["formulas"] = rules.get("formulas")
        rules_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
        print("已写入 %s（review_status / source_sha256 未改动）" % rules_path)
    return int(report["exit_code"])


if __name__ == "__main__":                                    # pragma: no cover
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
