"""红测：有效的「零零件 IR」必须算图纸解析完成。

现状缺口（实测）：
  · `tech_app/frontend/tech-workbench.js:898` 用零件数量当完成标志：
    `const irParts = (parts && parts.parts) || []; if (irParts.length) done.add('drawing');`
  · 同一写法还有 `tech-workbench.js:1156`（`project.ir.parts.length` 参与「有 IR」）与
    `报价首页.html:1677`（`ir.parts && ir.parts.length`）。
  · 解析成功但结果确实是 0 个零件的项目，因此永远显示「图纸解析未完成」，阶段恢复也判成
    2.1 没做完。零件数量是结果，不该同时充当完成标志。

可靠依据（实测）：解析成功才写 IR —— `store.save_ir(..., stage="parsed")` 会同时写 IR 文档、
`meta.ir_revision += 1`、`meta.stages["parsed"] = 时间戳`（3D 导入路径写 `parsed_3d`）；
只上传未解析时只有 `meta.stages.uploaded`。

行为断言用 node 真实执行共享判定 `TechStageRestore.drawingParsed(input)`（与第 3 项批次同一份
`tech-stage-restore.js`），不做静态文本提取；结构断言只用来确认调用点真的改成了共享判定。
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONT = ROOT / "tech_app" / "frontend"
SHARED = FRONT / "tech-stage-restore.js"
WORKBENCH = FRONT / "tech-workbench.js"
WORKBENCH_HTML = FRONT / "tech-workbench.html"
CPQ_HOME = ROOT / "报价首页.html"

IR_WITH_PARTS = {"device_name": "测试整机", "parts": [{"part_id": "P1", "name": "壳体"}]}
IR_EMPTY_PARTS = {"device_name": "测试整机", "parts": []}
IR_BARE = {"parts": []}

# (用例名, input, 期望)
CASES = [
    ("zero_part_ir_with_parsed_stage",
     {"ir": IR_EMPTY_PARTS, "meta": {"stages": {"parsed": "2026-09-15T10:00:00"}}}, True),
    ("zero_part_ir_without_any_hint",
     {"ir": IR_EMPTY_PARTS, "meta": {"stages": {}}}, True),
    ("zero_part_ir_alone", {"ir": IR_EMPTY_PARTS}, True),
    ("parsed_stage_without_ir",
     {"ir": {}, "meta": {"stages": {"parsed": "2026-09-15T10:00:00"}}}, True),
    ("ir_revision_without_ir_doc", {"ir": {}, "meta": {"ir_revision": 2}}, True),
    ("ir_input_revision_fallback", {"ir": {}, "meta": {"ir_input_revision": 1}}, True),
    ("3d_import_stage",
     {"ir": IR_BARE, "meta": {"stages": {"parsed_3d": "2026-09-15T11:00:00"}}}, True),
    ("stages_passed_explicitly",
     {"ir": IR_EMPTY_PARTS, "stages": {"parsed": "2026-09-15T10:00:00"}}, True),
    ("normal_ir_with_parts", {"ir": IR_WITH_PARTS, "meta": {}}, True),
    ("upload_only", {"ir": None, "meta": {"stages": {"uploaded": "2026-09-15T09:00:00"}}}, False),
    ("nothing_at_all", {"ir": {}, "meta": {}}, False),
    ("empty_input", {}, False),
    ("null_input_is_tolerated", {"ir": None, "meta": None, "stages": None}, False),
]

HARNESS = """
const fs = require('fs');
globalThis.window = globalThis.window || {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const api = globalThis.window.TechStageRestore || globalThis.TechStageRestore;
if (!api || typeof api.drawingParsed !== 'function') {
  console.error('NO_API: TechStageRestore.drawingParsed 缺失');
  process.exit(3);
}
const cases = JSON.parse(process.argv[2]);
const out = {};
for (const row of cases) {
  try { out[row[0]] = api.drawingParsed(row[1]); }
  catch (error) { out[row[0]] = 'THREW:' + (error && error.message); }
}
process.stdout.write(JSON.stringify(out));
""".strip()


def js_body(source: str, marker: str) -> str:
    idx = source.find(marker)
    if idx < 0:
        return ""
    brace = source.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(source):
        char = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if char == "/" and nxt == "/":
            j = source.find("\n", i)
            i = len(source) if j < 0 else j
            continue
        if char in "\"'`":
            quote = char
            i += 1
            while i < len(source):
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace:i + 1]
        i += 1
    return ""


class EmptyIrParseCompletionRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workbench = WORKBENCH.read_text(encoding="utf-8", errors="replace")
        cls.workbench_html = WORKBENCH_HTML.read_text(encoding="utf-8", errors="replace")
        cls.cpq_home = CPQ_HOME.read_text(encoding="utf-8", errors="replace")
        cls.shared_src = SHARED.read_text(encoding="utf-8", errors="replace") if SHARED.exists() else ""
        cls.matrix = cls._run_matrix()

    @classmethod
    def _run_matrix(cls) -> dict:
        node = shutil.which("node")
        if not node:  # pragma: no cover
            raise unittest.SkipTest("未找到 node，无法执行解析完成判定的行为断言")
        if not SHARED.exists():
            return {}
        proc = subprocess.run([node, "-e", HARNESS, str(SHARED),
                               json.dumps([[n, i] for n, i, _ in CASES], ensure_ascii=False)],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return {"__harness__": (proc.stderr or proc.stdout)[-400:]}
        return json.loads(proc.stdout)

    # ---------------------------------------------------- R1 行为矩阵
    def test_shared_judgement_is_reachable(self):
        self.assertTrue(self.matrix and "__harness__" not in self.matrix,
                        "共享判定 tech-stage-restore.js 的 TechStageRestore.drawingParsed 不可用："
                        f"{self.matrix.get('__harness__', '（文件缺失）')}")

    def test_completion_matrix(self):
        for name, _input, expected in CASES:
            with self.subTest(case=name):
                self.assertEqual(self.matrix.get(name), expected,
                                 f"{name} 期望 {expected}，实际 {self.matrix.get(name)}")

    def test_zero_part_ir_counts_as_parsed(self):
        for name in ("zero_part_ir_with_parsed_stage", "zero_part_ir_without_any_hint",
                     "zero_part_ir_alone", "3d_import_stage", "stages_passed_explicitly"):
            with self.subTest(case=name):
                self.assertIs(self.matrix.get(name), True,
                              f"{name}：解析成功但零件数为 0，必须算解析完成")

    def test_upload_only_is_not_parsed(self):
        for name in ("upload_only", "nothing_at_all", "empty_input"):
            with self.subTest(case=name):
                self.assertIs(self.matrix.get(name), False,
                              f"{name}：没有解析留痕时不能算完成")

    def test_judgement_never_throws_on_partial_input(self):
        for name, value in self.matrix.items():
            with self.subTest(case=name):
                self.assertNotIsInstance(value, str, f"{name} 判定抛异常：{value}")

    # ---------------------------------------------------- R3 唯一判定（结构）
    def test_refresh_progress_uses_shared_judgement(self):
        body = js_body(self.workbench, "async function refreshProgress(")
        self.assertTrue(body, "找不到 refreshProgress()")
        # 完成态的判定唯一来源在批次 5B 升级为「后端统一流程投影」：refreshProgress 只消费
        # TechWorkflowProjection.progress()，不再逐行 done.add(...)。判定的唯一性与
        # 「不看零件数量」这两条能力断言一条不少，只是换了承载它的那条实现链。
        self.assertIn("TechWorkflowProjection", body,
                      "图纸解析打点必须来自唯一判定 TechWorkflowProjection.progress()")
        self.assertIn("/workflow/projection", body,
                      "完成态必须取自后端统一投影接口，而不是前端各自拼")
        self.assertNotRegex(body, r"parts\.length",
                            "不得再用零件数量判断图纸解析是否完成")

    def test_no_part_count_completion_anywhere(self):
        targets = {
            "tech-workbench.js": self.workbench,
            "报价首页.html": self.cpq_home,
            "tech-stage-restore.js": self.shared_src,
        }
        for name, text in targets.items():
            if not text:
                continue
            with self.subTest(file=name):
                self.assertNotRegex(text, r"parts\s*\??\.\s*length",
                                    f"{name} 里仍有「零件数量 > 0 才算解析完成」的判定；"
                                    "零件数量只能用于文案展示")

    def test_both_pages_load_shared_module(self):
        for name, text in (("tech-workbench.html", self.workbench_html),
                           ("报价首页.html", self.cpq_home)):
            with self.subTest(page=name):
                self.assertIn("tech-stage-restore.js", text,
                              f"{name} 必须引入共享判定模块")

    # ---------------------------------------------------- R2 / R4 不缩水
    def test_step_dots_still_marked(self):
        # 步骤条打点仍在（阶段页签 / 顶部流程条照旧点亮），只是完成态的算法从「前端逐行
        # done.add(...)」搬到了后端统一投影（批次 5B）：前端只把投影换算成索引，不再自己
        # 拼完成态。这里改查「打点所依赖的投影与子步骤编号」都还在，数量与能力一条不少。
        self.assertIn("TechWorkflowProjection", self.workbench,
                      "步骤条打点必须消费唯一流程投影，不得删掉")
        for key in ("1.1", "2.1", "3.3", "4.3", "5.2", "5.3"):
            with self.subTest(key=key):
                self.assertIn(key, self.workbench, f"子步骤 {key} 的打点编号不得删除")

    def test_shared_module_stays_pure(self):
        if not self.shared_src:
            self.skipTest("共享模块尚未创建（第 3 项批次引入）")
        for forbidden in ("document.", "fetch(", "localStorage"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.shared_src,
                                 "共享判定必须保持纯函数：不碰 DOM、不发请求")

    def test_backend_and_routes_untouched(self):
        main = (ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8",
                                                                     errors="replace")
        for route in ('@app.get("/api/projects/{project_id}")',
                      '@app.get("/api/projects/{project_id}/workflow")',
                      '@app.get("/api/projects/{project_id}/summary")'):
            with self.subTest(route=route):
                self.assertIn(route, main, f"路由 {route} 不得改动")

    def test_apply_stage_entry_kept(self):
        self.assertIn("applyStage(stage,", self.workbench,
                      "工作台仍须用 applyStage() 落阶段")
        self.assertIn("techWorkbenchUrl(stage", self.cpq_home,
                      "首页仍须用 techWorkbenchUrl() 拼目标地址")


if __name__ == "__main__":
    unittest.main()
