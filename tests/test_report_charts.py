"""
Graphiques des rapports PDF : couleurs de la charte, et contrôle automatique de mise en page
(aucun texte ne chevauche un autre ni ne déborde du cadre, marques dans le cadre).
"""
from datetime import date, timedelta

import pytest
from reportlab.graphics.shapes import Circle, Group, Line, PolyLine, Rect, String
from reportlab.pdfbase.pdfmetrics import stringWidth

from apps.teams import report_charts as charts

WIDTH = 500


def walk(node):
    for item in getattr(node, 'contents', []):
        yield item
        if isinstance(item, Group):
            yield from walk(item)


def text_boxes(drawing):
    boxes = []
    for item in walk(drawing):
        if isinstance(item, String):
            width = stringWidth(item.text, item.fontName, item.fontSize)
            x = {'start': item.x, 'middle': item.x - width / 2, 'end': item.x - width}[item.textAnchor]
            boxes.append((item.text, x, item.y - item.fontSize * 0.2, x + width, item.y + item.fontSize * 0.75))
    return boxes


def assert_clean_layout(drawing):
    boxes = text_boxes(drawing)
    for text, x0, y0, x1, y1 in boxes:
        assert x0 >= -0.5 and x1 <= drawing.width + 0.5, f'« {text} » déborde horizontalement'
        assert y0 >= -0.5 and y1 <= drawing.height + 0.5, f'« {text} » déborde verticalement'
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            overlap = min(a[3], b[3]) - max(a[1], b[1]) > 0.5 and min(a[4], b[4]) - max(a[2], b[2]) > 0.5
            assert not overlap, f'« {a[0]} » chevauche « {b[0]} »'
    for item in walk(drawing):
        if isinstance(item, Rect):
            assert item.x >= -0.5 and item.x + item.width <= drawing.width + 0.5
        if isinstance(item, PolyLine):
            xs, ys = item.points[0::2], item.points[1::2]
            assert min(xs) >= 0 and max(xs) <= drawing.width and min(ys) >= 0 and max(ys) <= drawing.height


def points(n, **series):
    start = date(2026, 11, 1)
    return [{'date': (start + timedelta(days=i)).isoformat(), **{k: f(i) for k, f in series.items()}} for i in range(n)]


RATES = [('success_rate', 'Taux de réussite', charts.SERIES[0]), ('execution_rate', 'Avancement', charts.SERIES[1])]


class TestLineChart:
    @pytest.mark.parametrize('n', [1, 2, 14, 62])
    def test_layout_is_clean(self, n):
        data = points(n, success_rate=lambda i: min(100, 10 + i * 5), execution_rate=lambda i: min(100, 20 + i * 6))
        assert_clean_layout(charts.line_chart(data, RATES, True, WIDTH))

    def test_converging_end_labels_are_spread_with_leader_lines(self):
        data = points(5, success_rate=lambda i: 80, execution_rate=lambda i: 81)
        drawing = charts.line_chart(data, RATES, True, WIDTH)
        assert_clean_layout(drawing)
        ends = [s for s in text_boxes(drawing) if s[0] in ('80 %', '81 %')]
        assert len(ends) == 2 and abs(ends[0][2] - ends[1][2]) >= charts.LABEL_GAP - 0.5
        assert any(isinstance(i, Line) and i.strokeColor == charts.AXIS for i in walk(drawing))

    def test_styling_follows_the_chart_rules(self):
        drawing = charts.line_chart(points(5, success_rate=lambda i: 50, execution_rate=lambda i: 60), RATES, True, WIDTH)
        lines = [i for i in walk(drawing) if isinstance(i, PolyLine)]
        assert [line.strokeColor for line in lines] == charts.SERIES[:2]
        assert all(line.strokeWidth == charts.LINE_WIDTH for line in lines)
        dots = [i for i in walk(drawing) if isinstance(i, Circle)]
        assert len(dots) == 2 and all(d.strokeColor == charts.SURFACE for d in dots)  # point cerclé de blanc
        grid = [i for i in walk(drawing) if isinstance(i, Line) and i.strokeColor == charts.GRID]
        assert grid and all(not getattr(g, 'strokeDashArray', None) for g in grid)  # grille continue
        labels = {t[0] for t in text_boxes(drawing)}
        assert {'0 %', '50 %', '100 %', 'Taux de réussite', 'Avancement'} <= labels

    def test_count_axis_uses_round_ticks(self):
        data = points(6, defects_open=lambda i: i * 3, defects_resolved=lambda i: i)
        drawing = charts.line_chart(data, [('defects_open', 'Actives', charts.SERIES[0]),
                                           ('defects_resolved', 'Résolues', charts.SERIES[1])], False, WIDTH)
        assert_clean_layout(drawing)
        assert {'0', '5', '10', '15', '20'} <= {t[0] for t in text_boxes(drawing)}

    def test_no_points(self):
        assert 'Aucune donnée' in text_boxes(charts.line_chart([], RATES, True, WIDTH))[0][0]


class TestStatusBar:
    def test_blocks_follow_the_status_order_and_label_only_wide_blocks(self):
        stats = {'total': 24, 'passed': 15, 'in_progress': 1, 'blocked': 2, 'failed': 5, 'not_run': 1}
        drawing = charts.status_bar(stats, WIDTH)
        assert_clean_layout(drawing)
        fills = [r.fillColor for r in walk(drawing) if isinstance(r, Rect) and r.height > 10]
        assert fills == [c for _, _, c, _ in charts.STATUS_ORDER]
        texts = [t[0] for t in text_boxes(drawing)]
        assert '62,5 %' in texts and '4,2 %' not in texts  # « En cours » trop étroit : part dans la légende
        assert 'En cours 1 (4,2 %)' in texts and 'Passés 15 (62,5 %)' in texts

    def test_empty(self):
        assert text_boxes(charts.status_bar({'total': 0}, WIDTH))[0][0] == 'Aucun cas de test'


class TestSprintChart:
    def test_layout_and_values(self):
        sprints = [{'name': f'Sprint {i} — Workflow de traitement des dossiers', 'stats': {'total': 10, 'success_rate': r, 'execution_rate': e}}
                   for i, (r, e) in enumerate([(0, 0), (45, 70), (62.5, 85), (100, 100)], 1)]
        drawing = charts.sprint_chart(sprints, WIDTH)
        assert_clean_layout(drawing)
        texts = [t[0] for t in text_boxes(drawing)]
        assert '62,5 %' in texts and 'Taux de réussite' in texts and 'Avancement' in texts
        assert any(t.endswith('…') for t in texts)  # nom long abrégé

    def test_caps_the_number_of_sprints(self):
        sprints = [{'name': f'S{i}', 'stats': {'total': 1, 'success_rate': 50, 'execution_rate': 50}} for i in range(20)]
        names = [t[0] for t in text_boxes(charts.sprint_chart(sprints, WIDTH)) if t[0].startswith('S')]
        assert names == [f'S{i}' for i in range(8, 20)]

    def test_no_sprint_with_cases(self):
        assert charts.sprint_chart([{'name': 'Vide', 'stats': {'total': 0}}], WIDTH) is None


@pytest.mark.django_db
class TestExcelCharts:
    def test_workbook_charts_use_the_palette(self, env):
        import io
        from openpyxl import load_workbook
        from apps.teams.excel_reports import SERIES_HEX, generate_project_workbook
        wb = load_workbook(io.BytesIO(generate_project_workbook(env.project, None, 'QA')))
        sprint_charts = wb['Sprints']._charts
        assert len(sprint_charts) == 1
        # openpyxl ne relit pas les styles de série : on vérifie la génération directement.
        from openpyxl import Workbook
        from apps.teams.excel_reports import _sprint_bar_chart
        ws = Workbook().active
        for row in range(1, 10):
            ws.cell(row=row, column=9, value=row)
        _sprint_bar_chart(ws, 3)
        chart = ws._charts[0]
        assert chart.series[0].graphicalProperties.solidFill.srgbClr == SERIES_HEX[0]
        assert chart.legend is None and chart.y_axis.scaling.max == 100
        assert len(wb['Évolution']._charts) == 1


from tests.test_sprint_separation_jira_import import env  # noqa: E402,F401  (fixture)
