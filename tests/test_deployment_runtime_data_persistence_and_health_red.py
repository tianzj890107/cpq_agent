"""RED contract for CPQ Docker persistence and complete deployment readiness."""

from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DEPLOY = (ROOT / "scripts" / "deploy_server.sh").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "tech_app_launch.py").read_text(encoding="utf-8")
IMAGE_SERVER = (ROOT / "cpq_image_server.py").read_text(encoding="utf-8")


class DeploymentRuntimeDataPersistenceAndHealthRedTest(unittest.TestCase):
    def test_tech_runtime_data_has_exact_bind_mount(self):
        self.assertIn(
            'os.path.join(TECH_APP_DIR, "tech_data")',
            LAUNCHER,
            "守护：技术工艺运行目录仍应是 /app/tech_app/tech_data",
        )
        self.assertRegex(
            COMPOSE,
            r"(?m)^\s*-\s*\./tech_app/tech_data:/app/tech_app/tech_data\s*$",
            "docker-compose 必须把宿主 tech_app/tech_data 绑定到容器同一路径",
        )

    def test_product_images_have_exact_bind_mount(self):
        self.assertRegex(
            IMAGE_SERVER,
            r'IMAGE_DIR\s*=\s*os\.path\.join\(SCRIPT_DIR,\s*"product_images"\)',
            "守护：产品图片运行目录仍应是 /app/product_images",
        )
        self.assertRegex(
            COMPOSE,
            r"(?m)^\s*-\s*\./product_images:/app/product_images\s*$",
            "docker-compose 必须把宿主 product_images 绑定到容器同一路径",
        )

    def test_deploy_preflight_guards_both_runtime_directories(self):
        compose_up = DEPLOY.find("docker compose up")
        self.assertGreater(compose_up, 0, "部署脚本缺少 docker compose up")
        preflight = DEPLOY[:compose_up]
        for path in ("tech_app/tech_data", "product_images"):
            with self.subTest(path=path):
                self.assertIn(path, preflight, f"部署前必须检查持久化目录 {path}")
        self.assertNotRegex(
            preflight,
            r"mkdir\s+(?:-[A-Za-z]*p[A-Za-z]*\s+)?[^\n]*(?:tech_data|product_images)",
            "部署脚本不得用空目录掩盖运行数据缺失",
        )

    def test_deploy_waits_for_tech_health_json_not_only_root(self):
        self.assertIn(
            "/api/health",
            DEPLOY,
            "部署成功前必须请求技术工艺 /api/health",
        )
        self.assertRegex(
            DEPLOY,
            r"status.{0,80}ok|ok.{0,80}status",
            "部署脚本必须校验 /api/health JSON 的 status == ok",
        )
        self.assertIn(
            'http://127.0.0.1:${deploy_port}/',
            DEPLOY,
            "父服务首页检查必须保留",
        )

    def test_compose_declares_tech_readiness_healthcheck(self):
        self.assertRegex(COMPOSE, r"(?m)^\s*healthcheck:\s*$")
        self.assertIn("127.0.0.1:8010/api/health", COMPOSE)
        self.assertRegex(
            COMPOSE,
            r"status.{0,120}ok|ok.{0,120}status",
            "Compose healthcheck 必须校验 status == ok，不能只判断端口可连接",
        )


if __name__ == "__main__":
    unittest.main()
