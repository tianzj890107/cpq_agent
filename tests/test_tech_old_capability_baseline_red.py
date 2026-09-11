"""第 20 步红测：以旧 2.1 页面为基线的能力对照验收。

覆盖：
1. 新增可机读能力清单 docs/specs/tech-agent-recovery-20-capability-baseline.json，
   正好覆盖 upload / view / generate / modify / submit-review / export / version / expand-part；
2. 每条能力至少有一个 routes 或 refs 锚点；
3. routes 必须是 main.py 里真实存在的 @app 路由；refs 的 文件#token 必须能解析到真实代码；
4. 旧 2.1 页与八项能力依赖的冻结路由不减少；零件能力仍只在右侧看板 iframe 内展开。

不联网、不起服务、不读真实业务数据。
"""
from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "specs" / "tech-agent-recovery-20-capability-baseline.json"
MAIN = ROOT / "tech_app" / "backend" / "main.py"
FRONTEND = ROOT / "tech_app" / "frontend"
INDEX = FRONTEND / "index.html"
APP_JS = FRONTEND / "app.js"

EIGHT = (
    "upload", "view", "generate", "modify",
    "submit-review", "export", "version", "expand-part",
)
# 八项能力各自的关键冻结路由（可以新增，不能消失）。
FROZEN_ROUTES = (
    ("post", "/api/projects"),
    ("post", "/api/projects/3d"),
    ("get", "/api/projects/{project_id}/files"),
    ("get", "/api/projects/{project_id}/tree"),
    ("get", "/api/projects/{project_id}/bom"),
    ("get", "/api/projects/{project_id}/bom.csv"),
    ("put", "/api/projects/{project_id}/ir"),
    ("post", "/api/projects/{project_id}/generate"),
    ("post", "/api/projects/{project_id}/approval/submit"),
    ("get", "/api/projects/{project_id}/versions"),
    ("get", "/api/projects/{project_id}/versions/{version}"),
)


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _routes():
    return {(m.lower(), p) for m, p in re.findall(
        r'@app\.(get|post|put|delete)\("([^"]+)"', _read(MAIN))}


def _manifest():
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(_read(MANIFEST))
    except json.JSONDecodeError:
        return {}


class TechOldCapabilityBaselineRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes = _routes()
        cls.data = _manifest()
        caps = cls.data.get("capabilities") or []
        cls.caps = {c.get("id"): c for c in caps if isinstance(c, dict)}

    # ---------------------------------------------------------------- 清单覆盖
    def test_manifest_declares_all_eight_capabilities(self):
        self.assertTrue(MANIFEST.exists(),
                        f"缺少能力清单 {MANIFEST.relative_to(ROOT)}")
        self.assertEqual(self.data.get("step"), 20, "清单 step 必须是 20")
        self.assertEqual(set(self.caps), set(EIGHT),
                         f"八项基线能力必须齐全，实际：{sorted(self.caps)}")
        self.assertEqual(len(self.caps), 8, "八项能力不得重复")

    def test_every_capability_has_a_live_anchor(self):
        for cap_id in EIGHT:
            cap = self.caps.get(cap_id)
            self.assertIsNotNone(cap, f"缺少能力 {cap_id}")
            anchors = list(cap.get("routes") or []) + list(cap.get("refs") or [])
            self.assertTrue(anchors, f"{cap_id} 至少要有一个 routes / refs 锚点")

    # ---------------------------------------------------------------- 锚点可解析
    def test_route_anchors_exist_in_backend(self):
        for cap_id, cap in self.caps.items():
            for route in cap.get("routes") or []:
                match = re.match(r"^(GET|POST|PUT|DELETE)\s+(\S+)$", str(route))
                self.assertIsNotNone(match, f"{cap_id} 路由格式应为 'METHOD /path'：{route}")
                method, path = match.group(1).lower(), match.group(2)
                self.assertIn((method, path), self.routes,
                              f"{cap_id} 引用的路由不存在：{route}")

    def test_reference_anchors_resolve_in_code(self):
        for cap_id, cap in self.caps.items():
            for ref in cap.get("refs") or []:
                self.assertIn("#", str(ref), f"{cap_id} ref 格式应为 '路径#token'：{ref}")
                rel, token = str(ref).split("#", 1)
                target = ROOT / rel
                self.assertTrue(target.exists(), f"{cap_id} ref 文件不存在：{rel}")
                self.assertIn(token, _read(target),
                              f"{cap_id} 的 {token} 未出现在 {rel}")

    # ---------------------------------------------------------------- 守护
    def test_old_2_1_page_and_entry_points_kept(self):
        self.assertTrue(INDEX.exists(), "旧 2.1 页面被删除")
        self.assertTrue(APP_JS.exists(), "2.1 看板脚本被删除")
        for token in ("btnUpload", "fileInput", "btnParse", "btnGenerate", "btnVerify"):
            self.assertIn(f'id="{token}"', _read(INDEX),
                          f"2.1 入口 {token} 被删除")

    def test_frozen_routes_not_reduced(self):
        for route in FROZEN_ROUTES:
            self.assertIn(route, self.routes, f"八项能力依赖的路由被删除：{route}")

    def test_parts_still_expand_inside_board(self):
        app = _read(APP_JS)
        for view in ("drawing-overview", "parts-list", "part-detail", "part-process", "part-cost"):
            self.assertIn(view, app, f"看板内部视图 {view} 缺失，零件不能在右侧展开")


if __name__ == "__main__":
    unittest.main()
