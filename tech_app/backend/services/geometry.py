"""
几何生成内核(确定性 CAD) —— 平台的"承重墙"。

把 IR 中每个零件的"特征列表"确定性地翻译成 CadQuery(基于 OpenCASCADE)调用，
生成真正的 B-rep 几何，导出 STEP(3D 制造) + STL(Web 三维查看)，并:
  - 计算质量属性(体积/质量/包围盒);
  - 做基础几何/DFM 校验(零厚度、孔超出板面、孔过密等)。

关键: 大模型不直接写几何代码; 它只产出受 schema 约束的特征 IR，
由这里的确定性翻译器生成几何 —— 从而可制造、可校验、可追溯。

CadQuery 依赖较重，若未安装，本模块抛出 GeometryUnavailable，
上层 API 会优雅降级(仍返回解析/拆解结果，仅几何不可用)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..models.ir import Feature, FeatureType, Part

try:
    import cadquery as cq  # type: ignore

    CADQUERY_AVAILABLE = True
except Exception:  # pragma: no cover - 取决于环境
    cq = None  # type: ignore
    CADQUERY_AVAILABLE = False


class GeometryUnavailable(RuntimeError):
    """CadQuery 未安装时抛出。"""


CADQUERY_UNAVAILABLE_CODE = "CADQUERY_UNAVAILABLE"

# 预检的确定性规则表：判据、code、修复动作三件套只在这里定义一份，
# 单零件重生成（preflight_parts）与批量计划（plan_batch）共用同一套逐件结果。
_BASE_REQUIRED = {
    FeatureType.plate: ("length", "width", "thickness"),
    FeatureType.box: ("length", "width", "height"),
    FeatureType.cylinder: ("diameter", "height"),
}
_FEATURE_REQUIRED = {
    FeatureType.hole: ("diameter",),
    FeatureType.hole_pattern: ("diameter",),
    FeatureType.fillet: ("radius",),
    FeatureType.chamfer: ("distance",),
}


def _issue(part: Part, code: str, field_name: str, message: str,
           severity: str = "blocking_for_part", repair: str = "none") -> dict:
    """结构化预检问题：前端据此决定「补参数」还是「再次生成」。

    severity: blocking_for_part（只挡这个零件）/ blocking_for_batch（挡整批）。
    repair:   initialize_base_feature / fill_base_feature_dimensions /
              replace_base_feature / fill_feature_dimensions / regenerate /
              install_cadquery / none。
    """
    return {
        "part_id": part.part_id, "part_name": part.name, "scope": "geometry",
        "code": code, "field": field_name, "severity": severity,
        "repair": repair, "message": message,
    }


def runtime_issue(part_id: str, part_name: str, message: str) -> dict:
    """预检通过、但 CAD 运行期失败：只有这种失败才适合「再次生成」。"""
    return {
        "part_id": part_id, "part_name": part_name, "scope": "geometry",
        "code": "CAD_GENERATION_FAILED", "field": "cad",
        "severity": "blocking_for_part", "repair": "regenerate",
        "message": message,
    }


def preflight_part(part: Part, seen_ids: Optional[set] = None) -> List[dict]:
    """单个零件的结构化几何预检（纯本地：不调 LLM、不调 CAD）。

    ``seen_ids`` 传入时顺带做 part_id 去重（批量计划按顺序累积）。
    """
    issues: List[dict] = []
    if seen_ids is not None:
        if part.part_id in seen_ids:
            issues.append(_issue(part, "PART_ID_DUPLICATE", "part_id", "part_id 重复"))
        seen_ids.add(part.part_id)
    if part.quantity < 1:
        issues.append(_issue(part, "QUANTITY_INVALID", "quantity", "quantity 必须至少为 1"))
    if not part.features:
        issues.append(_issue(
            part, "BASE_FEATURE_MISSING", "features[0]",
            "缺少基体特征（plate / box / cylinder）",
            repair="initialize_base_feature",
        ))
        return issues

    base = part.features[0]
    required = _BASE_REQUIRED.get(base.type)
    if required is None:
        issues.append(_issue(
            part, "BASE_FEATURE_INVALID_TYPE", "features[0].type",
            f"第 1 个特征必须是 plate / box / cylinder，当前为 {base.type.value}",
            repair="replace_base_feature",
        ))
    else:
        for field_name in required:
            value = getattr(base, field_name)
            field_path = f"features[0].{field_name}"
            if value is None:
                issues.append(_issue(
                    part, "BASE_FEATURE_DIMENSION_MISSING", field_path,
                    f"基体缺少 {base.type.value}.{field_name}",
                    repair="fill_base_feature_dimensions",
                ))
            elif value <= 0:
                issues.append(_issue(
                    part, "BASE_FEATURE_DIMENSION_INVALID", field_path,
                    f"基体 {base.type.value}.{field_name} 必须大于 0",
                    repair="fill_base_feature_dimensions",
                ))

    for index, feature in enumerate(part.features[1:], start=1):
        shown = index + 1  # 人话里的「第 N 个特征」是 1 起数
        for field_name in _FEATURE_REQUIRED.get(feature.type, ()):
            value = getattr(feature, field_name)
            field_path = f"features[{index}].{field_name}"
            if value is None:
                issues.append(_issue(
                    part, "FEATURE_DIMENSION_MISSING", field_path,
                    f"第 {shown} 个特征 {feature.type.value} 缺少 {field_name}",
                    repair="fill_feature_dimensions",
                ))
            elif value <= 0:
                issues.append(_issue(
                    part, "FEATURE_DIMENSION_INVALID", field_path,
                    f"第 {shown} 个特征 {feature.type.value}.{field_name} 必须大于 0",
                    repair="fill_feature_dimensions",
                ))
        if feature.type == FeatureType.hole_pattern:
            for count_name, spacing_name in (("count_x", "spacing_x"), ("count_y", "spacing_y")):
                count = getattr(feature, count_name) or 1
                spacing = getattr(feature, spacing_name)
                if count < 1:
                    issues.append(_issue(
                        part, "FEATURE_DIMENSION_INVALID", f"features[{index}].{count_name}",
                        f"{count_name} 必须至少为 1", repair="fill_feature_dimensions",
                    ))
                if count > 1 and (spacing is None or spacing <= 0):
                    issues.append(_issue(
                        part,
                        "FEATURE_DIMENSION_MISSING" if spacing is None else "FEATURE_DIMENSION_INVALID",
                        f"features[{index}].{spacing_name}",
                        f"{count_name} 大于 1 时必须提供正数 {spacing_name}",
                        repair="fill_feature_dimensions",
                    ))
    return issues


def preflight_parts(parts: List[Part]) -> List[str]:
    """在 CAD 调用前检查模型无法安全补齐的几何必填项。

    这是纯本地校验，不会调用 LLM；让用户在生成前看到精确缺失字段，
    避免因模型格式问题重复付费解析。

    返回的字符串形状（``零件 P-001（上盖）: …``）是既有的对外契约，
    vision.py 与单零件重生成的 409 都靠它，这里只是把逐件结构化结果拼成人话。
    """
    messages: List[str] = []
    seen_ids: set[str] = set()
    for part in parts:
        label = f"零件 {part.part_id}（{part.name}）"
        for issue in preflight_part(part, seen_ids):
            messages.append(f"{label}: {issue['message']}")
    return messages


def plan_batch(parts: List[Part]):
    """批量生成计划：能生成的零件 + 被挡零件的结构化问题（C1）。

    预检是确定性的，所以在**提交异步任务之前**就能跑完：被挡零件不进任务，
    同一份 IR 反复点击也不会再堆出一串一模一样的失败任务（C7）。
    """
    processable: List[Part] = []
    blocked: List[dict] = []
    seen_ids: set[str] = set()
    for part in parts:
        issues = preflight_part(part, seen_ids)
        if issues:
            blocked.extend(issues)
        else:
            processable.append(part)
    return processable, blocked


@dataclass
class PartGeometryResult:
    part_id: str
    name: str
    ok: bool
    step_path: Optional[str] = None
    stl_path: Optional[str] = None
    volume_mm3: Optional[float] = None
    mass_g: Optional[float] = None
    bbox: Optional[List[float]] = None  # [dx, dy, dz]
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


# --------------------------------------------------------------------------- #
# 单个零件: 特征 -> CadQuery 实体
# --------------------------------------------------------------------------- #
def build_solid(part: Part):
    """公开接口: 按特征构建零件实体(供 2D 工程图等下游复用)。返回 cq.Workplane。"""
    if not CADQUERY_AVAILABLE:
        raise GeometryUnavailable("CadQuery 未安装，无法构建几何。")
    return _build_solid(part, [])


def _build_solid(part: Part, warnings: List[str]):
    """按特征顺序构建实体。第一个特征作为基体。返回 cq.Workplane。"""
    if not part.features:
        raise ValueError("零件无任何特征，无法生成几何")

    base = part.features[0]
    wp = _build_base(base, warnings)

    # 后续特征叠加(孔、阵列、倒角等)
    for feat in part.features[1:]:
        wp = _apply_feature(wp, feat, base, warnings)

    return wp


def _build_base(feat: Feature, warnings: List[str]):
    t = feat.type
    if t == FeatureType.plate:
        L = _req(feat.length, "plate.length")
        W = _req(feat.width, "plate.width")
        Th = _req(feat.thickness, "plate.thickness")
        _check_positive(L, W, Th, warnings=warnings, label="板")
        return cq.Workplane("XY").box(L, W, Th)
    if t == FeatureType.box:
        L = _req(feat.length, "box.length")
        W = _req(feat.width, "box.width")
        H = _req(feat.height, "box.height")
        _check_positive(L, W, H, warnings=warnings, label="长方体")
        return cq.Workplane("XY").box(L, W, H)
    if t == FeatureType.cylinder:
        D = _req(feat.diameter, "cylinder.diameter")
        H = _req(feat.height, "cylinder.height")
        _check_positive(D, H, warnings=warnings, label="圆柱")
        return cq.Workplane("XY").cylinder(H, D / 2.0)
    raise ValueError(f"基体特征类型不支持作为基体: {t}")


def _apply_feature(wp, feat: Feature, base: Feature, warnings: List[str]):
    t = feat.type

    if t == FeatureType.hole:
        D = _req(feat.diameter, "hole.diameter")
        x = feat.x or 0.0
        y = feat.y or 0.0
        _check_hole_in_face(x, y, D, base, warnings)
        return (
            wp.faces(">Z").workplane(centerOption="CenterOfBoundBox")
            .pushPoints([(x, y)])
            .hole(D)
        )

    if t == FeatureType.hole_pattern:
        D = _req(feat.diameter, "hole_pattern.diameter")
        cx = feat.count_x or 1
        cy = feat.count_y or 1
        sx = feat.spacing_x or 0.0
        sy = feat.spacing_y or 0.0
        pts = _grid_points(cx, cy, sx, sy)
        for (px, py) in pts:
            _check_hole_in_face(px, py, D, base, warnings)
        return (
            wp.faces(">Z").workplane(centerOption="CenterOfBoundBox")
            .pushPoints(pts)
            .hole(D)
        )

    if t == FeatureType.fillet:
        r = _req(feat.radius, "fillet.radius")
        try:
            return wp.edges().fillet(r)
        except Exception as e:
            warnings.append(f"倒圆角 r={r} 失败，已跳过: {e}")
            return wp

    if t == FeatureType.chamfer:
        d = _req(feat.distance, "chamfer.distance")
        try:
            return wp.edges().chamfer(d)
        except Exception as e:
            warnings.append(f"倒角 d={d} 失败，已跳过: {e}")
            return wp

    warnings.append(f"未知/不支持的叠加特征 {t}，已跳过")
    return wp


# --------------------------------------------------------------------------- #
# 辅助 & 校验
# --------------------------------------------------------------------------- #
def _req(value, name: str) -> float:
    if value is None:
        raise ValueError(f"缺少必需尺寸参数: {name}")
    return float(value)


def _check_positive(*vals, warnings: List[str], label: str):
    for v in vals:
        if v is not None and v <= 0:
            warnings.append(f"{label}存在非正尺寸 {v}mm，几何可能无效")


def _grid_points(cx: int, cy: int, sx: float, sy: float):
    """以零件中心为原点生成 cx*cy 网格点。"""
    pts = []
    x0 = -(cx - 1) * sx / 2.0
    y0 = -(cy - 1) * sy / 2.0
    for i in range(cx):
        for j in range(cy):
            pts.append((x0 + i * sx, y0 + j * sy))
    return pts


def _check_hole_in_face(x: float, y: float, d: float, base: Feature, warnings: List[str]):
    """校验孔是否落在基体顶面范围内(DFM: 孔边距)。"""
    half_x = half_y = None
    if base.type in (FeatureType.plate, FeatureType.box):
        if base.length:
            half_x = base.length / 2.0
        if base.width:
            half_y = base.width / 2.0
    if half_x is not None and abs(x) + d / 2.0 > half_x:
        warnings.append(f"孔(x={x}, ⌀{d}) 超出板面 X 边界，存在破边风险")
    if half_y is not None and abs(y) + d / 2.0 > half_y:
        warnings.append(f"孔(y={y}, ⌀{d}) 超出板面 Y 边界，存在破边风险")


def _mass_properties(solid, density_g_cm3: Optional[float], warnings: List[str]):
    """体积(mm^3)、质量(g)、包围盒(mm)。"""
    volume = mass = None
    bbox = None
    try:
        shape = solid.val()
        volume = float(shape.Volume())  # mm^3
        if density_g_cm3:
            mass = volume / 1000.0 * density_g_cm3  # mm^3 -> cm^3 * g/cm^3
        bb = shape.BoundingBox()
        bbox = [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)]
    except Exception as e:
        warnings.append(f"质量属性计算失败: {e}")
    return volume, mass, bbox


def _validate(solid, warnings: List[str]):
    """基础几何有效性校验(OCCT)。"""
    try:
        shape = solid.val()
        if hasattr(shape, "isValid") and not shape.isValid():
            warnings.append("OCCT 校验: 几何拓扑无效(可能存在自交/开壳)")
    except Exception as e:
        warnings.append(f"几何有效性校验异常: {e}")


# --------------------------------------------------------------------------- #
# 对外: 生成单个零件 / 整机
# --------------------------------------------------------------------------- #
def generate_part(part: Part, out_dir: Path) -> PartGeometryResult:
    if not CADQUERY_AVAILABLE:
        raise GeometryUnavailable(
            "CadQuery 未安装，无法生成几何。请 `pip install cadquery`。"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []
    try:
        wp = _build_solid(part, warnings)
        _validate(wp, warnings)

        density = part.material.density if part.material else None
        volume, mass, bbox = _mass_properties(wp, density, warnings)

        step_path = out_dir / f"{part.part_id}.step"
        stl_path = out_dir / f"{part.part_id}.stl"
        cq.exporters.export(wp, str(step_path))
        cq.exporters.export(wp, str(stl_path))

        return PartGeometryResult(
            part_id=part.part_id,
            name=part.name,
            ok=True,
            step_path=str(step_path),
            stl_path=str(stl_path),
            volume_mm3=round(volume, 3) if volume else None,
            mass_g=round(mass, 3) if mass else None,
            bbox=bbox,
            warnings=warnings,
        )
    except Exception as e:
        return PartGeometryResult(
            part_id=part.part_id,
            name=part.name,
            ok=False,
            warnings=warnings,
            error=str(e),
        )


def generate_all(parts: List[Part], out_dir: Path) -> List[PartGeometryResult]:
    return [generate_part(p, out_dir) for p in parts]


def result_from_solid(
    part_id: str, name: str, solid, out_dir: Path, density: Optional[float] = None
) -> PartGeometryResult:
    """直接用一个已有实体(如从 STEP 导入的 solid)导出 STEP/STL + 质量属性/校验。"""
    if not CADQUERY_AVAILABLE:
        raise GeometryUnavailable("CadQuery 未安装。")
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []
    try:
        wp = cq.Workplane().newObject([solid])
        _validate(wp, warnings)
        volume, mass, bbox = _mass_properties(wp, density, warnings)
        step_path = out_dir / f"{part_id}.step"
        stl_path = out_dir / f"{part_id}.stl"
        cq.exporters.export(wp, str(step_path))
        cq.exporters.export(wp, str(stl_path))
        return PartGeometryResult(
            part_id=part_id, name=name, ok=True,
            step_path=str(step_path), stl_path=str(stl_path),
            volume_mm3=round(volume, 3) if volume else None,
            mass_g=round(mass, 3) if mass else None,
            bbox=bbox, warnings=warnings,
        )
    except Exception as e:
        return PartGeometryResult(part_id, name, ok=False, warnings=warnings, error=str(e))
