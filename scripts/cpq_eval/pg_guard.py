# -*- coding: utf-8 -*-
"""`postgres_integration` 层**父进程与子进程共用**的安全连接合同。

为什么要单独一个模块：父层（`pg_integration`）与子层（`pg_scenarios`）如果各写一份
host 白名单，迟早出现「父层允许、子层拒绝」或反过来的矛盾。这里只定义一次，两边都调
`guard()`。

合同（必须同时满足）：

1. 必须显式设置 `CPQ_EVAL_INTEGRATION=1`；
2. 只读 `CPQ_EVAL_PG_*`，**绝不回退**到生产用的 `CPQ_PG_*`；
3. host 只能是回环地址，或在「CI 标志 + service alias 白名单」同时成立时才允许的
   GitLab service container 别名；
4. maintenance database 必须在显式白名单里（默认 `postgres`）；
5. 临时库名必须匹配 `^cpq_eval_it_[0-9a-f]{10}$`；
6. 任何生产 / PDT / 准生产特征出现在 host / port / user / database 名字里都立即拒绝。
"""
from __future__ import annotations

import os
import re

ENV_PREFIX = "CPQ_EVAL_PG_"
INTEGRATION_ENV = "CPQ_EVAL_INTEGRATION"
CI_FLAG_ENV = "CPQ_EVAL_PG_CI"
SERVICE_HOSTS_ENV = "CPQ_EVAL_PG_SERVICE_HOSTS"
MAINTENANCE_DBS_ENV = "CPQ_EVAL_PG_MAINTENANCE_DB_ALLOWLIST"

# 生产 / PDT / 准生产的特征（出现即拒绝，宁可拒绝也不连错库）
PRODUCTION_MARKERS = (
    "pdt", "prod", "production", "172.16.10.34", "172.16.5.181", ":8010", "/8010",
    "metabase", "亿纬", "yeewei",
)
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0")
DEFAULT_SERVICE_HOSTS = ("cpq-eval-pg",)
DEFAULT_MAINTENANCE_DBS = ("postgres",)

TEMP_DB_PREFIX = "cpq_eval_it_"
# 父层兜底清理只认这个格式，禁止模糊匹配批量 DROP。
TEMP_DB_RE = re.compile(r"^cpq_eval_it_[0-9a-f]{10}$")
# GitLab service alias：只能是 DNS 名，不能是 IP。
SERVICE_ALIAS_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class PgGuardError(Exception):
    """连接参数不安全 / 环境不满足（跳过或判非法，绝不"连上再说"）。"""


def integration_enabled() -> bool:
    return str(os.environ.get(INTEGRATION_ENV) or "") == "1"


def ci_flag() -> bool:
    """CI 环境必须有**显式**标志，不靠泛化 CI 变量推断。"""
    return str(os.environ.get(CI_FLAG_ENV) or "") == "1"


def service_hosts() -> tuple:
    raw = str(os.environ.get(SERVICE_HOSTS_ENV) or "")
    if not raw.strip():
        return DEFAULT_SERVICE_HOSTS
    out = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return out or DEFAULT_SERVICE_HOSTS


def maintenance_dbs() -> tuple:
    raw = str(os.environ.get(MAINTENANCE_DBS_ENV) or "")
    if not raw.strip():
        return DEFAULT_MAINTENANCE_DBS
    out = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return out or DEFAULT_MAINTENANCE_DBS


def _contains_marker(value: str) -> str:
    text = str(value or "").lower()
    for marker in PRODUCTION_MARKERS:
        if marker.lower() in text:
            return marker
    return ""


def allowed_hosts_label() -> str:
    hosts = list(LOOPBACK_HOSTS)
    if ci_flag():
        hosts += [f"{name}（CI service alias）" for name in service_hosts()]
    return " / ".join(hosts)


def guard(cfg: dict, *, require_integration: bool = True) -> dict:
    """校验连接参数；不通过抛 `PgGuardError`，通过返回连接模式信息。"""
    host = str((cfg or {}).get("host") or "").strip()
    if not host:
        raise PgGuardError(f"未配置 {ENV_PREFIX}HOST（本层只认 {ENV_PREFIX}* 变量，"
                           f"不读生产 CPQ_PG_*）")
    if require_integration and not integration_enabled():
        raise PgGuardError(f"未显式开启 {INTEGRATION_ENV}=1，拒绝连接任何数据库")

    named = {
        "host": host,
        "port": str((cfg or {}).get("port") or ""),
        "user": str((cfg or {}).get("user") or ""),
        "maintenance_db": str((cfg or {}).get("maintenance_db") or ""),
        "maintenance database": str((cfg or {}).get("maintenance_db") or ""),
    }
    # 数据库名（临时库 / 应用连接库）也要查生产特征
    for key in ("database", "child_db"):
        if (cfg or {}).get(key):
            named[key] = str(cfg[key])
    for name, value in named.items():
        marker = _contains_marker(value)
        if marker:
            raise PgGuardError(f"{name}={value!r} 命中生产/PDT 特征 {marker!r}，拒绝运行")

    key = host.strip().lower()
    if key in LOOPBACK_HOSTS:
        mode = "loopback"
    else:
        if not ci_flag():
            raise PgGuardError(
                f"host={key!r} 不是回环地址；本层只允许本机，或 CI 标志 "
                f"{CI_FLAG_ENV}=1 下的 service alias 白名单（当前允许：{allowed_hosts_label()}）")
        if key not in service_hosts():
            raise PgGuardError(
                f"host={key!r} 不在 service alias 白名单 {list(service_hosts())} 内，拒绝运行")
        if not SERVICE_ALIAS_RE.match(key):
            raise PgGuardError(f"host={key!r} 不是合法 service alias（只允许 DNS 名，禁止 IP）")
        mode = "ci_service"

    maintenance = str((cfg or {}).get("maintenance_db") or "").strip().lower()
    if maintenance and maintenance not in maintenance_dbs():
        raise PgGuardError(
            f"maintenance database={maintenance!r} 不在白名单 {list(maintenance_dbs())} 内，拒绝运行")

    child_db = str((cfg or {}).get("child_db") or "").strip()
    if child_db and not TEMP_DB_RE.match(child_db):
        raise PgGuardError(f"临时库名 {child_db!r} 不符合 {TEMP_DB_PREFIX}<10 hex> 约定")
    database = str((cfg or {}).get("database") or "").strip()
    if database and not TEMP_DB_RE.match(database):
        raise PgGuardError(f"应用连接的数据库 {database!r} 不是本轮创建的临时库，拒绝运行")

    return {"mode": mode, "host": key, "service_hosts": list(service_hosts()),
            "maintenance_db": maintenance, "allowed_hosts": allowed_hosts_label()}
