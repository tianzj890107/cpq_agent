"""红测：卡片「完成本步」对快照必须是合并，不是整份替换。

Spec：`docs/specs/quote-card-step-snapshot-merge-on-complete.md`

现状缺口（34 只读复验 + 本机源码，不是推断）：
  · `cpq_wf.complete_step()` 的 UPDATE 逐字是 `… data_snapshot = %s::jsonb …`，入参 `snap`
    直接落库；空串/非法 JSON 先置 `None` 再写 —— 等于把该步已有快照**清空**；
  · 同一模块里早就有合并语义 `merge_step_snapshot()`（`merged[key] = value`），但只有回传通道在用；
  · 后果（同一份代码、同一套回传通道，只差"最后谁写了这一步"）：
      - `566207eb006a` / `e2e00a1b2c3d` / `71c5a1c26619` → 第 2 步快照是
        `['packaging_package','s2_packaging','s2_packaging_cost']`（回传通道写的）；
      - `c0239386c1c4`（本轮真跑）/ `e59e1b382478`（`## 288` 引用的那张）→ 只剩
        `['s2_cost','s2_route']`（**被脚本 `/wf/card/step-done` 覆盖过**）。
    也就是说：包装这条链本来是通的，是"写快照用替换而不是合并"把它打掉的。

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

CPQ_WF = ROOT / "cpq_wf.py"
TECH_BRIDGE = ROOT / "cpq_tech_bridge.py"

PACKAGING_SNAPSHOT_KEYS = ("s2_packaging", "s2_packaging_cost", "packaging_package")


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


class ASnapshotMerge(unittest.TestCase):
    def test_a1_complete_step_merges_the_existing_snapshot(self):
        body = function_body(text_of(CPQ_WF), "complete_step")
        self.assertTrue(body, "取不到 complete_step() —— 测试前提失效（Spec §2.1）")
        self.assertTrue(("_snapshot_dict(" in body) or ("merge_step_snapshot(" in body),
                        "complete_step() 必须先把该步已有快照读出来再合并 —— "
                        "今天它是直接写 `data_snapshot = snap`，负载里没出现的键全部消失（Spec §2.1）")
        self.assertNotRegex(body, r"data_snapshot = %s::jsonb",
                            "写快照不许还是「入参直接落库」这一条（Spec §2.1）")

    def test_a2_empty_payload_never_wipes_the_snapshot(self):
        body = function_body(text_of(CPQ_WF), "complete_step")
        self.assertTrue(body, "取不到 complete_step() —— 测试前提失效（Spec §2.2）")
        # 只认「COALESCE 的第一个参数是要写的新值、随后回落 data_snapshot」这种把原值兜住的写法
        # （`COALESCE(%s::jsonb, data_snapshot)`）；反向出现不算 —— 那可能只是同一句 SQL 里
        # 另一列的 COALESCE，与快照无关。
        guarded = bool(re.search(r"COALESCE\([^()]*data_snapshot", body)) or ("merge_step_snapshot(" in body)
        self.assertTrue(guarded,
                        "快照为空 / 非法 JSON 时必须保持原值（COALESCE，或直接走 merge_step_snapshot）—— "
                        "今天空负载会把该步快照写成 NULL（Spec §2.2）")


class BGuards(unittest.TestCase):
    def test_b1_merge_step_snapshot_still_merges_key_by_key(self):
        body = function_body(text_of(CPQ_WF), "merge_step_snapshot")
        self.assertTrue(body, "merge_step_snapshot() 必须仍在（Spec §2.5）")
        self.assertRegex(body, r"merged\[key\] = value",
                         "合并语义必须是逐键覆盖（Spec §2.5）")

    def test_b2_tech_side_projection_keys_unchanged(self):
        body = function_body(text_of(TECH_BRIDGE), "packaging_snapshot")
        self.assertTrue(body, "取不到 packaging_snapshot() —— 测试前提失效（Spec §2.4）")
        missing = [key for key in PACKAGING_SNAPSHOT_KEYS if key not in body]
        self.assertEqual([], missing, "技术侧投影的键名不许改（Spec §2.4）：%s" % "、".join(missing))

    def test_b3_current_step_still_first_pending(self):
        body = function_body(text_of(CPQ_WF), "complete_step")
        self.assertIn("next_pending_step(", body,
                      "`current_step` 仍取第一个未完成步，重放不许把进度条倒回去（Spec §2.3）")


if __name__ == "__main__":
    unittest.main()
