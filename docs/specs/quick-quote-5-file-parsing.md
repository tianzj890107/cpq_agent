# 规格：逆向快速报价 第 5 批 —— 文件解析接入与真实案例验收

状态：Spec + 红测（已实现）
红测：`tests/test_quick_quote_file_parsing_red.py`
依赖：批 1（案例模型）、批 2（候选检索）、批 3（工作区）、批 4（快速报价）。
**本批假设前四批已实现**。前三批之前**不得被 DWG 阻塞**（文字/Excel/PDF/图片先能用）。

## 0. 本批范围

让销售可以直接丢文件：文字需求 / Excel / PDF / 图片 → 直接快速报价；
DWG / DXF 放到本批接入，但**必须共享服务器的统一解析服务**，
**不允许在报价项目里再装第二套 ODA / LibreDWG**。

```
报价 Agent → 统一文件解析服务 → 结构化解析结果 → 快速报价案例匹配
```

分工（关键）：

| | 用途 | 需要什么 |
| --- | --- | --- |
| 报价快速通道 | 只提取**匹配所需字段** | 单位、标注尺寸、外形尺寸、盒型/结构特征、闭合方式、V 槽/磁铁/开窗、材料与文字标注、图层与块名、必要展开尺寸 |
| 技术工艺 | 完整几何、部件、证据与建模 | 复用同一底层解析能力，但取用范围更大 |

两侧共享同一个底层 DWG 解析能力（`tech_app/backend/services/cad_converter/` + 既有 ODA 部署），
报价侧只当**客户端**。

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| 仓库根 | `cpq_quick_quote_file.py` 不存在 |
| `cpq_agent_server.py:3500` | 有 `/api/extract`，但只覆盖 `_extract_text()` 支持的文本文档（pdf/docx/xlsx/txt/md/csv），**DWG/DXF 直接判"无法解析"** |
| `grep -l "dwg2dxf\|ODAFileConverter\|libredwg" cpq_*.py` | 报价侧目前**没有**任何 DWG 转换器直连（这是好现状，本批必须保持） |
| `DEPLOYMENT.md` | 没有 `CPQ_UNIFIED_PARSE_URL` 这一项，报价侧要往哪儿要解析结果没有登记 |
| `cpq_agent_server.py` | 没有 `/api/quick-quote/parse` 路由，也没有任何能力预检出口 |
| `docs/specs/dwg-file-capability-preflight.md` | 已经吃过一次"页面宣称支持、后端实际不支持"的亏（健康检查说能预览、实际不产预览）。本批要求报价侧**先把能力问清楚再解析**，能力不足时必须明确拒绝，不许静默降级 |

## 2. 契约

### 2.1 新模块 `cpq_quick_quote_file.py`

```python
ENGINE_VERSION = "quick_quote_file_v1"
INDUSTRY = "packaging"

#: 统一解析服务地址：唯一来源是环境变量，代码里只有一个默认值。
PARSE_URL_ENV = "CPQ_UNIFIED_PARSE_URL"
DEFAULT_PARSE_URL = "http://127.0.0.1:8010/api/file/parse"
PARSE_PATH = "/api/file/parse"
CAPABILITY_PATH = "/api/file/parse/capability"
QUICK_QUOTE_PARSE_PATH = "/api/quick-quote/parse"

DWG_EXTS = (".dwg", ".dxf")
DOC_EXTS = (".txt", ".md", ".csv", ".xlsx", ".xls", ".pdf", ".docx",
            ".png", ".jpg", ".jpeg")

#: 报价侧只要这些字段（技术工艺侧用更完整的几何/部件/证据）。
QUICK_FIELDS = ("units", "annotated_dimensions", "outline_size", "box_features",
                "closure_type", "v_groove", "magnet", "window",
                "material_notes", "text_annotations", "layers", "blocks",
                "unfolded_size")

#: 解析结果 → 批 2 匹配输入键的映射（单位一律换算到 mm）。
MATCH_INPUT_MAP = {"outline_size": ("inner_length", "inner_width", "inner_height"),
                   "box_features": ("box_type", "box_family"),
                   "closure_type": ("closure_type",), "v_groove": ("v_groove",),
                   "magnet": ("magnet",), "window": ("window",)}

class QuickQuoteFileError(Exception):
    """带用户可见文案的文件解析错误（空文件 / 格式不支持 / 内容损坏）。"""

class ParseServiceUnavailable(RuntimeError):
    """统一解析服务不可达（未部署 / 网络 / 5xx）。不回落空解析。"""

class ParseUnsupported(RuntimeError):
    """服务明确回答"这个格式我解析不了"（能力不足）。带 `.advice` 文案。"""

def parse_url() -> str
def capability(*, transport=None) -> dict
def parse_file(name, raw, *, transport=None) -> dict
def to_match_inputs(parsed, *, fallback=None) -> dict
def parse_and_match(name, raw, cases=None, *, transport=None, today=None,
                    weights=None) -> dict
```

- `parse_url()` 返回 `PARSE_URL_ENV` 指定的地址（未设则 `DEFAULT_PARSE_URL`）——
  解析服务地址只有一个来源，模块内其它位置不得再各自读环境变量。
- `transport` 是**可注入的 HTTP 客户端**：签名 `transport(url, payload=None) -> dict`
  （`payload=None` 表示 GET）。默认实现用 `urllib.request` 访问 `PARSE_URL_ENV` 或
  `DEFAULT_PARSE_URL`；离线测试一律注入，**绝不真发请求**。
- 本模块**不得** import `tech_app` / `cad_converter`，源码里不得出现
  `dwg2dxf` / `dwg2SVG` / `AppRun` / `ODAFileConverter` / `libredwg`
  （第二套 ODA 的红线，Spec §1）。

### 2.2 能力预检 `capability()`

```python
{"service": str, "provider": str, "provider_version": str,
 "dwg": bool, "dxf": bool, "preview": bool}
```

- 服务不可达 / 非 2xx / 返回体不是 dict → 抛 `ParseServiceUnavailable`（**不返回空能力表**）。
- 报价侧先在 UI/接口上如实展示 provider 与版本：上一次 DWG 事故就是"声称支持、实际不支持"，
  所以能力字段必须能被前端读到（见 §2.5 路由）。

### 2.3 解析 `parse_file(name, raw)`

按扩展名分两条路，**文档类不得依赖 DWG 服务**：

| 类别 | 行为 |
| --- | --- |
| `DOC_EXTS` | 走**既有** `/api/extract` 口径（复用 `cpq_agent_server._extract_text()`），返回 `{"kind": "document", "text": str, "chars": int, "truncated": bool, …}`；**不调用**统一解析服务 |
| `DWG_EXTS` | 先取能力；`capability["dwg"]` 为假 → `ParseUnsupported`（带 `.advice`：转人工或转精准报价）；为真 → POST `{"name", "data": base64, "fields": QUICK_FIELDS}`，返回 `{"kind": "drawing", "fields": {…QUICK_FIELDS 里的键…}, "missing_fields": [...], "service", "provider", "provider_version", …}` |
| 其它扩展名 | `QuickQuoteFileError`，文案点名支持的格式清单 |
| 空文件（`b""`） | `QuickQuoteFileError("空文件")`，**不发请求** |
| 服务 5xx / 网络异常 | `ParseServiceUnavailable` |

`fields` 请求体**只允许**包含 `QUICK_FIELDS`：报价快速通道不索取完整几何与部件证据。

### 2.4 映射到匹配输入 `to_match_inputs(parsed, fallback=None)`

```python
{"inputs": {批 2 的 QUICK_MATCH_INPUT_KEYS …},
 "missing": [key …],            # 解析没给出、需要人补的键
 "sources": {key: "parse"|"fallback"},}
```

规则：

1. 单位换算：`units` ∈ `mm / cm / m / inch` → 统一 × 1 / ×10 / ×1000 / ×25.4 到 mm；
   单位缺失时按 `mm` 处理并在 `warnings` 里记一条（不猜成别的单位）。
2. `outline_size` → `inner_length` / `inner_width` / `inner_height`；若解析结果里同时有
   标注内尺寸，内尺寸优先（外形尺寸只是兜底）。
3. `box_features` → `box_type` / `box_family`；`material_notes` 里能读出的克重
   （`200g` / `200 克` / `1200gsm`）→ `face_paper_gsm` / `grey_board_gsm`。
4. **不猜**：解析不出的键进 `missing`，绝不填默认值；`fallback`（销售手填）只在解析缺失时
   补齐，并在 `sources` 里标 `"fallback"`；解析值与 fallback 冲突时以**解析值**为准并记 warning。

### 2.5 HTTP 路由与部署登记

- `cpq_agent_server.py` 新增 `POST QUICK_QUOTE_PARSE_PATH`，处理函数 `_handle_quick_quote_parse()`：
  接收 base64 文件 + 名称，返回 `{"ok", "parse", "inputs", "missing", "sources",
  "capability", "match"}`；`capability` 段必须回传（前端据此显示"解析器版本 / 是否支持 DWG"）。
- 路由不得转调技术工艺项目接口（`/api/projects/…`），也不得自己转换 DWG。
- `DEPLOYMENT.md` 必须登记 `CPQ_UNIFIED_PARSE_URL`（含 34 上的取值口径），
  与既有 `DWG_CONVERTER_*` 一段并列。

### 2.6 真实案例验收

- 真实样本仍用客户只读样本 `裕同包装项目-待开发/酒盒.dwg`、`圆盘盒.dwg`（`AC1027`，不入库）。
- 服务在线时才跑真实解析；服务不在线必须 `skipTest` 并**在原因里点名缺什么**
  （沿用 `tests/test_dwg_real_samples_e2e_red.py` 的纪律：静默跳过等于把"没跑过"包装成"通过"）。
- 无论服务是否在线，都必须能断言：真实样本存在且文件头可读、报价侧无 ODA 直连。

## 3. 红测映射（`tests/test_quick_quote_file_parsing_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：常量、只索取 `QUICK_FIELDS`、不 import 技术工艺、仓库根 `cpq_*.py` 无 ODA 直连 |
| B | 能力预检：字段齐、服务不可用抛 `ParseServiceUnavailable`、能力为假时 `ParseUnsupported` 带建议 |
| C | 正确失败：空文件 / 未知扩展名 / 服务 5xx，且都不发无用请求、不返回空解析 |
| D | 文档路径：文档类走既有 `/api/extract`，**不调用**统一解析服务 |
| E | 映射：单位换算、外形→内尺寸、特征→盒型/闭合/结构、材料克重、`missing` 不猜、`fallback` 标来源 |
| F | 端到端：`parse_and_match()` 串起解析 → 匹配候选；0 候选给转精准建议 |
| G | 路由与部署：`/api/quick-quote/parse` + 能力段 + `DEPLOYMENT.md` 登记 env |
| H | 真实样本：样本存在与文件头、服务不在线时点名缺什么、报价侧无第二套转换器 |

## 4. 本批非目标

- 不在报价项目里安装/维护第二套 ODA 或 LibreDWG；
- 不复制技术工艺的 DWG 解析器实现（只当客户端）；
- 不改技术工艺侧的解析口径与几何/部件/证据产出；
- 不做精准成本核算、BOM 重建、工艺生成、审批与报告回传。
