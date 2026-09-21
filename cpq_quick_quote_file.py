"""逆向快速报价 第 5 批：文件解析接入（报价侧只当客户端）。

Spec：`docs/specs/quick-quote-5-file-parsing.md`
红测：`tests/test_quick_quote_file_parsing_red.py`

分工（不许越界）：

  · 报价快速通道只要**匹配所需字段**（`QUICK_FIELDS`），向**统一解析服务**要结果；
  · 报价侧**不装第二套** ODA / LibreDWG，也不 import 技术工艺的解析实现 ——
    转换器只住在技术工艺侧，这里是纯 HTTP 客户端；
  · 文档类（txt/xlsx/pdf/docx/图片）走既有 `/api/extract`（`cpq_agent_server._extract_text`），
    **不依赖** DWG 服务：DWG 没就绪不该拖死文字需求；
  · 解析不出的字段进 `missing`，**绝不填默认值**；能力不足时明确拒绝并给建议，
    不静默降级成"空解析"。
"""
from __future__ import annotations

import base64
import copy
import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

import cpq_quick_quote_match as qq_match

ENGINE_VERSION = "quick_quote_file_v1"
INDUSTRY = "packaging"

#: 统一解析服务地址：唯一来源是环境变量，代码里只有一个默认值（Spec §2.1）。
PARSE_URL_ENV = "CPQ_UNIFIED_PARSE_URL"
DEFAULT_PARSE_URL = "http://127.0.0.1:8010/api/file/parse"
PARSE_PATH = "/api/file/parse"
CAPABILITY_PATH = "/api/file/parse/capability"
QUICK_QUOTE_PARSE_PATH = "/api/quick-quote/parse"

DWG_EXTS = (".dwg", ".dxf")
DOC_EXTS = (".txt", ".md", ".csv", ".xlsx", ".xls", ".pdf", ".docx",
            ".png", ".jpg", ".jpeg")

#: 报价快速通道只索取这些字段（技术工艺侧才要完整几何 / 部件 / 证据）。
QUICK_FIELDS = ("units", "annotated_dimensions", "outline_size", "box_features",
                "closure_type", "v_groove", "magnet", "window",
                "material_notes", "text_annotations", "layers", "blocks",
                "unfolded_size")

#: 解析结果 → 批 2 匹配输入键的映射（单位一律换算到 mm）。
MATCH_INPUT_MAP = {"outline_size": ("inner_length", "inner_width", "inner_height"),
                   "box_features": ("box_type", "box_family"),
                   "closure_type": ("closure_type",), "v_groove": ("v_groove",),
                   "magnet": ("magnet",), "window": ("window",)}

#: 批 2 的匹配输入闭集（唯一事实源在 cpq_quick_quote_match）。
MATCH_INPUT_KEYS = tuple(qq_match.QUICK_MATCH_INPUT_KEYS)

#: 尺寸轴名 → 匹配键（标注尺寸用轴名，外形尺寸按长/宽/高顺序兜底）。
#: 图纸幅面类来源（Spec 批 9 §1.1）：这些 `outline_size` 是**整张图的幅面**，不是成品内尺寸，
#: 一律不得当内尺寸用 —— 否则 14362×6152 的图框会被当成盒子的内宽/内高（`## 250` 实测）。
SHEET_SIZE_SOURCES = ("document_extents",)

_AXIS_KEYS = {"inner_length": "inner_length", "inner_width": "inner_width",
              "inner_height": "inner_height", "length": "inner_length",
              "width": "inner_width", "height": "inner_height"}
_UNIT_FACTORS = {"mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
                 "cm": 10.0, "m": 1000.0, "inch": 25.4, "in": 25.4,
                 "英寸": 25.4, "毫米": 1.0, "厘米": 10.0}
_GREY_WORDS = ("灰板", "纸板", "greyboard", "grey", "gray")
_FACE_WORDS = ("面纸", "面", "face", "cover")
_GSM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:g/m²|g/m2|gsm|g|克)")

_text = qq_match._text
_num = qq_match._num


class QuickQuoteFileError(Exception):
    """带用户可见文案的文件解析错误（空文件 / 格式不支持 / 内容损坏）。"""

    def __init__(self, message: str, status_code: int = 400, code: str = ""):
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = code or ""


class ParseServiceUnavailable(RuntimeError):
    """统一解析服务不可达（未部署 / 网络 / 5xx）。**不回落空解析**。"""


class ParseUnsupported(QuickQuoteFileError):
    """服务明确回答「这个格式我解析不了」（能力不足）：带 `.advice` 给用户可见建议。"""

    def __init__(self, message: str, *, advice: str = "", code: str = "parse_unsupported"):
        super().__init__(message, 409, code)
        self.advice = advice or ("该图纸暂时解析不了：请转人工处理，或直接转精准报价，"
                                 "由工艺同事按图纸录入尺寸。")


# --------------------------------------------------------------------------- #
# 统一解析服务客户端
# --------------------------------------------------------------------------- #
def parse_url() -> str:
    """统一解析服务地址（每次读环境变量，部署时改 env 即可，代码里只有这一个默认值）。"""
    return (_text(os.environ.get(PARSE_URL_ENV)) or DEFAULT_PARSE_URL)


def capability_url() -> str:
    base = parse_url()
    if base.endswith(PARSE_PATH):
        return base[: -len(PARSE_PATH)] + CAPABILITY_PATH
    return base.rstrip("/") + CAPABILITY_PATH


def _http_transport(url: str, payload: Optional[dict] = None) -> Any:
    """默认 HTTP 客户端：GET（payload=None）/ POST JSON。非 2xx 一律报错，不静默降级。"""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=headers,
                                    method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=120) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ParseServiceUnavailable("统一解析服务返回的不是 JSON（%s）：%s"
                                      % (url, str(exc)[:120]))


def _fetch(url, payload=None, *, transport=None):
    fetch = transport or _http_transport
    try:
        return fetch(url, payload) if payload is not None else fetch(url)
    except (ParseServiceUnavailable, ParseUnsupported, QuickQuoteFileError):
        raise
    except Exception as exc:                                   # noqa: BLE001 - 统一收敛成不可达
        raise ParseServiceUnavailable("统一解析服务不可达（%s）：%s"
                                      % (url, str(exc)[:200])) from exc


def capability(*, transport=None) -> dict:
    """能力预检（Spec §2.2）：`dwg` 为假时上游必须明确拒绝，不许硬着头皮解析。"""
    url = capability_url()
    got = _fetch(url, None, transport=transport)
    if not isinstance(got, dict):
        raise ParseServiceUnavailable("统一解析服务能力表格式不对（%s 返回 %s）："
                                      "能力不明时不得当成支持"
                                      % (url, type(got).__name__))
    out = {"service": _text(got.get("service")),
           "provider": _text(got.get("provider")),
           "provider_version": _text(got.get("provider_version")),
           "dwg": bool(got.get("dwg")), "dxf": bool(got.get("dxf")),
           "preview": bool(got.get("preview"))}
    for key in ("detail", "checked_at", "notes"):
        if got.get(key):
            out[key] = got[key]
    return out


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def _ext(name: str) -> str:
    return os.path.splitext(_text(name))[1].lower()


def parse_file(name, raw, *, transport=None) -> dict:
    """按扩展名分两条路（Spec §2.3）：文档走既有 `/api/extract`，DWG/DXF 走统一解析服务。"""
    ext = _ext(name)
    data = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw or b"")
    if not data:
        raise QuickQuoteFileError("空文件：%s 里没有任何内容，请重新导出后再传" % (_text(name) or "该文件"))
    if ext in DWG_EXTS:
        return _parse_drawing(_text(name), bytes(data), transport=transport)
    if ext in DOC_EXTS:
        return _parse_document(_text(name), bytes(data))
    raise QuickQuoteFileError("暂不支持 %s 格式（%s）：本通道支持 %s"
                              % (ext or "无扩展名", _text(name) or "未命名文件",
                                 "、".join(list(DWG_EXTS) + list(DOC_EXTS))))


def _parse_document(name: str, data: bytes) -> dict:
    import cpq_agent_server                      # 延迟 import：文档路径复用既有抽取口径
    text, error = cpq_agent_server._extract_text(name, data)
    if not text:
        raise QuickQuoteFileError("文档内容读不出来（%s）：%s"
                                  % (name, _text(error) or "可能是加密 / 扫描件 / 空文件，请换一份"),
                                  400, "extract_failed")
    text = str(text)
    return {"kind": "document", "name": name, "text": text, "chars": len(text),
            "truncated": False, "engine_version": ENGINE_VERSION}


def _parse_drawing(name: str, data: bytes, *, transport=None) -> dict:
    cap = capability(transport=transport)
    if not cap.get("dwg"):
        raise ParseUnsupported(
            "统一解析服务当前不支持 DWG（provider=%s，版本=%s）：%s"
            % (cap.get("provider") or "未配置", cap.get("provider_version") or "未配置",
               cap.get("detail") or "DWG 转换器未就绪"))
    payload = {"name": name, "data": base64.b64encode(data).decode("ascii"),
               "fields": list(QUICK_FIELDS)}
    got = _fetch(parse_url(), payload, transport=transport)
    if not isinstance(got, dict):
        raise ParseServiceUnavailable("解析服务返回体不是 dict（%s）：%s"
                                      % (parse_url(), type(got).__name__))
    fields = {}
    for key in QUICK_FIELDS:
        value = got.get(key, got.get("fields", {}).get(key) if isinstance(got.get("fields"), dict) else None)
        fields[key] = copy.deepcopy(value)
    return {"kind": "drawing", "name": name, "fields": fields,
            "missing_fields": [key for key in QUICK_FIELDS if _blank(fields.get(key))],
            "service": cap.get("service"), "provider": cap.get("provider"),
            "provider_version": cap.get("provider_version"),
            "capability": cap, "engine_version": ENGINE_VERSION}


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) == 0
    return False


# --------------------------------------------------------------------------- #
# 映射到批 2 的匹配输入
# --------------------------------------------------------------------------- #
def _unit_factor(units: str, warnings: List[str]) -> float:
    key = _text(units).lower()
    if not key:
        warnings.append("图纸未标单位：按 mm 处理，请人工确认（不猜别的单位）")
        return 1.0
    if key in _UNIT_FACTORS:
        return _UNIT_FACTORS[key]
    warnings.append("图纸单位「%s」不认识：按 mm 处理，请人工确认" % units)
    return 1.0


def _dimensions(fields: dict, factor: float,
                warnings: Optional[List[str]] = None) -> Dict[str, float]:
    """标注内尺寸优先，外形尺寸兜底（Spec 批 5 §2.4 第 2 条）。

    批 9 的两条收紧（Spec `quick-quote-9-parse-field-alignment.md` §1.2）：
      · `outline_size.source` 命中 `SHEET_SIZE_SOURCES` → 是**图纸幅面**，一个尺寸都不用，
        并记一条 warning（说清它不是成品内尺寸、要人工补）；
      · `annotated_dimensions` 只认带 `axis` + `value` 的 dict；元素是**裸数字**时（统一解析服务
        按 Spec 批 7 §2.4 回的就是 `measured_value`）**不猜轴**，并记一条 warning 说明原因。

    两条都不改变既有口径：没标 source 的外形尺寸照旧兜底，带轴的标注照旧优先。
    """
    warnings = warnings if warnings is not None else []
    out: Dict[str, float] = {}
    annotated = fields.get("annotated_dimensions")
    if isinstance(annotated, (list, tuple)):
        bare = 0
        for item in annotated:
            if not isinstance(item, dict):
                if _num(item) is not None:
                    bare += 1
                continue
            key = _AXIS_KEYS.get(_text(item.get("axis")).lower())
            value = _num(item.get("value"))
            if key and value is not None and key not in out:
                out[key] = value * factor
        if bare:
            warnings.append("图纸标注尺寸只有实测值、没有轴名（axis）：未用于内尺寸，"
                            "请人工确认哪条是内长/内宽/内高（不按顺序猜）")
    outline = fields.get("outline_size")
    if isinstance(outline, dict):
        source = _text(outline.get("source")).lower()
        if source in SHEET_SIZE_SOURCES:
            warnings.append("图纸范围（outline_size.source=%s）是整张图的幅面、不是成品内尺寸："
                            "未用于内尺寸，请人工补内长/内宽/内高" % source)
        else:
            for raw_key in ("length", "width", "height"):
                key = _AXIS_KEYS[raw_key]
                value = _num(outline.get(raw_key))
                if value is not None and key not in out:
                    out[key] = value * factor
    return out


def _gsm_from_notes(notes, warnings: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not isinstance(notes, (list, tuple, str)):
        return out
    items = [notes] if isinstance(notes, str) else list(notes)
    for note in items:
        text = _text(note)
        if not text:
            continue
        found = _GSM_RE.search(text.replace(",", ""))
        value = _num(found.group(0)) if found else None
        if value is None:
            continue
        if any(word in text for word in _GREY_WORDS):
            out.setdefault("grey_board_gsm", value)
        elif any(word in text for word in _FACE_WORDS):
            out.setdefault("face_paper_gsm", value)
    if any(word for word in items if _text(word) and _GSM_RE.search(_text(word).replace(",", ""))):
        if "face_paper_gsm" not in out and "grey_board_gsm" not in out:
            warnings.append("材料标注里读出了克重，但分不清是面纸还是灰板：请人工确认")
    return out


def to_match_inputs(parsed, *, fallback=None) -> dict:
    """解析结果 → 批 2 匹配输入（Spec §2.4）：解析不出的键进 `missing`，绝不填默认值。"""
    parsed = parsed if isinstance(parsed, dict) else {}
    fields = parsed.get("fields") if isinstance(parsed.get("fields"), dict) else {}
    warnings: List[str] = []
    factor = _unit_factor(fields.get("units"), warnings)
    inputs: Dict[str, Any] = {}
    sources: Dict[str, str] = {}

    for key, value in _dimensions(fields, factor, warnings).items():
        inputs[key] = value
        sources[key] = "parse"

    features = fields.get("box_features")
    if isinstance(features, dict):
        for key in ("box_type", "box_family"):
            value = _text(features.get(key))
            if value:
                inputs[key] = value
                sources[key] = "parse"
        inner = features.get("insert_type")
        if _text(inner):
            inputs["insert_type"] = _text(inner)
            sources["insert_type"] = "parse"

    for key in ("closure_type",):
        value = _text(fields.get(key))
        if value:
            inputs[key] = value
            sources[key] = "parse"
    for key in ("v_groove", "magnet", "window"):
        value = fields.get(key)
        if isinstance(value, bool):
            inputs[key] = value
            sources[key] = "parse"

    inputs.update({k: v for k, v in _gsm_from_notes(fields.get("material_notes"), warnings).items()})
    for key in ("face_paper_gsm", "grey_board_gsm"):
        if key in inputs:
            sources[key] = "parse"

    missing = [key for key in MATCH_INPUT_KEYS if key not in inputs]
    fallback = fallback if isinstance(fallback, dict) else {}
    for key in list(missing):
        if key not in fallback or _blank(fallback.get(key)):
            continue
        inputs[key] = copy.deepcopy(fallback[key])
        sources[key] = "fallback"
        missing.remove(key)
    conflicts = [key for key in MATCH_INPUT_KEYS
                 if key in fallback and not _blank(fallback.get(key))
                 and sources.get(key) == "parse" and _text(fallback.get(key)) != _text(inputs.get(key))]
    for key in conflicts:
        warnings.append("「%s」销售手填 %s 与图纸解析 %s 不一致：以图纸解析为准，请确认"
                        % (key, _text(fallback.get(key)), _text(inputs.get(key))))

    return {"inputs": inputs, "missing": missing, "sources": sources,
            "warnings": warnings, "units_factor": factor,
            "match_input_keys": list(MATCH_INPUT_KEYS)}


def parse_and_match(name, raw, cases=None, *, transport=None, today=None,
                    weights=None) -> dict:
    """端到端（Spec §2.6）：文件 → 解析 → 匹配输入 → 候选（不落库、不派任务）。"""
    parsed = parse_file(name, raw, transport=transport)
    mapped = to_match_inputs(parsed)
    match = qq_match.match_cases(mapped["inputs"], cases=cases, weights=weights, today=today)
    return {"engine_version": ENGINE_VERSION, "industry": INDUSTRY,
            "parse": parsed, "inputs": mapped["inputs"], "missing": mapped["missing"],
            "sources": mapped["sources"], "warnings": mapped["warnings"],
            "match": match}


if __name__ == "__main__":                                   # pragma: no cover - 手工跑
    print("parse url =", parse_url())
    print("dwg exts =", DWG_EXTS, "| quick fields =", len(QUICK_FIELDS),
          "| match inputs =", len(MATCH_INPUT_KEYS))
