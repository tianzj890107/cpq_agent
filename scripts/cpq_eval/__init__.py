# -*- coding: utf-8 -*-
"""CPQ 业务回归数据集与离线执行器（``dataset/evals/cpq`` + ``scripts/cpq_eval``）。

定位：**只做测试脚手架**。加载 / 校验 / 执行 / 汇总仓库内的业务回归数据集，不改任何
业务实现，不访问真实数据库、真实模型、PDT 与线上服务。

口径（本包内唯一事实源，全部引用仓库现有实现）：
  * 报价六步：直接读 ``cpq_wf.QUOTE_STEPS``（1 确认需求配置 / 2 工艺确认 /
    3 定价-利润加成 / 4 报价-其他加价项 / 5 报价方案 / 6 输出报价单）。
  * 技术工艺五阶段 13 子步骤：直接读 ``tech_app/backend/services/workflow_stages.py``
    的 ``PHASES`` / ``STAGES``（该模块是纯数据表，无需 pydantic / fastapi）。
  * 任务类型：``cpq_wf.TASK_KIND_*``。
  * 跨系统业务实例关联：``cpq_case_link`` 的纯函数（``decide`` / ``dedupe_candidates``）。

禁止：本包不得 import 需要真实数据库连接的模块，不得读取 home 目录下的历史会话，
不得把运行产物写进仓库（报告默认写临时目录）。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATASET_ROOT = ROOT / "dataset" / "evals" / "cpq"
CASES_DIR = DATASET_ROOT / "cases"
FIXTURES_DIR = DATASET_ROOT / "fixtures"
SCHEMAS_DIR = DATASET_ROOT / "schemas"
REPORTS_DIR = DATASET_ROOT / "reports"

DOMAINS = (
    "quote", "tech", "cross_agent", "auth_acl", "session_history",
    "concurrency", "llm_contract", "failure_recovery", "ui_protocol",
)
PRIORITIES = ("P0", "P1", "P2")
# layer：案例数据的**离线/联网属性**（历史字段，保持兼容，已有 242 条案例不改）。
LAYERS = ("deterministic", "recorded_provider", "integration")

# executor：**案例由谁执行**。这是发布门禁的唯一口径 —— simulation 通过不能抵消
# production-backed 失败，也**不允许**从 layer 字段推断出生产层（防止靠改数据字段
# 虚增生产覆盖率）。见 dataset/evals/cpq/README.md「执行分层」。
#
#   · specification_only  —— 没有可执行步骤，只固化规范 / 期望，不算「执行过」；
#   · simulation          —— 由 runner.Sim 的受控假库状态机执行，不计入发布门禁；
#   · production_unit     —— 直接调用真实生产函数；
#   · production_http     —— 真实 FastAPI app / TestClient + 真鉴权依赖；
#   · recorded_provider   —— 回放固定 provider 响应并进入真实 Agent / 工具分发边界；
#   · postgres_integration—— 隔离 PostgreSQL：唯一约束 / 事务 / 原子领取（默认跳过）。
EXECUTORS = ("specification_only", "simulation", "production_unit", "production_http",
             "recorded_provider", "postgres_integration")
# 历史上用过的名字 → 现名（只做只读兼容，案例应写现名）。
EXECUTOR_ALIASES = {"spec_simulation": "simulation"}
# 需要真实生产代码边界的层（发布门禁只认这几层的 P0）。
PRODUCTION_EXECUTORS = ("production_unit", "production_http", "recorded_provider",
                        "postgres_integration")
# 计入「production-backed 通过率」的三层（postgres_integration 默认 skip，单独统计）。
GATE_EXECUTORS = ("production_unit", "production_http", "recorded_provider")
ROUTE_SNAPSHOT = DATASET_ROOT / "routes" / "project_routes.json"
WRITE_ROUTE_POLICY = DATASET_ROOT / "routes" / "write_route_policy.json"


def _raw_of(case):
    if isinstance(case, dict):
        return case
    raw = getattr(case, "raw", None)
    return raw if isinstance(raw, dict) else {}


def executor_of(case) -> str:
    """案例由谁执行。

    规则（唯一口径，**不看 layer**）：
      1. 显式 ``executor`` 字段优先（旧名经 ``EXECUTOR_ALIASES`` 归一）；
      2. 没有 ``executor`` 但声明了 ``actions`` → ``simulation``（Sim 状态机真的会跑）；
      3. 其余（既没有 executor 也没有可执行步骤）→ ``specification_only``。

    这样「改 layer 字段」不能把案例算进生产层；而声明了生产层却没走生产入口的案例
    会被 ``checks`` 的声明一致性门禁直接判不合法。
    """
    raw = _raw_of(case)
    value = str(raw.get("executor") or "").strip()
    if value:
        return EXECUTOR_ALIASES.get(value, value)
    if raw.get("actions"):
        return "simulation"
    return "specification_only"

def covers_routes(case) -> list:
    return [str(item) for item in (_raw_of(case).get("covers_routes") or [])]

# 角色（数据集里的统一写法）。CPQ 口径与技术工艺口径的并集见 tech-project-acl-visible-scope。
ROLES = (
    "sales_mgr", "process_mgr", "process_engineer", "finance_mgr",
    "reviewer", "admin", "viewer",
)

# 失败类型（覆盖矩阵用，闭集）。
FAILURE_TYPES = (
    "none",
    "permission_denied",
    "missing_prerequisite",
    "duplicate_request",
    "concurrency_conflict",
    "transaction_rollback",
    "provider_error",
    "invalid_output",
    "not_found",
    "timeout",
    "refresh_failure",
    "stale_upstream",
    "auth_failure",
    "data_leak_attempt",
    "ui_protocol_violation",
)

# ---- 技术工艺：5 阶段 × 13 子步骤（与 workflow_stages.py 逐行一致） ----
PHASES = (
    {"no": "1", "title": "工艺评估需求", "subs": ("1.1", "1.2", "1.3")},
    {"no": "2", "title": "图纸解析", "subs": ("2.1",)},
    {"no": "3", "title": "组装与整合", "subs": ("3.1", "3.2", "3.3")},
    {"no": "4", "title": "成本测算", "subs": ("4.1", "4.2", "4.3")},
    {"no": "5", "title": "工艺评估报告", "subs": ("5.1", "5.2", "5.3")},
)

_STAGE_ROWS = (
    ("requirement-create", "1", "工艺评估需求", "1.1", "创建需求", "", "requirement-create.html"),
    ("requirement-confirm", "1", "工艺评估需求", "1.2", "确认需求", "", "requirement-confirm.html"),
    ("requirement-review", "1", "工艺评估需求", "1.3", "审核需求", "", "requirement-review.html"),
    ("drawing", "2", "图纸解析", "2.1", "图纸解析", "", "index.html"),
    ("process", "3", "组装与整合", "3.1", "整合图纸", "drawings", "assembly-integration.html"),
    ("process", "3", "组装与整合", "3.2", "参数推荐", "params", "assembly-integration.html"),
    ("process", "3", "组装与整合", "3.3", "组装工艺", "process", "assembly-integration.html"),
    ("cost", "4", "成本测算", "4.1", "零件成本", "parts", "cost-review.html"),
    ("cost", "4", "成本测算", "4.2", "组装成本", "assembly", "cost-review.html"),
    ("cost", "4", "成本测算", "4.3", "汇总", "total", "cost-review.html"),
    ("summary", "5", "工艺评估报告", "5.1", "汇总结果", "", "summary.html"),
    ("report-review", "5", "工艺评估报告", "5.2", "结果审核", "", "report-review.html"),
    ("report-publish", "5", "工艺评估报告", "5.3", "发布并回传报价", "", "report-publish.html"),
)

STAGES = tuple(
    {"stage_id": sid, "phase": phase, "phase_title": phase_title, "sub": sub,
     "sub_title": sub_title, "view": view, "page": page,
     "key": f"{sub} {sub_title}"}
    for sid, phase, phase_title, sub, sub_title, view, page in _STAGE_ROWS
)

STAGE_IDS = tuple(dict.fromkeys(row["stage_id"] for row in STAGES))
MULTI_SUB_STAGES = ("process", "cost")
SUB_STEPS = tuple(row["sub"] for row in STAGES)
STAGE_BY_SUB = {row["sub"]: row for row in STAGES}
SUB_BY_STAGE = {row["stage_id"]: row["sub"] for row in STAGES if row["stage_id"] not in MULTI_SUB_STAGES}


def phase_title(no: str) -> str:
    for row in PHASES:
        if row["no"] == str(no):
            return row["title"]
    return ""


def stage_title(stage_id: str) -> str:
    """用户可见称呼：跨子步骤的 stage 用「阶段号 阶段标题」，其余用「子步骤号 子标题」。

    与 ``workflow_stages.stage_title`` 同一规则；查不到返回空串，不退回别的步骤。
    """
    row = None
    for item in STAGES:
        if item["stage_id"] == stage_id:
            row = item
            break
    if row is None:
        return ""
    if row["stage_id"] in MULTI_SUB_STAGES:
        return f"{row['phase']} {row['phase_title']}"
    return f"{row['sub']} {row['sub_title']}"


# ---- 报价六步：直接引用 cpq_wf ----
_FALLBACK_QUOTE_STEPS = (
    (1, "确认需求配置", "sales_mgr"),
    (2, "工艺确认", "process_mgr"),
    (3, "定价-利润加成", "sales_mgr"),
    (4, "报价-其他加价项", "sales_mgr"),
    (5, "报价方案", "sales_mgr"),
    (6, "输出报价单", "sales_mgr"),
)
QUOTE_STEPS_SOURCE = "cpq_wf.QUOTE_STEPS"
try:  # pragma: no cover - 只有 cpq_wf 不可导入时才走 fallback
    import cpq_wf as _cpq_wf

    QUOTE_STEPS = tuple(tuple(row) for row in _cpq_wf.QUOTE_STEPS)
    TASK_KINDS = tuple(_cpq_wf.TASK_KINDS)
except Exception:  # pragma: no cover
    QUOTE_STEPS_SOURCE = "fallback（cpq_wf 不可导入）"
    QUOTE_STEPS = _FALLBACK_QUOTE_STEPS
    TASK_KINDS = ("handoff", "tech_new_product", "tech_cost", "tech_cost_return")

QUOTE_STEP_TITLES = tuple(title for _no, title, _role in QUOTE_STEPS)
QUOTE_ROLE_BY_STEP = {int(no): role for no, _title, role in QUOTE_STEPS}


def load_real_stage_rows() -> tuple:
    """按文件路径加载 ``workflow_stages.py`` 的 ``STAGES``（纯数据模块，不需要 pydantic）。"""
    path = ROOT / "tech_app" / "backend" / "services" / "workflow_stages.py"
    spec = importlib.util.spec_from_file_location("cpq_eval_real_workflow_stages", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(dict(row) for row in module.STAGES)


# ---- 旧口径文案（数据集里禁止出现，历史 fixture 例外） ----
LEGACY_PATTERNS = {
    "九阶段": re.compile(r"九\s*阶段"),
    "第N大步": re.compile(r"第\s*[0-9一二三四五六七八九十]+\s*大步"),
    "2.2组装与整合": re.compile(r"2\.2\s*组装与整合"),
    "2.3成本测算": re.compile(r"2\.3\s*成本测算"),
    "3.1汇总结果": re.compile(r"3\.1\s*汇总结果"),
    "3.2审核": re.compile(r"3\.2\s*(结果审核|审核)"),
    "3.3发布": re.compile(r"3\.3\s*(发布报告|发布并回传报价|发布)"),
    "三阶段页签行": re.compile(r"2\.1\s*图纸解析\s*→\s*2\.2"),
    "旧报告子步骤": re.compile(r"3\.3\s*发布"),
}


def find_legacy_wording(text: str) -> list:
    """返回文本里命中的旧口径名称列表（空列表代表合规）。"""
    if not isinstance(text, str) or not text:
        return []
    return [name for name, pattern in LEGACY_PATTERNS.items() if pattern.search(text)]
