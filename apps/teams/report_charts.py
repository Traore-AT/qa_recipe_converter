"""
Graphiques des rapports PDF (ReportLab), alignés sur la charte graphique de l'application :

- séries dans un ordre fixe (bleu, orange, aqua), statuts réservés (vert / ambre / rouge / gris) ;
- un seul axe par graphique, grille en trait fin continu, traits de 2 px, marques fines ;
- légende pour 2 séries ou plus, valeurs écrites avec parcimonie (fin de courbe, tête de barre) ;
- palette validée contre le daltonisme (vert et rouge jamais adjacents dans la répartition des statuts).

Tout est dessiné avec des formes simples : rendu identique sous Windows, Linux et Render.
"""
from datetime import date, datetime

from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth

SERIES = [colors.HexColor('#257de0'), colors.HexColor('#f47230'), colors.HexColor('#00b894')]
STATUS_ORDER = [  # (clé de statistiques, libellé, couleur, couleur du texte posé dessus)
    ('passed', 'Passés', colors.HexColor('#0aac23'), colors.white),
    ('in_progress', 'En cours', colors.HexColor('#257de0'), colors.white),
    ('blocked', 'Bloqués', colors.HexColor('#fbb315'), colors.HexColor('#0b0b0b')),
    ('failed', 'Échoués', colors.HexColor('#dc3c3d'), colors.white),
    ('not_run', 'À tester', colors.HexColor('#94a3b8'), colors.white),
]
INK = colors.HexColor('#1e293b')
INK_MUTED = colors.HexColor('#64748b')
GRID = colors.HexColor('#e7e7e7')
AXIS = colors.HexColor('#c2c2c2')
SURFACE = colors.white

FONT, FONT_BOLD = 'Helvetica', 'Helvetica-Bold'
HAIRLINE = 0.5
LINE_WIDTH = 1.6          # ≈ 2 px à l'impression
BAR = 7                   # épaisseur de barre (pt)
LABEL_GAP = 9             # écart vertical minimal entre deux libellés de fin de courbe


def _text(x, y, value, size=7.5, color=INK, bold=False, anchor='start'):
    return String(x, y, value, fontName=FONT_BOLD if bold else FONT, fontSize=size, fillColor=color, textAnchor=anchor)


def _pct(value):
    return f'{value:.1f}'.replace('.', ',').replace(',0', '') + ' %'


def _num(value):
    return f'{value:,.0f}'.replace(',', ' ')


def _day(value):
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime('%d/%m')


def _nice_top(value):
    """Borne haute « ronde » de l'axe (1, 2, 2,5 ou 5 × 10ⁿ) et son pas."""
    value = max(value, 1)
    magnitude = 10 ** (len(str(int(value))) - 1)
    for factor in (1, 2, 2.5, 5, 10):
        top = factor * magnitude
        if top >= value:
            step = top / 4 if factor in (1, 2, 10) else top / 5
            return top, step
    return value, value / 4


def legend(drawing, items, x, y, shape='line'):
    """Légende : repère (trait ou carré) de la couleur de la série + libellé en encre de texte."""
    for label, color in items:
        if shape == 'line':
            drawing.add(Line(x, y + 3, x + 12, y + 3, strokeColor=color, strokeWidth=2, strokeLineCap=1))
            offset = 16
        else:
            drawing.add(Rect(x, y, 7, 7, rx=1.5, ry=1.5, fillColor=color, strokeColor=None))
            offset = 11
        drawing.add(_text(x + offset, y + 0.5, label, size=7.5))
        x += offset + stringWidth(label, FONT, 7.5) + 14
    return x


def line_chart(points, series, percent, width, height=170):
    """
    Courbe d'évolution. series : [(clé, libellé, couleur)] — un seul axe.
    Fin de courbe : point cerclé + valeur ; libellés écartés (avec filet de rappel) s'ils se chevauchent.
    """
    drawing = Drawing(width, height)
    if not points:
        drawing.add(_text(0, height / 2, 'Aucune donnée sur la période', color=INK_MUTED))
        return drawing
    if len(points) == 1:
        points = points * 2
    left, right, bottom, top_pad = 34, 52, 18, 22
    plot_w, plot_h = width - left - right, height - bottom - top_pad
    values = [p[key] or 0 for p in points for key, _, _ in series]
    top, step = (100, 25) if percent else _nice_top(max(values))

    def px(i):
        return left + plot_w * i / (len(points) - 1)

    def py(v):
        return bottom + plot_h * min(max(v, 0), top) / top

    # Grille horizontale + graduations
    tick = 0
    while tick <= top + 1e-9:
        y = py(tick)
        drawing.add(Line(left, y, left + plot_w, y, strokeColor=AXIS if tick == 0 else GRID, strokeWidth=HAIRLINE))
        drawing.add(_text(left - 5, y - 2.5, _pct(tick) if percent else _num(tick), size=6.5, color=INK_MUTED, anchor='end'))
        tick += step
    # Dates : au plus 6, réparties
    count = min(6, len(points))
    for k in range(count):
        i = round(k * (len(points) - 1) / max(count - 1, 1))
        drawing.add(_text(px(i), bottom - 11, _day(points[i]['date']), size=6.5, color=INK_MUTED, anchor='middle'))

    ends = []
    for key, label, color in series:
        coords = []
        for i, p in enumerate(points):
            coords += [px(i), py(p[key] or 0)]
        drawing.add(PolyLine(coords, strokeColor=color, strokeWidth=LINE_WIDTH, strokeLineJoin=1, strokeLineCap=1))
        ends.append([coords[-1], coords[-2], points[-1][key] or 0, color])

    # Libellés de fin : du haut vers le bas, écartés d'au moins LABEL_GAP, filet de rappel si déplacés.
    placed = sorted(ends, key=lambda e: -e[0])
    previous = None
    for end in placed:
        y, x, value, color = end
        label_y = y if previous is None else min(y, previous - LABEL_GAP)
        label_y = max(label_y, bottom + 2)
        if abs(label_y - y) > 1.5:
            drawing.add(Line(x + 4, y, x + 9, label_y + 2.5, strokeColor=AXIS, strokeWidth=HAIRLINE))
        drawing.add(Circle(x, y, 3, fillColor=color, strokeColor=SURFACE, strokeWidth=1.5))
        drawing.add(_text(x + 11, label_y, _pct(value) if percent else _num(value), size=7, bold=True))
        previous = label_y

    legend(drawing, [(label, color) for _, label, color in series], left, height - 10)
    return drawing


def status_bar(stats, width):
    """Répartition des cas par statut : barre segmentée (blocs séparés de 1,5 pt), part dans les blocs larges."""
    total = stats.get('total') or 0
    parts = [(key, label, fill, ink, stats.get(key, 0)) for key, label, fill, ink in STATUS_ORDER if stats.get(key)]
    height, bar_h, gap = 46, 18, 1.5
    drawing = Drawing(width, height)
    if not total or not parts:
        drawing.add(_text(0, height / 2, 'Aucun cas de test', color=INK_MUTED))
        return drawing
    usable = width - gap * (len(parts) - 1)
    x, y = 0, height - bar_h
    for key, label, fill, ink, count in parts:
        w = max(usable * count / total, 2)
        drawing.add(Rect(x, y, w, bar_h, rx=3, ry=3, fillColor=fill, strokeColor=None))
        share = _pct(count / total * 100)
        if stringWidth(share, FONT_BOLD, 7.5) + 10 <= w:
            drawing.add(_text(x + 5, y + 5.5, share, size=7.5, color=ink, bold=True))
        x += w + gap
    # Légende : nom + effectif + part (la valeur d'un bloc étroit y figure toujours)
    lx = 0
    for key, label, fill, ink, count in parts:
        text = f'{label} {count} ({_pct(count / total * 100)})'
        drawing.add(Rect(lx, 6, 7, 7, rx=1.5, ry=1.5, fillColor=fill, strokeColor=None))
        drawing.add(_text(lx + 11, 6.5, text, size=7.5))
        lx += 11 + stringWidth(text, FONT, 7.5) + 14
    return drawing


def _bar(drawing, x0, y, length, color):
    """Barre horizontale : extrémité de donnée arrondie, base carrée."""
    if length <= 0:
        return
    radius = min(2.5, length / 2, BAR / 2)
    drawing.add(Rect(x0, y, length, BAR, rx=radius, ry=radius, fillColor=color, strokeColor=None))
    drawing.add(Rect(x0, y, min(length, radius + 0.5), BAR, fillColor=color, strokeColor=None))


def sprint_chart(sprints, width, max_sprints=12):
    """Réussite et avancement par sprint (barres horizontales groupées, axe 0–100 %)."""
    rows = [s for s in sprints if s['stats']['total']][-max_sprints:]
    if not rows:
        return None
    label_w, right, legend_h, axis_h = 118, 36, 16, 14
    group_h = 2 * BAR + 1.5 + 9
    height = legend_h + axis_h + group_h * len(rows)
    drawing = Drawing(width, height)
    plot_w = width - label_w - right
    plot_top = height - legend_h

    for tick in (0, 25, 50, 75, 100):
        x = label_w + plot_w * tick / 100
        drawing.add(Line(x, axis_h, x, plot_top, strokeColor=AXIS if tick == 0 else GRID, strokeWidth=HAIRLINE))
        drawing.add(_text(x, axis_h - 10, _pct(tick), size=6.5, color=INK_MUTED, anchor='middle'))

    for i, s in enumerate(rows):
        top = plot_top - i * group_h - 4.5
        name = s['name'] if len(s['name']) <= 26 else s['name'][:25] + '…'
        drawing.add(_text(label_w - 6, top - BAR - 2.5, name, size=7, color=INK, anchor='end'))
        success, progress = s['stats']['success_rate'], s['stats']['execution_rate']
        _bar(drawing, label_w, top - BAR, plot_w * success / 100, SERIES[0])
        _bar(drawing, label_w, top - 2 * BAR - 1.5, plot_w * progress / 100, SERIES[1])
        drawing.add(_text(label_w + plot_w * success / 100 + 4, top - BAR + 1.5, _pct(success), size=6.5, bold=True))

    legend(drawing, [('Taux de réussite', SERIES[0]), ('Avancement', SERIES[1])], label_w, height - 9, shape='square')
    return drawing
