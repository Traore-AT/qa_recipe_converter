"""
Rapports PDF : Daily Scrum, hebdomadaire, projet, sprint(s).

Générés avec ReportLab (100 % Python) : fonctionne à l'identique sous Windows, Linux et
Render, sans bibliothèque système. Toutes les pages portent la charte GLOBAL ITEC :
logo centré en en-tête, mentions légales en pied de page.
"""
from datetime import timedelta
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from django.utils.formats import date_format
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import CondPageBreak, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.models import ExtractedUseCase
from .models import Project, Team, TeamMember
from .permissions import can_view_project

# ── Charte GLOBAL ITEC ───────────────────────────────────────────────────────
COMPANY_NAME = 'GLOBAL ITEC'
COMPANY_FOOTER_LINES = (
    'GLOBAL ITEC – SARL au capital de 10 000 000 GNF – RCCM/GC –KAL/034.492A/2011 – '
    'Code NIF 176496818-6E -Sonfonia-Soloprimo – Conakry - BP : 2817 |',
    'Tél : +224 627 90 58 41 / +224 657 22 95 00 – E-mail : contact@global-itec.com – www.global-itec.com',
)
LOGO_PATH = settings.BASE_DIR / 'static' / 'logo_global_report.png'
LOGO_RATIO = 876 / 408

NAVY, CYAN = colors.HexColor('#132d5e'), colors.HexColor('#20dfea')
INK, MUTED = colors.HexColor('#1e293b'), colors.HexColor('#64748b')
GRID, ZEBRA = colors.HexColor('#e2e8f0'), colors.HexColor('#f8fafc')
BLUE, GREEN, RED = colors.HexColor('#2563eb'), colors.HexColor('#059669'), colors.HexColor('#b91c1c')
AMBER, SKY, SLATE = colors.HexColor('#d97706'), colors.HexColor('#0284c7'), colors.HexColor('#64748b')

STATUS_COLORS = {'Passé': GREEN, 'Échoué': RED, 'Bloqué': AMBER, 'En cours': BLUE, 'À tester': SLATE}
STATUS_KEYS = [('passed', 'Passé'), ('failed', 'Échoué'), ('blocked', 'Bloqué'),
               ('in_progress', 'En cours'), ('not_run', 'À tester')]
LEVEL_COLORS = {'excellent': GREEN, 'good': SKY, 'average': AMBER, 'critical': RED, 'not_evaluated': SLATE}
SEVERITY_COLORS = {'critical': RED, 'major': AMBER, 'minor': BLUE, 'trivial': SLATE}

MARGIN = 1.8 * cm
WIDTH = A4[0] - 2 * MARGIN

_BASE = ParagraphStyle('base', fontName='Helvetica', fontSize=9, leading=12, textColor=INK)
STYLES = {
    'base': _BASE,
    'cell': ParagraphStyle('cell', parent=_BASE, fontSize=8, leading=10),
    'head': ParagraphStyle('head', parent=_BASE, fontName='Helvetica-Bold', fontSize=8, leading=10,
                           textColor=colors.white),
    'title': ParagraphStyle('title', parent=_BASE, fontName='Helvetica-Bold', fontSize=18, leading=22,
                            textColor=NAVY, alignment=TA_CENTER, spaceAfter=2),
    'subtitle': ParagraphStyle('subtitle', parent=_BASE, fontSize=11, leading=14, textColor=MUTED,
                               alignment=TA_CENTER, spaceAfter=8),
    'h2': ParagraphStyle('h2', parent=_BASE, fontName='Helvetica-Bold', fontSize=12.5, leading=16,
                         textColor=NAVY, spaceBefore=12, spaceAfter=6),
    'h3': ParagraphStyle('h3', parent=_BASE, fontName='Helvetica-Bold', fontSize=10.5, leading=14,
                         textColor=BLUE, spaceBefore=8, spaceAfter=3),
    'muted': ParagraphStyle('muted', parent=_BASE, fontSize=8.5, leading=11, textColor=MUTED),
    'kpi': ParagraphStyle('kpi', parent=_BASE, fontName='Helvetica-Bold', fontSize=16, leading=19, alignment=TA_CENTER),
    'kpi_label': ParagraphStyle('kpi_label', parent=_BASE, fontSize=7.5, leading=9, textColor=MUTED,
                                alignment=TA_CENTER),
}

_REPLACEMENTS = str.maketrans({'→': '>', '←': '<', '✓': 'OK', '✅': 'OK', '❌': 'X', ' ': ' ', '‑': '-'})


# ─────────────────────────────────────────────────────────────────────────────
# Briques de mise en page
# ─────────────────────────────────────────────────────────────────────────────

def _plain(value):
    """Texte imprimable avec les polices PDF standard (Windows-1252)."""
    text = '—' if value in (None, '') else str(value)
    text = text.translate(_REPLACEMENTS)
    return text.encode('cp1252', 'replace').decode('cp1252')


def _clip(value, limit):
    text = _plain(value)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _p(value, style='base', color=None, bold=False, limit=None):
    text = escape(_clip(value, limit) if limit else _plain(value)).replace('\n', '<br/>')
    if bold:
        text = f'<b>{text}</b>'
    if color is not None:
        text = f'<font color="{color.hexval().replace("0x", "#")}">{text}</font>'
    return Paragraph(text, STYLES[style])


class BrandedCanvas(canvas.Canvas):
    """Canvas qui pose en-tête, pied de page et « Page x / y » sur chaque page."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        for state in self._pages:
            self.__dict__.update(state)
            self._draw_branding(len(self._pages))
            super().showPage()
        super().save()

    def _draw_branding(self, total):
        page_w, page_h = A4
        logo_h = 1.75 * cm
        logo_w = logo_h * LOGO_RATIO
        # Logo centré
        self.drawImage(str(LOGO_PATH), (page_w - logo_w) / 2, page_h - 0.55 * cm - logo_h,
                       width=logo_w, height=logo_h)
        self.setStrokeColor(NAVY)
        self.setLineWidth(1)
        self.line(MARGIN, page_h - 2.55 * cm, page_w - MARGIN, page_h - 2.55 * cm)
        # Pied de page
        self.setStrokeColor(CYAN)
        self.line(MARGIN, 1.75 * cm, page_w - MARGIN, 1.75 * cm)
        self.setFillColor(NAVY)
        for line, y in zip(COMPANY_FOOTER_LINES, (1.35 * cm, 1.0 * cm)):
            size = 6.8
            while self.stringWidth(line, 'Helvetica', size) > WIDTH:
                size -= 0.1
            self.setFont('Helvetica', size)
            self.drawCentredString(page_w / 2, y, line)
        self.setFillColor(MUTED)
        self.setFont('Helvetica', 7)
        self.drawCentredString(page_w / 2, 0.55 * cm, f'Page {self._pageNumber} / {total}')


def _render(title, story):
    # Un titre n'est jamais laissé seul en bas de page.
    flow = []
    for item in story:
        if isinstance(item, Paragraph) and item.style.name in ('h2', 'h3'):
            flow.append(CondPageBreak(3.5 * cm))
        flow.append(item)
    buffer = BytesIO()
    # Le cadre ReportLab ajoute 6 pt de marge interne : on les compense pour aligner texte et tableaux.
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN - 6, rightMargin=MARGIN - 6,
        topMargin=3.0 * cm, bottomMargin=2.2 * cm, title=_plain(title), author=COMPANY_NAME,
    ).build(flow, canvasmaker=BrandedCanvas)
    return buffer.getvalue()


def _heading(title, subtitle, meta):
    rows = [[_p(k, 'muted', bold=True), _p(v, 'muted')] for k, v in meta]
    table = Table(rows, colWidths=[3.4 * cm, WIDTH - 3.4 * cm])
    table.setStyle(TableStyle([('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    return [_p(title, 'title'), _p(subtitle, 'subtitle'), table]


def _kpis(items):
    """Bandeau d'indicateurs : [(libellé, valeur, couleur)]."""
    count = len(items)
    table = Table([[_p(v, 'kpi', color=c) for _, v, c in items], [_p(label, 'kpi_label') for label, _, _ in items]],
                  colWidths=[WIDTH / count] * count)
    table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, GRID), ('LINEAFTER', (0, 0), (-2, -1), 0.6, GRID),
        ('BACKGROUND', (0, 0), (-1, -1), ZEBRA),
        ('TOPPADDING', (0, 0), (-1, 0), 6), ('BOTTOMPADDING', (0, 1), (-1, 1), 6),
    ]))
    return table


def _table(headers, rows, widths, empty='Aucune donnée'):
    total = sum(widths)
    widths = [WIDTH * w / total for w in widths]
    body = [[cell if isinstance(cell, Paragraph) else _p(cell, 'cell') for cell in row] for row in rows]
    if not body:
        body = [[_p(empty, 'muted')] + [''] * (len(headers) - 1)]
    table = Table([[_p(h, 'head') for h in headers]] + body, colWidths=widths, repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, GRID), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ZEBRA]),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]
    if not rows:
        style.append(('SPAN', (0, 1), (-1, 1)))
    table.setStyle(TableStyle(style))
    return table


def _status(value):
    return _p(value, 'cell', color=STATUS_COLORS.get(value, INK), bold=True)


def _fr_date(value, fmt='d/m/Y'):
    if isinstance(value, str):
        from django.utils.dateparse import parse_date, parse_datetime
        value = parse_date(value) if len(value) == 10 else parse_datetime(value)
    if value is None:
        return '—'
    if hasattr(value, 'hour'):
        value = timezone.localtime(value)
    return date_format(value, fmt)


# ─────────────────────────────────────────────────────────────────────────────
# Graphiques
# ─────────────────────────────────────────────────────────────────────────────

def _legend(drawing, items, x, y):
    for label, color in items:
        drawing.add(Rect(x, y, 8, 8, fillColor=color, strokeColor=None))
        drawing.add(String(x + 12, y + 1, _plain(label), fontName='Helvetica', fontSize=8, fillColor=INK))
        x += 26 + len(label) * 4.4


def _line_chart(points, series, percent):
    """series : [(clé, libellé, couleur)] — un seul axe par graphique."""
    height = 5.2 * cm
    drawing = Drawing(WIDTH, height)
    if len(points) < 2:
        points = points * 2
    plot = LinePlot()
    plot.x, plot.y, plot.width, plot.height = 30, 24, WIDTH - 45, height - 48
    plot.data = [[(i, p[key]) for i, p in enumerate(points)] for key, _, _ in series]
    for i, (_, _, color) in enumerate(series):
        plot.lines[i].strokeColor = color
        plot.lines[i].strokeWidth = 1.8
    top = 100 if percent else max(5, max(p[key] for p in points for key, _, _ in series))
    step = 25 if percent else max(1, -(-top // 5))
    plot.yValueAxis.valueMin, plot.yValueAxis.valueStep = 0, step
    plot.yValueAxis.valueMax = top if percent else step * 5
    plot.yValueAxis.labelTextFormat = '%d%%' if percent else '%d'
    plot.yValueAxis.visibleGrid = True
    plot.yValueAxis.gridStrokeColor = GRID
    plot.yValueAxis.gridStrokeWidth = 0.4
    plot.yValueAxis.strokeColor = None
    plot.xValueAxis.valueMin, plot.xValueAxis.valueMax = 0, len(points) - 1
    plot.xValueAxis.valueSteps = sorted({round(i * (len(points) - 1) / 5) for i in range(6)})
    plot.xValueAxis.labelTextFormat = lambda v: _fr_date(points[int(round(v))]['date'], 'd/m/y')
    plot.xValueAxis.strokeColor = MUTED
    for axis in (plot.xValueAxis, plot.yValueAxis):
        axis.labels.fontName, axis.labels.fontSize, axis.labels.fillColor = 'Helvetica', 7, MUTED
    drawing.add(plot)
    _legend(drawing, [(label, color) for _, label, color in series], 30, height - 12)
    return drawing


def _status_bar(stats):
    """Barre unique empilée : répartition des cas par statut."""
    drawing = Drawing(WIDTH, 1.3 * cm)
    total, x = stats['total'] or 1, 0
    visible = [(key, label) for key, label in STATUS_KEYS if stats[key]]
    for key, label in visible:
        width = WIDTH * stats[key] / total
        drawing.add(Rect(x, 18, max(width - 2, 0.5), 14, fillColor=STATUS_COLORS[label], strokeColor=None))
        x += width
    _legend(drawing, [(f'{label} ({stats[key]})', STATUS_COLORS[label]) for key, label in visible], 0, 3)
    return drawing


def _sprint_chart(sprints):
    """Taux de réussite par sprint (barres horizontales, une seule mesure)."""
    sprints = [s for s in sprints if s['stats']['total']][-12:]
    if not sprints:
        return None
    height = 26 * len(sprints) + 24
    drawing = Drawing(WIDTH, height)
    chart = HorizontalBarChart()
    chart.x, chart.y, chart.width, chart.height = 120, 18, WIDTH - 165, height - 22
    chart.data = [[s['stats']['success_rate'] for s in reversed(sprints)]]
    chart.categoryAxis.categoryNames = [_clip(s['name'], 24) for s in reversed(sprints)]
    chart.bars[0].fillColor, chart.bars[0].strokeColor = BLUE, None
    chart.barWidth, chart.groupSpacing = 10, 8
    chart.barLabelFormat = '%.1f%%'
    chart.barLabels.fontName = 'Helvetica'
    chart.barLabels.nudge, chart.barLabels.fontSize, chart.barLabels.fillColor = 16, 7.5, INK
    chart.valueAxis.valueMin, chart.valueAxis.valueMax, chart.valueAxis.valueStep = 0, 100, 25
    chart.valueAxis.labelTextFormat = '%d%%'
    chart.valueAxis.visibleGrid, chart.valueAxis.gridStrokeColor = True, GRID
    chart.categoryAxis.strokeColor = MUTED
    chart.valueAxis.strokeColor = None
    for axis in (chart.categoryAxis, chart.valueAxis):
        axis.labels.fontName, axis.labels.fontSize, axis.labels.fillColor = 'Helvetica', 7.5, MUTED
    drawing.add(chart)
    return drawing


# ─────────────────────────────────────────────────────────────────────────────
# Rapport projet / sprint(s)
# ─────────────────────────────────────────────────────────────────────────────

def _performance_section(perf):
    stats, defects = perf['stats'], perf['defects']
    score = '—' if perf['score'] is None else f'{perf["score"]:g}/100'
    level_color = LEVEL_COLORS[perf['level']]
    labels = {
        'pass_rate': ('Réussite des cas exécutés', 'Cas passés / cas exécutés'),
        'execution_rate': ('Avancement', 'Cas exécutés / cas prévus'),
        'resolution_rate': ('Résolution des anomalies', 'Anomalies résolues ou fermées / total'),
        'stability_rate': ('Stabilité', 'Part des cas exécutés sans régression'),
    }
    rows = [[labels[k][0], labels[k][1], f'{perf["weights"][k]} %',
             _p(f'{perf["components"][k]:g} %', 'cell', bold=True)] for k in perf['weights']]
    extra = [f'Automatisation : {perf["automation_rate"]:g} % ({perf["automated"]} cas)',
             f'Densité d\'anomalies : {perf["defect_density"]:g} par cas exécuté']
    if defects['avg_resolution_days'] is not None:
        extra.append(f'Délai moyen de résolution : {defects["avg_resolution_days"]:g} jour(s)')
    return [
        _p('Niveau de performance des tests', 'h2'),
        _kpis([('Score global', score, level_color), ('Niveau', perf['level_label'], level_color),
               ('Taux de réussite', f'{stats["success_rate"]:g} %', GREEN),
               ('Avancement', f'{stats["execution_rate"]:g} %', BLUE)]),
        Spacer(1, 6),
        _table(['Composante', 'Mode de calcul', 'Poids', 'Résultat'], rows, [4, 6, 1.4, 1.8]),
        Spacer(1, 4),
        _p('  |  '.join(extra), 'muted'),
    ]


def generate_project_report(project, sprints, generated_by) -> bytes:
    """Rapport PDF d'un projet (sprints=None), d'un sprint ou de plusieurs sprints."""
    from apps.qamanagement.analytics import build_report
    data = build_report(project, sprints)
    perf, stats = data['performance'], data['performance']['stats']
    defects, scope = perf['defects'], data['scope']
    titles = {'project': 'Rapport de projet', 'sprint': 'Rapport de sprint', 'sprints': 'Rapport multi-sprints'}

    meta = [('Équipe', data['project']['team']), ('Projet', project.name), ('Périmètre', scope['label'])]
    if sprints:
        meta.append(('Période', f'du {_fr_date(min(s.start_date for s in sprints))} '
                                f'au {_fr_date(max(s.end_date for s in sprints))}'))
        if len(sprints) > 1:
            meta.append(('Sprints', ', '.join(s.name for s in sprints)))
        elif sprints[0].goal:
            meta.append(('Objectif', sprints[0].goal))
    meta += [('Généré par', generated_by), ('Date', _fr_date(timezone.now(), 'd/m/Y à H:i'))]

    story = _heading(titles[scope['type']], f'{project.name} — {scope["label"]}', meta)
    story += [
        _p('Synthèse', 'h2'),
        _kpis([('Cas de test', stats['total'], INK), ('Passés', stats['passed'], GREEN),
               ('Échoués', stats['failed'], RED), ('Bloqués', stats['blocked'], AMBER),
               ('Anomalies actives', defects['active'], RED), ('Régressions', perf['regressed_cases'], AMBER)]),
        Spacer(1, 8),
    ]
    if stats['total']:
        story.append(_status_bar(stats))
    story.append(KeepTogether(_performance_section(perf)))

    evolution = data['evolution']
    sign = '+' if evolution['trend'] > 0 else ''
    story += [
        KeepTogether([
            _p("Courbe d'évolution", 'h2'),
            _p(f'Du {_fr_date(evolution["start"])} au {_fr_date(evolution["end"])} — évolution du taux de réussite : '
               f'{sign}{evolution["trend"]:g} point(s)', 'muted'),
            _line_chart(evolution['points'], [('success_rate', 'Taux de réussite', GREEN),
                                              ('execution_rate', 'Avancement', BLUE)], percent=True),
        ]),
        KeepTogether([
            _p('Anomalies dans le temps', 'h3'),
            _line_chart(evolution['points'], [('defects_open', 'Anomalies actives', RED),
                                              ('defects_resolved', 'Anomalies résolues (cumul)', SKY)], percent=False),
        ]),
    ]

    story.append(_p('Résultats par sprint', 'h2'))
    story.append(_table(
        ['Sprint', 'Période', 'Statut', 'Cas', 'Passés', 'Échoués', 'Réussite', 'Anom.', 'Régr.', 'Score'],
        [[s['name'], f'{_fr_date(s["start_date"])} - {_fr_date(s["end_date"])} ({s["duration_days"]} j)',
          s['status_label'], s['stats']['total'], s['stats']['passed'], s['stats']['failed'],
          f'{s["stats"]["success_rate"]:g} %', s['defects']['total'], s['regressed_cases'],
          '—' if s['score'] is None else f'{s["score"]:g}'] for s in data['sprints']],
        [3.2, 3.9, 1.8, 1.1, 1.6, 1.8, 1.9, 1.4, 1.3, 1.4], empty='Aucun sprint'))
    if len(data['sprints']) > 1:
        chart = _sprint_chart(data['sprints'])
        if chart:
            story += [KeepTogether([_p('Taux de réussite par sprint', 'h3'), chart])]

    story.append(_p(f'Anomalies ({defects["total"]})', 'h2'))
    story.append(_kpis([('Ouvertes', defects['open'] + defects['reopened'], RED), ('En cours', defects['in_progress'], AMBER),
                        ('Résolues', defects['resolved'], SKY), ('Fermées', defects['closed'], SLATE),
                        ('Critiques', defects['critical'], RED), ('Majeures', defects['major'], AMBER)]))
    story.append(Spacer(1, 6))
    story.append(_table(
        ['Ticket', 'Anomalie', 'Cas', 'Sprint', 'Sévérité', 'Statut', 'Assignée à'],
        [[d['ticket_number'], _p(('[Régression] ' if d['is_regression'] else '') + d['title'], 'cell', limit=140),
          ', '.join(f'CAS-{n:03d}' for n in d['case_numbers']), d['sprint_name'],
          _p(d['severity_label'], 'cell', color=SEVERITY_COLORS[d['severity']], bold=True),
          d['status_label'], d['assigned_to']] for d in data['defects']],
        [2.5, 4.2, 1.8, 3, 1.7, 1.7, 2.1], empty='Aucune anomalie'))

    story.append(_p(f'Régressions ({len(data["regressions"])})', 'h2'))
    story.append(_table(
        ['Cas', 'Intitulé', 'Sprint', 'Détectée le', 'Occ.', 'Statut actuel', 'Anomalies liées'],
        [[f'CAS-{r["case_number"]:03d}', _p(r['title'], 'cell', limit=110), r['sprint_name'],
          _fr_date(r['last_detected_at']), r['occurrences'], _status(r['current_status']),
          ', '.join(d['ticket_number'] for d in r['defects'])] for r in data['regressions']],
        [1.8, 4.2, 3, 2, 1, 2, 2.8], empty='Aucune régression détectée'))

    story.append(_p('Répartition par membre', 'h2'))
    story.append(_table(
        ['Membre', 'Cas', 'Passés', 'Échoués', 'Bloqués', 'En cours', 'À tester', 'Avancement'],
        [[m['member'], m['total'], m['passed'], m['failed'], m['blocked'], m['in_progress'], m['not_run'],
          f'{m["execution_rate"]:g} %'] for m in data['members']],
        [4, 1.2, 1.5, 1.6, 1.6, 1.6, 1.6, 2], empty='Aucun cas assigné'))

    story.append(_p(f'Cas de test ({len(data["cases"])})', 'h2'))
    story.append(_table(
        ['Cas', 'Intitulé', 'Statut', 'Auto.', 'Jira', 'Anomalies'],
        [[f'CAS-{c["case_number"]:03d}', _p(c['title'], 'cell', limit=160), _status(c['status']),
          'Oui' if c['is_automated'] else 'Non', c['jira_ticket'], ', '.join(c['defects'])] for c in data['cases']],
        [1.8, 7, 1.9, 1.2, 2, 2.8], empty='Aucun cas de test'))

    return _render(f'{titles[scope["type"]]} — {project.name}', story)


# ─────────────────────────────────────────────────────────────────────────────
# Rapports d'équipe (Daily Scrum, hebdomadaire)
# ─────────────────────────────────────────────────────────────────────────────

MAX_CASES_PER_PROJECT = 50


def _team_story(team, user, detailed):
    projects = [p for p in Project.objects.filter(team=team)
                if user is None or can_view_project(user, p)]
    all_stats = [(p, p.stats) for p in projects]
    total = sum(s['total'] for _, s in all_stats)
    passed = sum(s['passed'] for _, s in all_stats)
    failed = sum(s['failed'] for _, s in all_stats)
    rate = round(passed / total * 100, 1) if total else 0

    story = [
        _p("Vue d'ensemble", 'h2'),
        _kpis([('Projets', len(projects), INK), ('Projets actifs', sum(p.status == 'active' for p in projects), BLUE),
               ('Cas de test', total, INK), ('Passés', passed, GREEN), ('Échoués', failed, RED),
               ('Taux de réussite', f'{rate:g} %', GREEN)]),
        _p('Projets', 'h2'),
        _table(['Projet', 'Statut', 'Cas', 'Passés', 'Échoués', 'Bloqués', 'En cours', 'À tester', 'Auto.', 'Réussite'],
               [[p.name, p.get_status_display(), s['total'], s['passed'], s['failed'], s['blocked'],
                 s['in_progress'], s['not_run'], s['automated'], f'{s["success_rate"]:g} %'] for p, s in all_stats],
               [4, 1.6, 1.1, 1.5, 1.6, 1.6, 1.6, 1.6, 1.2, 1.8], empty='Aucun projet'),
    ]
    for project, stats in all_stats:
        if not stats['total'] or (not detailed and project.status != 'active'):
            continue
        cases = ExtractedUseCase.objects.filter(project=project).order_by('case_number')[:MAX_CASES_PER_PROJECT]
        story += [
            _p(project.name, 'h3'), _status_bar(stats),
            _table(['Cas', 'Intitulé', 'Statut', 'Auto.'],
                   [[f'CAS-{c.case_number:03d}', _p(c.use_case_text or c.description, 'cell', limit=160),
                     _status(c.status), 'Oui' if c.is_automated else 'Non'] for c in cases],
                   [1.8, 9, 2, 1.3]),
        ]
        if stats['total'] > MAX_CASES_PER_PROJECT:
            story.append(_p(f'… et {stats["total"] - MAX_CASES_PER_PROJECT} autre(s) cas '
                            '(voir le rapport de projet).', 'muted'))

    members = TeamMember.objects.filter(team=team).select_related('user')
    story += [
        _p(f"Membres de l'équipe ({len(members)})", 'h2'),
        _table(['Membre', 'E-mail', 'Rôle', 'Depuis'],
               [[m.user.get_full_name() or m.user.username, m.user.email, m.get_role_display(),
                 _fr_date(m.joined_at)] for m in members], [4, 5, 2.5, 2]),
    ]
    return story


def generate_daily_scrum(team: Team, generated_by: str, user=None) -> bytes:
    now = timezone.now()
    story = _heading('Daily Scrum', team.name, [
        ('Date', _fr_date(now, 'l d F Y')), ('Généré par', generated_by)])
    return _render(f'Daily Scrum — {team.name}', story + _team_story(team, user, detailed=False))


def generate_weekly_report(team: Team, generated_by: str, user=None) -> bytes:
    today = timezone.localdate()
    start = today - timedelta(days=today.weekday())
    story = _heading('Rapport hebdomadaire', team.name, [
        ('Période', f'du {_fr_date(start)} au {_fr_date(start + timedelta(days=6))}'),
        ('Généré par', generated_by), ('Date', _fr_date(timezone.now(), 'd/m/Y à H:i'))])
    return _render(f'Rapport hebdomadaire — {team.name}', story + _team_story(team, user, detailed=True))
