"""红测：轮廓未闭合的件必须有一条出路（入口 + 说得清下一步），现在它是死路。

Spec：`docs/specs/packaging-open-outline-part-needs-a-way-out.md`

现状缺口（9-22 在本机 HEAD `14afe4b` 上逐条核对，不是推断）：
  · `processability()`（`packaging_parts.py:1985`）的 `PACKAGING_PART_NOT_CLOSED` 分支只有
    「这一件没有可信的闭合轮廓（<原因码>），不能拿包围盒尺寸去排工艺」——
    **不给任何可执行下一步**；同一个函数里缺材料/料厚那条分支却逐条写
    「缺材料 → 点这一行「补材料」补上」。而且 `loop_budget_exhausted`（这一件没算完，可重试）
    与 `odd_endpoints`（图纸真的没闭合，要人处理）除了原因码之外**文案完全一样**。
  · 没有任何件级轮廓出路：`main.py` 只有 `…/{part_code}/thickness` / `material` / `process` /
    `cost` / `solid`（+ 两条 `-lookup`），**没有** `…/outline` 一类；
    `packaging_parts.py` 里与轮廓相关的全是判定函数（`outline_diagnosis` / `_open_outline_reason` /
    `_rescue_outline`），没有落库/签字的函数。
  · 前端只有"画出来"没有"点下去"：`app.js:1115-1127` 有一张 `OUTLINE_STATUS_TEXT` /
    `OUTLINE_OPEN_REASON_TEXT` 文案表（未闭合件画虚线 + 包围盒），零件树里却只有
    `part-material-fix`（`app.js:2740`）/ `part-thickness-fix`（`app.js:2753`）两个控件。
  · "重跑一次"也不会变：`_open_outline_reason()` 的判定是**确定性**的
    （`outline_diagnosis()` 的 docstring 明确要求逐件诊断两次跑逐字相同）；
    唯一的重抽接口 `…/packaging-parts/extract`（`main.py:7097`）全仓前端 **0 命中**。
  · 34 实测后果：`a42e5e60a720` 上 `closed 60/64`、
    `unprocessable_reason_mix.PACKAGING_PART_NOT_CLOSED = 4`（代表件 `DWG-P01` / `DWG-P02`），
    这 4 件在任何页面上都推不到工艺/成本。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.packaging_parts"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

#: 出口文案里必须出现"下一步做什么"的痕迹（任一即可，Spec §2.1）。
ACTION_MARKERS = ("点这一行", "点这行", "重算轮廓", "重新解析", "重抽", "改图", "重新上传",
                  "人工确认", "签字确认", "按包围盒", "估算确认")
#: `…/packaging-parts/{part_code}/outline` 一类件级轮廓出口的路径形状（Spec §2.3）。
OUTLINE_ROUTE = re.compile(r"packaging-parts/\{part_code\}/outline")
#: 服务层允许的落库 / 签字函数名（任一即可，Spec §2.3）。
OUTLINE_HELPERS = ("set_manual_outline", "save_part_outline", "recompute_outline",
                   "confirm_part_outline", "save_outline_confirmation")
#: 前端控件类名（与 `part-material-fix` / `part-thickness-fix` 同形状，Spec §2.3）。
OUTLINE_CONTROL = "part-outline-fix"
#: 未闭合原因闭集（Spec §2.5 冻结面）。
OPEN_REASONS = ("no_curve_entity", "unit_unconfirmed", "loop_budget_exhausted",
                "odd_endpoints", "loop_too_small")
#: `_open_outline_reason()` 的判定顺序（Spec §2.5 冻结面）。
REASON_ORDER = ("no_curve_entity", "loop_budget_exhausted", "odd_endpoints", "loop_too_small")
#: 卡片第 6 步的 10 列（Spec §2.5 冻结面）。
CARD_LABELS = ["零件号", "名称", "角色", "材料", "厚度(mm)", "展开长(mm)", "展开宽(mm)",
               "轮廓状态", "可算", "不可算原因"]


def module():
    return importlib.import_module(PKG)


def text_of(path):
    return path.read_text(encoding="utf-8", errors="replace")


def open_part(reason):
    return {"part_code": "DWG-P01", "name": "图纸零件 DWG-P01",
            "outline_status": "open", "outline_reason": reason,
            "material": {"spec": "灰板", "grade": "", "material_code": ""},
            "material_source": {"kind": "note", "text": "图纸注记"},
            "thickness_mm": 1.2, "thickness_source": {"kind": "note", "text": "1.2mm"},
            "unfolded_length_mm": 440.0, "unfolded_width_mm": 480.0,
            "attribution": {"kind": "", "material_unresolved": [], "thickness_unresolved": []}}


def function_body(text, name):
    match = re.search(r"^def %s\(" % re.escape(name), text, re.M)
    if not match:
        return ""
    lines = text[match.start():].splitlines(keepends=True)
    index = 0
    while index < len(lines) and not lines[index].rstrip().endswith(":"):
        index += 1
    body = []
    for line in lines[index + 1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        body.append(line)
    return "".join(body)


class AWayOut(unittest.TestCase):
    """A 组：未闭合件要有说得清的下一步、有能点的入口（今天全红）。"""

    def test_a1_blocked_message_tells_the_user_what_to_do_next(self):
        mod = module()
        verdict = mod.processability(open_part("odd_endpoints"))
        self.assertEqual("PACKAGING_PART_NOT_CLOSED", verdict.get("code"),
                         "测试前提失效：未闭合件的稳定码必须是 PACKAGING_PART_NOT_CLOSED")
        message = str(verdict.get("message") or "")
        self.assertTrue(any(marker in message for marker in ACTION_MARKERS),
                        "未闭合件的出口文案没有可执行下一步（只有现象 + 原因码）：%r —— "
                        "对比缺材料/料厚那条分支会写「缺材料 → 点这一行「补材料」补上」"
                        "（Spec §2.1 第 1 条）" % message)

    def test_a2_retryable_and_manual_reasons_get_different_advice(self):
        mod = module()
        retryable = str(mod.processability(open_part("loop_budget_exhausted")).get("message") or "")
        manual = str(mod.processability(open_part("odd_endpoints")).get("message") or "")
        strip = lambda text: text.replace("loop_budget_exhausted", "").replace("odd_endpoints", "")
        self.assertNotEqual(
            strip(retryable), strip(manual),
            "「这一件没算完（可重试）」与「图纸真的没闭合（要人处理）」给的是同一句话：%r —— "
            "前者该指向重算、后者该指向改图或人工签字（Spec §2.1 第 2 条）" % strip(retryable))

    def test_a3_part_level_outline_route_and_helper_exist(self):
        main_text = text_of(MAIN_PY)
        self.assertTrue(
            OUTLINE_ROUTE.search(main_text),
            "main.py 里没有 `…/packaging-parts/{part_code}/outline` 一类的件级轮廓出口"
            "（今天只有 thickness / material / process / cost / solid）（Spec §2.1 第 3 条）")
        parts_text = text_of(PARTS_PY)
        self.assertTrue(any(("def %s(" % name) in parts_text for name in OUTLINE_HELPERS),
                        "packaging_parts.py 里没有轮廓出路对应的落库/签字函数 %s（Spec §2.1 第 3 条）"
                        % "、".join(OUTLINE_HELPERS))

    def test_a4_frontend_outline_control_is_wired(self):
        self.assertTrue(OUTLINE_CONTROL in text_of(APP_JS),
                        "前端零件树没有未闭合件的可点控件（%s）—— 现在只有画虚线与包围盒，"
                        "用户拿不到下一步（Spec §2.1 第 3 条）" % OUTLINE_CONTROL)


class BGuards(unittest.TestCase):
    """B 组：今天就是绿的，不许被改红。"""

    def test_b1_open_reason_closed_set_and_order_unchanged(self):
        mod = module()
        self.assertEqual(list(OPEN_REASONS), list(mod.OUTLINE_OPEN_REASONS),
                         "未闭合原因闭集是冻结面，本层不许新增/删除码（Spec §2.5 第 5 条）")
        body = function_body(text_of(PARTS_PY), "_open_outline_reason")
        self.assertTrue(body, "取不到 `_open_outline_reason()` —— 测试前提失效")
        seen = [name for name in REASON_ORDER if '"%s"' % name in body]
        positions = [body.find('"%s"' % name) for name in seen]
        self.assertEqual(sorted(positions), positions,
                         "`_open_outline_reason()` 的判定顺序不许变：%s（Spec §2.5 第 5 条）" % seen)

    def test_b2_open_part_still_rejected_with_stable_shape(self):
        verdict = module().processability(open_part("odd_endpoints"))
        self.assertFalse(verdict.get("ok"), "未闭合件不许被算（Spec §2.5 第 5 条）")
        self.assertEqual("PACKAGING_PART_NOT_CLOSED", verdict.get("code"))
        self.assertEqual(["outline"], list(verdict.get("missing_variables") or []),
                         "`missing_variables` 必须仍是 [\"outline\"]（Spec §2.5 第 5 条）")

    def test_b3_nobody_writes_outline_status_closed(self):
        pattern = re.compile(r'outline_status"?\]?\s*=\s*"closed"')
        for path in (PARTS_PY, MAIN_PY):
            self.assertIsNone(pattern.search(text_of(path)),
                              "%s 里出现了把 `outline_status` 写成 closed 的赋值 —— "
                              "`closed` 只能由几何判定给出（Spec §2.5 第 4 条）" % path.name)

    def test_b4_card_columns_and_card_row_unchanged(self):
        mod = module()
        self.assertEqual(CARD_LABELS, [col["label"] for col in mod.card_columns()],
                         "卡片列定义是冻结面（Spec §2.5 第 5 条）")
        body = function_body(text_of(PARTS_PY), "card_row")
        self.assertIn("processability", body,
                      "`card_row()` 的可算性仍必须只取自 `processability()`（Spec §2.5 第 5 条）")


if __name__ == "__main__":
    unittest.main()
