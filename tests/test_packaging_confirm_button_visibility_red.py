"""红测：包装项目的业务零件也算“已解析”，2.1 必须出现「确认工艺」。

现状缺口：app.js 的 drawingParsed() 只认 currentIR / standard_parts。包装项目的
业务部件在 currentPackagingBusinessParts 里，currentIR 为空，于是：
  · confirmDrawingResult.visible = drawingParsed() && ... 恒为 false；
  · runAllPartProcesses 的主按钮判定 drawingParsed() && ... 恒为 false；
用户侧看到的就是“工艺推荐跑完了，但没有确认工艺按钮”。

红线：确认闸门、批量工艺实现、确认后的按人发送都保持不变；只让包装业务清单
参与“已解析”的同步判定。
"""
from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "tech_app" / "frontend" / "app.js").read_text(encoding="utf-8", errors="replace")
INDEX = (ROOT / "tech_app" / "frontend" / "index.html").read_text(encoding="utf-8", errors="replace")


def block_from(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    tail = idx + len(marker) - 1
    brace = tail if text[tail:tail + 1] == "{" else text.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    pos = brace
    while pos < len(text):
        ch = text[pos]
        nxt = text[pos + 1] if pos + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            end = text.find("\n", pos)
            pos = len(text) if end < 0 else end
            continue
        if ch in ("\"", "'", "`"):
            quote = ch
            pos += 1
            while pos < len(text):
                if text[pos] == "\\":
                    pos += 2
                    continue
                if text[pos] == quote:
                    break
                pos += 1
            pos += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace:pos + 1]
        pos += 1
    return ""


class PackagingConfirmButtonVisibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = block_from(APP, "function drawingParsed(")
        cls.confirm = block_from(APP, "confirmDrawingResult:")
        cls.bulk = block_from(APP, "runAllPartProcesses:")

    def test_drawing_parsed_counts_packaging_business_parts(self):
        self.assertTrue(self.parsed, "找不到 drawingParsed()")
        self.assertIn("packagingBusinessPartRows(currentPackagingBusinessParts)", self.parsed,
                      "包装业务零件必须参与“已解析”判定")
        self.assertLess(self.parsed.index("packagingBusinessPartRows(currentPackagingBusinessParts)"),
                        self.parsed.index("return Boolean(currentIR"),
                        "包装业务零件要先于 currentIR 判定，不能只作为兜底")

    def test_confirm_process_button_is_visible_for_packaging_until_confirmed(self):
        self.assertTrue(self.confirm, "找不到 confirmDrawingResult")
        self.assertIn("visible: drawingParsed() && !window.PackagingHandoff?.current(currentProject).confirmed",
                      self.confirm, "确认工艺必须仍只在未确认时出现，确认后让位给发送按钮")
        self.assertIn("packagingBusinessPartRows(currentPackagingBusinessParts).length?'确认工艺'",
                      self.confirm, "有包装业务零件时按钮文案必须是“确认工艺”")

    def test_bulk_button_can_be_primary_before_packaging_processes_are_ready(self):
        self.assertTrue(self.bulk, "找不到 runAllPartProcesses")
        self.assertIn('role: (drawingParsed() && !partsProcessComplete()) ? "primary" : "aux"',
                      self.bulk, "全部工艺未完成时，批量按钮必须能成为主按钮")

    def test_app_cache_is_busted_for_the_fix(self):
        self.assertIn("app.js?v=20261019-confirmfix1", INDEX,
                      "修改 app.js 后必须更新版本号，避免浏览器继续用旧缓存看不到按钮")


if __name__ == "__main__":
    unittest.main()
