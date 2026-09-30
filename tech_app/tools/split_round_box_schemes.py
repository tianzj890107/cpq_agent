"""Split the two layout schemes in the supplied round-box DWG.

The cut lines were measured from the drawing itself: scheme 1 is above
Y=7450, scheme 2 lies between Y=4000 and Y=7450. The shipping/container
illustrations below Y=4000 are common ancillary drawings, not scheme 2.
The single original border spanning both schemes is intentionally omitted.
The input DWG is never changed.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

import ezdxf
from ezdxf import bbox


UPPER_FLOOR = 7450.0
LOWER_FLOOR = 4000.0


def scheme_for_bounds(low: float, high: float) -> str:
    """Drawing-specific band selection; never apply these coordinates to other DWGs."""
    if low >= UPPER_FLOOR:
        return "upper"
    if low >= LOWER_FLOOR and high < UPPER_FLOOR:
        return "lower"
    return "excluded"


def split(source_dxf: Path, target_dir: Path, converter: Path) -> None:
    names = {
        "upper": "圆盘盒-方案1-上半",
        "lower": "圆盘盒-方案2-下半",
    }
    kept: dict[str, int] = {}
    omitted: dict[str, int] = {"shared_border": 0, "ancillary": 0}
    with tempfile.TemporaryDirectory(prefix="round-box-schemes-") as temporary:
        work = Path(temporary)
        dxf_dir = work / "dxf"
        dwg_dir = work / "dwg"
        dxf_dir.mkdir()
        dwg_dir.mkdir()
        for scheme, stem in names.items():
            drawing = ezdxf.readfile(source_dxf)
            modelspace = drawing.modelspace()
            count = 0
            for entity in list(modelspace):
                bounds = bbox.extents([entity])
                if not bounds.has_data:
                    raise ValueError(f"Entity {entity.dxf.handle} has no bounding box")
                low, high = bounds.extmin.y, bounds.extmax.y
                selected = scheme_for_bounds(low, high) == scheme
                if selected:
                    count += 1
                else:
                    modelspace.delete_entity(entity)
                    if scheme == "upper" and low < UPPER_FLOOR < high:
                        omitted["shared_border"] += 1
                    elif scheme == "upper" and high < LOWER_FLOOR:
                        omitted["ancillary"] += 1
            kept[scheme] = count
            drawing.saveas(dxf_dir / f"{stem}.dxf")

        command = [
            str(converter), str(dxf_dir), str(dwg_dir), "ACAD2018", "DWG",
            "0", "1", "*.dxf",
        ]
        subprocess.run(command, check=True, capture_output=True, timeout=120)
        target_dir.mkdir(parents=True, exist_ok=True)
        for stem in names.values():
            produced = dwg_dir / f"{stem}.dwg"
            if not produced.is_file() or produced.stat().st_size == 0:
                raise RuntimeError(f"ODA did not produce {produced.name}")
            destination = target_dir / produced.name
            if destination.exists():
                raise FileExistsError(f"Refusing to overwrite {destination}")
            produced.replace(destination)
            print(f"{destination}: {destination.stat().st_size} bytes")
    print(f"Modelspace entities kept: {kept}")
    print(f"Shared border and ancillary entities excluded: {omitted}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="圆盘盒原始 DWG 或已转换的 DXF")
    parser.add_argument("target_dir", type=Path)
    parser.add_argument("--converter", type=Path, default=Path(
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"))
    arguments = parser.parse_args()
    source = arguments.source.resolve()
    if not source.is_file() or source.suffix.lower() not in (".dwg", ".dxf"):
        parser.error("source must be an existing DWG or DXF file")
    if source.suffix.lower() == ".dxf":
        split(source, arguments.target_dir, arguments.converter)
        return
    with tempfile.TemporaryDirectory(prefix="round-box-input-") as temporary:
        dxf_dir = Path(temporary)
        subprocess.run([
            str(arguments.converter), str(source.parent), str(dxf_dir), "ACAD2018", "DXF",
            "0", "1", source.name,
        ], check=True, capture_output=True, timeout=120)
        converted = dxf_dir / f"{source.stem}.dxf"
        if not converted.is_file() or converted.stat().st_size == 0:
            raise RuntimeError("ODA did not produce an input DXF")
        split(converted, arguments.target_dir, arguments.converter)


if __name__ == "__main__":
    main()
