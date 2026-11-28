"""Exports Excel à la charte GLOBAL ITEC : rapport de projet/sprints et liste d'anomalies."""
from datetime import datetime
from io import BytesIO

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.image import Image
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU

from .reports import COMPANY_FOOTER_LINES, LOGO_PATH, LOGO_RATIO

NAVY, GRID = '132D5E', 'E2E8F0'
STATUS_COLORS = {'Passé': '059669', 'Échoué': 'B91C1C', 'Bloqué': 'D97706', 'En cours': '2563EB', 'À tester': '64748B'}
LOGO_HEIGHT = 70
HEADER_ROWS = 4  # logo (1), titre (2), sous-titre (3), espace (4)
BORDER = Border(*(Side(style='thin', color=GRID) for _ in range(4)))


def _value(value):
    """Valeur de cellule sûre : dates réelles, texte nettoyé (jamais interprété comme formule)."""
    if value is None or value == '':
        return '—'
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub('', value)[:32000]
    if isinstance(value, datetime):
        return timezone.localtime(value).replace(tzinfo=None) if timezone.is_aware(value) else value
    return value


def _date(value):
    if not value:
        return None
    return parse_date(value) if len(value) == 10 else parse_datetime(value)


def _write(ws, row, column, value, **style):
    cell = ws.cell(row=row, column=column)
    cell.value = _value(value)
    if isinstance(cell.value, str):
        cell.data_type = 's'  # « =1+1 » saisi par un utilisateur reste du texte
    for name, item in style.items():
        setattr(cell, name, item)
    return cell


def _sheet(wb, name, title, subtitle, headers, rows, widths, status_column=None):
    ws = wb.create_sheet(name)
    columns = len(headers)
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.sheet_view.showGridLines = False

    # En-tête : logo centré sur la largeur du tableau
    ws.row_dimensions[1].height = LOGO_HEIGHT * 0.75 + 8
    image = Image(str(LOGO_PATH))
    image.height, image.width = LOGO_HEIGHT, round(LOGO_HEIGHT * LOGO_RATIO)
    offset = max(0, (sum(round(w * 7 + 5) for w in widths) - image.width) // 2)
    column = 0
    while column < columns - 1 and offset >= round(widths[column] * 7 + 5):
        offset -= round(widths[column] * 7 + 5)
        column += 1
    image.anchor = OneCellAnchor(
        _from=AnchorMarker(col=column, row=0, colOff=pixels_to_EMU(offset), rowOff=pixels_to_EMU(4)),
        ext=XDRPositiveSize2D(pixels_to_EMU(image.width), pixels_to_EMU(image.height)))
    ws.add_image(image)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for row, text, font in ((2, title, Font(bold=True, size=15, color=NAVY)), (3, subtitle, Font(size=10, color='64748B'))):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=columns)
        _write(ws, row, 1, text, font=font, alignment=center)
    ws.row_dimensions[2].height = 24

    head = HEADER_ROWS + 1
    for i, header in enumerate(headers, 1):
        _write(ws, head, i, header, font=Font(bold=True, color='FFFFFF'), fill=PatternFill('solid', start_color=NAVY),
               alignment=center, border=BORDER)
    for r, values in enumerate(rows, head + 1):
        for c, value in enumerate(values, 1):
            cell = _write(ws, r, c, value, border=BORDER, alignment=Alignment(vertical='top', wrap_text=True))
            if cell.is_date:
                cell.number_format = 'DD/MM/YYYY'
            if c == status_column and value in STATUS_COLORS:
                cell.font = Font(bold=True, color=STATUS_COLORS[value])
    last = head + len(rows)
    if rows:
        ws.auto_filter.ref = f'A{head}:{get_column_letter(columns)}{last}'
    ws.freeze_panes = ws.cell(row=head + 1, column=1)

    # Pied : visible à l'écran sous le tableau, et répété sur chaque page imprimée
    for i, line in enumerate(COMPANY_FOOTER_LINES):
        row = last + 2 + i
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=columns)
        _write(ws, row, 1, line, font=Font(size=8, color=NAVY), alignment=center)
        ws.row_dimensions[row].height = 24
    ws.oddFooter.center.text = '\n'.join(COMPANY_FOOTER_LINES).replace('&', '&&')
    ws.oddFooter.center.size = 6
    ws.page_setup.orientation, ws.page_setup.paperSize = 'landscape', ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f'1:{head}'
    return ws


def _save(wb):
    del wb['Sheet']
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _cases(numbers):
    return ', '.join(f'CAS-{n:03d}' for n in numbers)


def _yes(value):
    return 'Oui' if value else 'Non'


# Palette des graphiques (identique à l'application et aux PDF).
SERIES_HEX = ('257DE0', 'F47230', '00B894')
GRID_HEX = 'E7E7E7'


def _style_axes(chart):
    """Grille en trait fin continu, axes discrets."""
    chart.y_axis.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill=GRID_HEX, w=6350))
    for axis in (chart.x_axis, chart.y_axis):
        axis.spPr = GraphicalProperties(ln=LineProperties(solidFill='C2C2C2', w=6350))
    chart.legend.position = 't'


def _sprint_bar_chart(ws, count):
    """Réussite (%) par sprint, colonnes, à droite du tableau."""
    head, last = HEADER_ROWS + 1, HEADER_ROWS + 1 + count
    chart = BarChart()
    chart.type, chart.grouping = 'col', 'clustered'
    chart.title, chart.height, chart.width = 'Taux de réussite par sprint (%)', 9, 20
    chart.y_axis.scaling.min, chart.y_axis.scaling.max, chart.y_axis.majorUnit = 0, 100, 25
    chart.x_axis.delete, chart.y_axis.delete = False, False
    chart.add_data(Reference(ws, min_col=9, min_row=head, max_row=last), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=head + 1, max_row=last))
    chart.gapWidth = 120
    chart.series[0].graphicalProperties.solidFill = SERIES_HEX[0]
    chart.series[0].graphicalProperties.line.noFill = True
    _style_axes(chart)
    chart.legend = None  # une seule série : le titre la nomme
    ws.add_chart(chart, f'N{head}')


def generate_project_workbook(project, sprints, generated_by) -> bytes:
    from apps.qamanagement.analytics import build_report
    data = build_report(project, sprints)
    perf, stats, defects = data['performance'], data['performance']['stats'], data['performance']['defects']
    subtitle = f'{project.name} — {data["scope"]["label"]} · {generated_by} · {timezone.localtime():%d/%m/%Y %H:%M}'
    wb = Workbook()

    labels = {'pass_rate': 'Réussite des cas exécutés (%)', 'execution_rate': 'Avancement (%)',
              'resolution_rate': 'Résolution des anomalies (%)', 'stability_rate': 'Stabilité (%)'}
    summary = [
        ('Équipe', data['project']['team']), ('Projet', project.name), ('Périmètre', data['scope']['label']),
        ('Score de performance (/100)', perf['score']), ('Niveau', perf['level_label']),
        *((f'{labels[k]} — poids {w} %', perf['components'][k]) for k, w in perf['weights'].items()),
        ('Cas de test', stats['total']), ('Passés', stats['passed']), ('Échoués', stats['failed']),
        ('Bloqués', stats['blocked']), ('En cours', stats['in_progress']), ('À tester', stats['not_run']),
        ('Taux de réussite (%)', stats['success_rate']), ('Automatisation (%)', perf['automation_rate']),
        ('Anomalies', defects['total']), ('Anomalies actives', defects['active']), ('Cas en régression', perf['regressed_cases']),
        ('Délai moyen de résolution (jours)', defects['avg_resolution_days']),
    ]
    _sheet(wb, 'Synthèse', 'Synthèse', subtitle, ['Indicateur', 'Valeur'], summary, [48, 44])
    _narrative_sheet(wb, subtitle, data['narrative'])

    points = data['evolution']['points']
    ws = _sheet(wb, 'Évolution', "Courbe d'évolution", subtitle,
                ['Date', 'Cas', 'Passés', 'Échoués', 'Réussite (%)', 'Avancement (%)', 'Anomalies actives', 'Anomalies résolues', 'Régressions'],
                [[_date(p['date']), p['total'], p['passed'], p['failed'], p['success_rate'], p['execution_rate'],
                  p['defects_open'], p['defects_resolved'], p['regressions']] for p in points],
                [13, 9, 9, 9, 13, 15, 18, 18, 13])
    head, last = HEADER_ROWS + 1, HEADER_ROWS + 1 + len(points)
    chart = LineChart()
    chart.title, chart.height, chart.width = 'Réussite et avancement (%)', 9, 20
    chart.y_axis.scaling.min, chart.y_axis.scaling.max = 0, 100
    chart.x_axis.number_format, chart.x_axis.delete, chart.y_axis.delete = 'DD/MM', False, False
    chart.add_data(Reference(ws, min_col=5, max_col=6, min_row=head, max_row=last), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=head + 1, max_row=last))
    for series, color in zip(chart.series, SERIES_HEX):
        series.graphicalProperties.line.solidFill, series.smooth = color, False
        series.graphicalProperties.line.width = 22000  # ≈ 2 px
    _style_axes(chart)
    ws.add_chart(chart, f'K{head}')

    sprints_ws = _sheet(wb, 'Sprints', 'Résultats par sprint', subtitle,
           ['Sprint', 'Début', 'Fin', 'Durée (j)', 'Statut', 'Cas', 'Passés', 'Échoués', 'Réussite (%)', 'Anomalies', 'Régressions', 'Score'],
           [[s['name'], _date(s['start_date']), _date(s['end_date']), s['duration_days'], s['status_label'], s['stats']['total'],
             s['stats']['passed'], s['stats']['failed'], s['stats']['success_rate'], s['defects']['total'],
             s['regressed_cases'], s['score']] for s in data['sprints']],
           [32, 12, 12, 11, 12, 8, 9, 10, 13, 12, 13, 9])
    if len(data['sprints']) > 1:
        _sprint_bar_chart(sprints_ws, len(data['sprints']))
    _defects_sheet(wb, subtitle, data['defects'])
    _sheet(wb, 'Régressions', 'Régressions', subtitle,
           ['Cas', 'Intitulé', 'Sprint', 'Détectée le', 'Occurrences', 'Statut actuel', 'Anomalies liées'],
           [[f'CAS-{r["case_number"]:03d}', r['title'], r['sprint_name'], _date(r['last_detected_at']), r['occurrences'],
             r['current_status'], ', '.join(d['ticket_number'] for d in r['defects'])] for r in data['regressions']],
           [11, 60, 28, 14, 14, 15, 30], status_column=6)
    _sheet(wb, 'Membres', 'Répartition par membre', subtitle,
           ['Membre', 'Cas', 'Passés', 'Échoués', 'Bloqués', 'En cours', 'À tester', 'Avancement (%)'],
           [[m['member'], m['total'], m['passed'], m['failed'], m['blocked'], m['in_progress'], m['not_run'], m['execution_rate']]
            for m in data['members']], [32, 9, 10, 10, 10, 11, 10, 16])
    _sheet(wb, 'Cas de test', 'Cas de test', subtitle, ['Cas', 'Intitulé', 'Statut', 'Automatisé', 'Jira', 'Anomalies'],
           [[f'CAS-{c["case_number"]:03d}', c['title'], c['status'], _yes(c['is_automated']), c['jira_ticket'], ', '.join(c['defects'])]
            for c in data['cases']], [11, 70, 13, 13, 14, 32], status_column=3)
    return _save(wb)


def narrative_rows(narrative):
    """Phrases clés du rapport sous forme de lignes (rubrique, contenu) pour Excel."""
    rows = [('Introduction', line) for line in narrative['introduction']]
    rows += [('Point clé', line) for line in narrative['key_points']]
    for item in narrative.get('attention_cases', []):
        observed = item['observed'] or (f'{item["comment"]} ({item["comment_author"]})' if item['comment'] else 'Non renseigné')
        rows.append((f'Cas {item["status"].lower()}',
                     f'CAS-{item["case_number"]:03d} {item["jira_ticket"]} — {item["title"]} | Constat : {observed} | '
                     f'Responsable : {", ".join(item["testers"]) or "à attribuer"} | Action : {item["action"]}'))
    rows += [(f'Action — {r["owner"]}', r['actions']) for r in narrative.get('responsibilities', [])]
    rows += [(f'Recommandation {i}', line) for i, line in enumerate(narrative['recommendations'], start=1)]
    rows.append(('Niveau de satisfaction', narrative['satisfaction']['label']))
    rows += [('Conclusion', line) for line in narrative['conclusion']]
    return rows


def _narrative_sheet(wb, subtitle, narrative):
    ws = _sheet(wb, 'Rapport', 'Rapport — phrases clés', subtitle, ['Rubrique', 'Contenu'],
                narrative_rows(narrative), [30, 120])
    wb.move_sheet(ws, offset=-(len(wb.sheetnames) - 2))  # juste après la feuille par défaut, supprimée ensuite
    return ws


def _defects_sheet(wb, subtitle, rows):
    _sheet(wb, 'Anomalies', f'Anomalies ({len(rows)})', subtitle,
           ['Ticket', 'Jira', 'Titre', 'Sévérité', 'Priorité', 'Statut', 'Régression', 'Cas', 'Sprint', 'Assignée à', 'Créée le', 'Résolue le'],
           [[d['ticket_number'], d['jira_key'], d['title'], d['severity_label'], d['priority_label'], d['status_label'],
             _yes(d['is_regression']), _cases(d['case_numbers']), d['sprint_name'], d['assigned_to'],
             _date(d['created_at']), _date(d['resolved_at'])] for d in rows],
           [15, 12, 55, 12, 11, 12, 13, 20, 26, 22, 13, 13])


def generate_defects_workbook(project, defects, generated_by) -> bytes:
    from apps.qamanagement.analytics import defect_row
    wb = Workbook()
    subtitle = f'{project.name} · {generated_by} · {timezone.localtime():%d/%m/%Y %H:%M}'
    _defects_sheet(wb, subtitle, [defect_row(d) for d in defects])
    return _save(wb)
