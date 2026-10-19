"""ReportLab表格渲染，随包中文字体；有界内存缓存，不将样例原件作为画布。"""
from collections import OrderedDict
import hashlib
import io
import json
from pathlib import Path
import threading
from xml.sax.saxutils import escape

_CACHE = OrderedDict()
_LOCK = threading.RLock()
MAX_CACHE_ENTRIES = 24
MAX_CACHE_BYTES = 16 * 1024 * 1024
FONT_PATH = Path(__file__).resolve().parents[1] / 'assets/fonts/DroidSansFallback.ttf'


def render(snapshot):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, HRFlowable

    stable = {key: value for key, value in snapshot.items() if key != 'generated_at'}
    key = hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    cacheable = 'unavailable' not in snapshot.get('availability', {}).values()
    with _LOCK:
        if cacheable and key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
        font = 'PackagingDroidSansFallback'
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font, str(FONT_PATH)))
        blue = colors.HexColor('#1762a5')
        normal = ParagraphStyle('body', fontName=font, fontSize=9, leading=14, wordWrap='CJK',
                                textColor=colors.HexColor('#252b33'), alignment=TA_LEFT)
        heading = ParagraphStyle('section', parent=normal, fontSize=12, leading=18,
                                 textColor=blue, spaceBefore=12, spaceAfter=4, keepWithNext=True)
        title = ParagraphStyle('title', parent=heading, fontSize=17, leading=23, spaceBefore=0, spaceAfter=6)
        small = ParagraphStyle('small', parent=normal, fontSize=8, leading=12)
        width = A4[0] - 84

        def p(value, style=normal):
            return Paragraph(escape(str(value)).replace('\n', '<br/>'), style)

        def table(rows, widths, header=False):
            result = Table(rows, colWidths=widths, repeatRows=1 if header else 0, splitByRow=1, splitInRow=1, hAlign='LEFT')
            commands = [('FONTNAME', (0, 0), (-1, -1), font), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#c9cdd2')),
                        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e6f0fa'))]
            if header: commands.append(('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e6f0fa')))
            result.setStyle(TableStyle(commands))
            return result

        stream = io.BytesIO()
        doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=42,
                                bottomMargin=63, title=snapshot['title'], author='裕同包装')
        story = [p(snapshot['title'], title), p('Packaging Process Requirement Evaluation', small), Spacer(1, 9)]
        metadata = [('文档名称', snapshot['title']), ('文档编号', snapshot.get('requirement_no') or '待确认'),
                    ('模板版本', snapshot['template_version']), ('数据更新时间', snapshot.get('data_updated_at') or '未记录'),
                    ('生成时间', snapshot['generated_at']), ('编制', snapshot.get('created_by') or '待确认'),
                    ('文档状态', '草稿' if snapshot['draft'] else '需求已确认（不代表正式报价发布）')]
        story.append(table([[p(k, small), p(v, small)] for k, v in metadata], [106, width - 106]))
        for section in snapshot['sections']:
            story += [p(section['title'], heading), HRFlowable(width='100%', thickness=.6, color=blue), Spacer(1, 5)]
            if not section['rows']: continue
            rows = [[p('项目', small), p('当前项目数据', small), p('数据来源', small)]]
            for row in section['rows']:
                value = row['value']
                for alternative in row.get('alternatives') or []:
                    value += '\n' + alternative['source'] + '：' + alternative['value'] + '（存在差异）'
                if row.get('conflict') and not row.get('alternatives'):
                    value += '\n存在差异；备选值尚未完整记录，请核对来源资料。'
                rows.append([p(row['label'], small), p(value, small), p(row['source'], small)])
            story.append(table(rows, [106, width - 180, 74], header=True))

        def page(canvas, current):
            canvas.saveState()
            if snapshot['draft']:
                canvas.setFont(font, 66)
                canvas.setFillColor(colors.HexColor('#e5e9ef'))
                canvas.setFillAlpha(.22)
                canvas.translate(A4[0] / 2, A4[1] / 2)
                canvas.rotate(35)
                canvas.drawCentredString(0, 0, '草稿')
                canvas.restoreState(); canvas.saveState()
            canvas.setFont(font, 7)
            canvas.setFillColor(colors.HexColor('#687587'))
            canvas.drawString(42, 40, snapshot['template_version'] + '  生成：' + snapshot['generated_at'])
            canvas.drawRightString(A4[0] - 42, 40, f'第 {current.page} 页')
            canvas.setFont(font, 6)
            canvas.drawString(42, 27, '数据SHA256：' + snapshot['data_hash'])
            canvas.restoreState()

        def canvas_maker(*args, **kwargs):
            kwargs['invariant'] = 1
            return Canvas(*args, **kwargs)

        doc.build(story, onFirstPage=page, onLaterPages=page, canvasmaker=canvas_maker)
        result = stream.getvalue()
        if cacheable and len(result) <= MAX_CACHE_BYTES:
            _CACHE[key] = result
            while len(_CACHE) > MAX_CACHE_ENTRIES or sum(len(item) for item in _CACHE.values()) > MAX_CACHE_BYTES:
                _CACHE.popitem(last=False)
        return result
