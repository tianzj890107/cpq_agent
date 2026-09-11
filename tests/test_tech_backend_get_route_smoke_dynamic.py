"""后端 GET 路由冒烟：任何 /api 的读接口都不允许 5xx。

背景：本仓库的后端回归基本是文本 grep 型红测 + `py_compile`，两者都只验证“代码里
写了什么”，不验证“真的能被调用”。`tech_app/backend/main.py` 就出现过
`requirement_service` 从未导入（11 处引用 → 一发请求 500）和 `recommend_costest`
漏掉 `dependency_hash` 赋值（同样即发 500）这两类缺陷，静态测试全绿、真实请求全挂。

这里用 FastAPI 自带的 TestClient 把**每一个** `GET /api/**` 路由都真打一遍：
先建一个临时 `DATA_DIR`、塞一个最小项目，再把路径里的 `{project_id}` 换成该项目、
其余路径参数换成哑值，最后断言没有任何 5xx。读接口理应 200 / 404 / 400，出现 500
就说明 handler 里存在 NameError / AttributeError / 未处理异常。

只探 GET：写接口有副作用且需要请求体，不适合无脑冒烟，单独由各自的专项测试覆盖。
缺 FastAPI/Starlette 的解释器（如系统 python3）自动跳过。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# import tech_app.backend.main 之前必须先钉住 DATA_DIR：config 在导入期就读取它，
# 否则冒烟会落到真实数据目录。（目前没有别的测试导入 main，这里仍显式设置以防万一。）
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="cpq-get-smoke-")
os.environ["DATA_DIR"] = _TMP_DATA_DIR
os.environ["AUTH_ENABLED"] = "false"

# 其余非 project_id 的路径参数：读接口对不存在的 ID 应当 404/400，而不是炸成 500。
DUMMY_PARAMS = {
    "filename": "no-such-file.png",
    "part_id": "no-such-part",
    "task_id": "no-such-task",
    "version": "1",
    "v_from": "1",
    "v_to": "2",
}


def _load_app():
    """返回 (TestClient, app, store)；缺依赖时返回 None。"""
    try:
        from starlette.testclient import TestClient
    except Exception:  # pragma: no cover - 取决于解释器是否装了 FastAPI 依赖
        return None
    from tech_app.backend.storage import store
    import tech_app.backend.main as main

    return TestClient(main.app, raise_server_exceptions=False), main.app, store


class GetRouteSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded = _load_app()
        if loaded is None:
            raise unittest.SkipTest("缺少 FastAPI/Starlette 依赖，跳过 GET 路由冒烟")
        cls.client, cls.app, cls.store = loaded
        cls.project_id = cls.store.create_project(
            "smoke.png", b"\x89PNG\r\n\x1a\n", owner="smoke",
        )
        # 只建项目还不够：/requirement/precheck 之类接口在“没有需求单”时会提前 404，
        # 于是根本不执行到 service 调用。必须把文档也种上，冒烟才不是空跑。
        cls.store.save_requirement(cls.project_id, {
            "project_id": cls.project_id,
            "requirement_no": "REQ-SMOKE",
            "title": "冒烟用需求单",
            "status": "draft",
            "data": {},
            "created_by": "smoke",
            "history": [],
        }, author="smoke")
        cls.paths = sorted({
            getattr(route, "path", "")
            for route in cls.app.routes
            if "GET" in (getattr(route, "methods", None) or set())
            and getattr(route, "path", "").startswith("/api")
        })

    def _url(self, path: str) -> str:
        url = path
        for name in re.findall(r"\{([^}]+)\}", path):
            url = url.replace(
                "{" + name + "}",
                self.project_id if name == "project_id" else DUMMY_PARAMS.get(name, "x"),
            )
        return url

    def test_scan_covers_the_api_surface(self):
        # 空 glob / 路由表读不到都不能让下面那条断言 vacuous 通过。
        self.assertGreaterEqual(len(self.paths), 50, self.paths)
        for expected in ("/api/projects/{project_id}/requirement",
                         "/api/projects/{project_id}/costest",
                         "/api/projects/{project_id}/agent/meta"):
            self.assertIn(expected, self.paths)

    def test_no_get_route_returns_5xx(self):
        failures = []
        for path in self.paths:
            response = self.client.get(self._url(path))
            if response.status_code >= 500:
                failures.append(f"{response.status_code} {path}")
        self.assertEqual(
            failures, [],
            "以下 GET 接口在最小数据集上直接 5xx（多为 NameError / 未处理异常）：\n  "
            + "\n  ".join(failures),
        )


if __name__ == "__main__":
    unittest.main()
