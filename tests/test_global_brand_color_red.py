import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]

TOP_LEVEL_UI = [
    path for path in ROOT.iterdir()
    if path.is_file() and path.suffix.lower() in {".html", ".css", ".js"}
]
TECH_UI = [
    path for base in (ROOT / "tech_app" / "frontend", ROOT / "tech_app" / "apps" / "tech-process")
    for path in base.rglob("*")
    if path.is_file() and path.suffix.lower() in {".html", ".css", ".js"}
]
UI_FILES = sorted(set(TOP_LEVEL_UI + TECH_UI))

# 只列项目曾用作品牌/交互强调的紫色和竞争蓝；不扫描语义绿、橙、红或中性色。
LEGACY_BRAND_COLORS = {
    "#6366f1", "#4f46e5", "#8b5cf6", "#7c3aed", "#a855f7",
    "#3b82f6", "#2563eb", "#1d4ed8", "#1677ff", "#2f6bff",
    "#377df4", "#1f4fd0",
}
LEGACY_BRAND_RGB = {
    "99,102,241", "124,58,237", "139,92,246", "59,130,246",
    "37,99,235", "29,78,216", "22,119,255", "47,107,255",
}


class GlobalBrandColorContract(unittest.TestCase):
    def texts(self):
        for path in UI_FILES:
            yield path, path.read_text(encoding="utf-8")

    def test_global_entry_points_define_0067d1_as_primary(self):
        entries = [
            "报价首页.html", "配置首页.html", "规则首页.html",
            "XBOM智能体-配置BOM生成.html", "规则助手-规则配置.html",
            "tech_app/frontend/style.css", "tech_app/frontend/tech-workbench.css",
        ]
        for relative in entries:
            text = (ROOT / relative).read_text(encoding="utf-8")
            if "#0067D1" not in text.upper():
                self.fail(f"{relative} 尚未声明统一品牌主色")

    def test_no_legacy_purple_or_competing_blue_brand_literals(self):
        failures = []
        for path, text in self.texts():
            lower = re.sub(r"\s+", "", text.lower())
            found = sorted(color for color in LEGACY_BRAND_COLORS if color in lower)
            found_rgb = sorted(rgb for rgb in LEGACY_BRAND_RGB if rgb in lower)
            if found or found_rgb:
                failures.append(f"{path.relative_to(ROOT)}: {', '.join(found + found_rgb)}")
        self.assertFalse(failures, "仍存在旧品牌紫色/竞争蓝：\n" + "\n".join(failures))

    def test_shared_dynamic_components_use_new_primary_fallback(self):
        for relative in ("cpq_auth.js", "cpq_msg.js"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            if "#0067D1" not in text.upper():
                self.fail(f"{relative} fallback 未统一")
            self.assertNotIn("#6366f1", text.lower())

    def test_blue_tint_tokens_exist_for_background_border_and_interaction(self):
        combined = "\n".join(text for _, text in self.texts()).upper()
        for color in ("#0057B8", "#004A9F", "#EAF3FC", "#F4F9FE", "#B8D7F4"):
            if color not in combined:
                self.fail(f"缺少统一同色系 token：{color}")
        compact = re.sub(r"\s+", "", combined)
        self.assertRegex(compact, r"RGBA\(0,103,209,\.?\d+\)")

    def test_semantic_success_warning_and_danger_colors_are_preserved(self):
        combined = "\n".join(text for _, text in self.texts()).lower()
        self.assertRegex(combined, r"#(?:10b981|16a34a)")
        self.assertRegex(combined, r"#(?:f59e0b|f97316)")
        self.assertRegex(combined, r"#(?:ef4444|dc2626)")


if __name__ == "__main__":
    unittest.main()
