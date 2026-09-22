"""红测：阶段顺序必须等于依赖顺序（图纸解析要夹在「1.1 存草稿」与「1.1 提交确认」之间）。

Spec：`docs/specs/packaging-stage-order-equals-dependency.md`

现状缺口（9-22 在本机 HEAD 上逐条核对，不是推断）：
  · 依赖是硬的：图纸解析第 8 步 `field_write` 要把字段**回写进那张需求单**，所以需求必须处于
    `EDITABLE_STATUSES = ("draft", "rejected")`；而 1.1 提交确认 / 1.2 通过确认 / 1.3 审核通过
    会把它推成 `pending_confirmation` / `pending_review` / `approved` → 按页面顺序做必
    `blocked / REQUIREMENT_NOT_EDITABLE`（实测 7/8）。
  · 呈现顺序却是编号顺序：`workflow_stages.py` 把 `2.1 图纸解析` 排在 `1.2 确认需求`、
    `1.3 审核需求` 之后 —— 用户顺着流程栏点就一定踩坑。
  · 编号表被手抄了 4 份（`workflow.js` / `requirement-create.js` / `requirement-confirm-page.js` /
    `report-publish-result.js`），改一处没用。
  · 1.1 页面没有任何"先去做图纸解析"的引导与入口：门禁只管拦住并说原因，不负责把人送过去。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STAGES_PY = ROOT / "tech_app" / "backend" / "services" / "workflow_stages.py"
REQUIREMENT_SERVICE = ROOT / "tech_app" / "backend" / "services" / "requirement_service.py"
FRONTEND = ROOT / "tech_app" / "frontend"

#: §1.4 四个手抄编号表的文件（Spec §2.3：必须由 `workflow_stages.py` 派生）。
DUPLICATE_TABLE_FILES = ("workflow.js", "requirement-create.js",
                         "requirement-confirm-page.js", "report-publish-result.js")
#: 1.1 页面的两个实现（新表单 / 旧表单），任一处给引导与入口都算数。
CREATE_PAGES = ("requirement-create.js", "requirement.js")

#: 写死的子步骤编号表特征（成对出现的编号 + 标题）。
HARDCODED_TABLE = (
    re.compile(r"1\.2['\"]?\s*[,，]\s*['\"]?确认"),
    re.compile(r"1\.2\s*确认"),
    re.compile(r"1\.3\s*审核"),
    re.compile(r"['\"]2\.1['\"]\s*[,，]\s*['\"]图纸解析"),
    re.compile(r"2\.1\s*图纸解析"),
)
SUB_PATTERN = re.compile(r"^[1-5]\.\d$")


def text_of(path):
    return path.read_text(encoding="utf-8", errors="replace")


def function_body(text, name):
    """按缩进取一个模块级函数的函数体（签名可跨行；取不到返回空串）。"""
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


class AStageOrder(unittest.TestCase):
    def test_a1_drawing_comes_before_requirement_confirm(self):
        from tech_app.backend.services import workflow_stages
        order = [row["stage_id"] for row in workflow_stages.STAGES]
        self.assertIn("drawing", order, "测试前提失效：阶段表里必须有 drawing")
        self.assertIn("requirement-confirm", order, "测试前提失效：阶段表里必须有 requirement-confirm")
        self.assertLess(
            order.index("drawing"), order.index("requirement-confirm"),
            "图纸解析必须排在「确认需求」之前（依赖：字段回写要需求处于可编辑草稿）；"
            "当前顺序 %s（Spec §2.1）" % " → ".join(order[:5]))

    def test_a2_sub_numbers_stay_unique_and_shaped(self):
        from tech_app.backend.services import workflow_stages
        subs = [row["sub"] for row in workflow_stages.STAGES]
        self.assertEqual(13, len(subs), "13 个子步骤一个都不许少（Spec §2.2）")
        self.assertEqual(len(subs), len(set(subs)), "子步骤号必须唯一（Spec §2.2）")
        bad = [sub for sub in subs if not SUB_PATTERN.match(sub)]
        self.assertEqual([], bad, "子步骤号形状必须仍是 `^[1-5]\\.\\d$`（Spec §2.2）：%s" % bad)

    def test_a3_no_second_copy_of_the_numbering_table(self):
        offenders = []
        for name in DUPLICATE_TABLE_FILES:
            text = text_of(FRONTEND / name)
            if any(pattern.search(text) for pattern in HARDCODED_TABLE):
                offenders.append(name)
        self.assertEqual([], offenders,
                         "这些前端文件还在手抄子步骤编号表：%s —— 编号只能由 "
                         "`workflow_stages.py` 一处派生（Spec §2.3）" % "、".join(offenders))


class BCreatePageGuidance(unittest.TestCase):
    def test_b1_create_page_points_at_drawing_parse(self):
        joined = "\n".join(text_of(FRONTEND / name) for name in CREATE_PAGES)
        self.assertIn("图纸解析", joined, "1.1 页面必须提到图纸解析（Spec §2.4）")
        self.assertTrue(("stage=drawing" in joined) or ("drawing.html" in joined)
                        or re.search(r"navigate\w*\(\s*['\"]drawing", joined)
                        or re.search(r"['\"]drawing['\"]\s*,\s*[A-Za-z_$]", joined),
                        "1.1 页面必须给一个能进图纸解析的可点入口（Spec §2.4）")


class CGuards(unittest.TestCase):
    def test_c1_drawing_guard_still_wired_at_both_gates(self):
        text = text_of(REQUIREMENT_SERVICE)
        self.assertIn("def assert_requirement_drawing_parsed", text,
                      "顺序门禁不许被撤掉（Spec §2.5）")
        for name in ("submit_requirement_confirmation", "confirm_requirement"):
            body = function_body(text, name)
            self.assertTrue(body, "取不到 %s() —— 测试前提失效" % name)
            self.assertIn("assert_requirement_drawing_parsed", body,
                          "%s 必须继续过同一道顺序门禁（Spec §2.5）" % name)

    def test_c2_editable_statuses_unchanged(self):
        text = text_of(REQUIREMENT_SERVICE)
        self.assertRegex(text, r'EDITABLE_STATUSES\s*=\s*\(\s*"draft"\s*,\s*"rejected"\s*,?\s*\)',
                         "编辑口径不许放宽（Spec §2.5）")

    def test_c3_return_to_draft_covers_approved(self):
        text = text_of(REQUIREMENT_SERVICE)
        match = re.search(r"RETURNABLE_TO_DRAFT_STATUSES\s*=\s*\(([^)]*)\)", text)
        self.assertIsNotNone(match, "退路状态集必须仍在（Spec §2.5）")
        self.assertIn("approved", match.group(1),
                      "`approved` 之后必须仍退得回草稿（Spec §2.5）")


if __name__ == "__main__":
    unittest.main()
