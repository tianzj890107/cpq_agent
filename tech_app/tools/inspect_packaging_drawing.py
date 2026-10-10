"""Read-only drawing evidence ledger and CAD overview; no BOM or application store."""
import argparse
import json
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing import layout
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy


def inspect(source, output):
    document = ezdxf.readfile(source)
    space = document.modelspace()
    ledger = []
    for entity in space:
        kind = entity.dxftype()
        if kind not in ('TEXT', 'MTEXT', 'CIRCLE', 'DIMENSION'):
            continue
        bounds = bbox.extents([entity])
        row = {'handle': entity.dxf.handle, 'type': kind,
               'layer': entity.dxf.layer,
               'bbox': [bounds.extmin.x, bounds.extmin.y, bounds.extmax.x, bounds.extmax.y]
               if bounds.has_data else None}
        if kind in ('TEXT', 'MTEXT'):
            row['text'] = entity.plain_text()
        if kind == 'CIRCLE':
            row['diameter'] = 2 * entity.dxf.radius
        if kind == 'DIMENSION':
            row['measurement'] = entity.get_measurement()
            row['text'] = entity.dxf.get('text', '')
        ledger.append(row)
    ledger.sort(key=lambda row: (-(row['bbox'] or [0, 0, 0, 0])[3],
                                 (row['bbox'] or [0, 0])[0], row['handle']))
    output.mkdir(parents=True, exist_ok=True)
    (output / 'ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2))
    backend = PyMuPdfBackend()
    Frontend(RenderContext(document), backend, config=Configuration(
        background_policy=BackgroundPolicy.WHITE,
        color_policy=ColorPolicy.BLACK)).draw_layout(space, finalize=True)
    content = backend.get_pdf_bytes(layout.Page(420, 160, layout.Units.mm))
    (output / 'overview.pdf').write_bytes(content)
    import pymupdf
    with pymupdf.open(stream=content, filetype='pdf') as image:
        image[0].get_pixmap(matrix=pymupdf.Matrix(3, 3)).save(output / 'overview.png')
    print(json.dumps({'source': str(source), 'output': str(output), 'records': len(ledger)}, ensure_ascii=False))


def compare(source, output):
    # Run only after the independent ledger/overview has been written.
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    from tech_app.backend.services import cad_ir, packaging_parts
    from tech_app.backend.services.packaging_business_part_resolver import resolve_business_parts
    ir = cad_ir.parse_dxf(source.read_bytes(), filename=source.name)
    geometry = packaging_parts.extract(ir)
    result = resolve_business_parts('offline-readonly', ir, geometry, strict_schemes=True)
    document = packaging_parts.business_parts_document(result['reference'],geometry,bindings=result['match'])
    rows = [{'name':row['name'],'sequence_no':row.get('sequence_no'),
             'drawing_order':row.get('drawing_order'), 'binding':row.get('geometry_binding'),
             'sections':row.get('sections')} for row in document['business_parts']]
    (output/'parsed-parts.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    print(json.dumps({'parts':len(rows),'bound':sum(row['binding'].get('status')=='bound' for row in rows)},ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--compare', action='store_true')
    args = parser.parse_args()
    inspect(args.source, args.output)
    if args.compare:
        compare(args.source,args.output)
