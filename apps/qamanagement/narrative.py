"""
Phrases clés des rapports : introduction, points clés, cas en échec et plan d'action, répartition
des actions, recommandations, niveau de satisfaction et conclusion.

Tout est rédigé à partir des données du rapport (statuts, commentaires et résultats observés des cas
en échec, anomalies, régressions, responsables) : le texte reste conforme aux chiffres présentés.

- `project_narrative(project, sprints, data)` : rapport d'un sprint, de plusieurs sprints ou du projet ;
- `portfolio_narrative(team, rows, kind)` : rapport global de l'ensemble des projets d'une équipe.
"""
import unicodedata
from collections import defaultdict

from django.utils import timezone
from django.utils.formats import date_format

from apps.core.models import ExtractedUseCase
from .models import UseCaseAssignment

ATTENTION = ('Échoué', 'Bloqué')
COMMENT_EXCERPT = 220

# Thèmes repérés dans les commentaires et résultats observés des cas en échec → recommandation ciblée.
THEMES = [
    (('upload', 'telecharg', 'fichier', '413', 'volumineux', 'piece jointe', 'taille'),
     "Vérifier la gestion des fichiers (taille maximale, formats acceptés, limite d'envoi côté serveur)"),
    (('erreur 500', ' 500', 'erreur serveur', 'exception', 'internal server', 'plantage', 'crash'),
     'Analyser les journaux applicatifs pour identifier la cause des erreurs serveur'),
    (('lent', 'timeout', 'temps de reponse', 'chargement', 'performance', 'freeze'),
     'Mesurer et optimiser les temps de réponse sur les parcours concernés'),
    (('droit', 'privilege', 'permission', 'acces', 'role', 'autoris', '403'),
     'Revoir la configuration des droits et des privilèges des profils concernés'),
    (('connexion', 'login', 'mot de passe', 'session', 'authentif', 'deconnex'),
     "Contrôler le parcours d'authentification (connexion, session, mot de passe)"),
    (('workflow', 'statut', 'validation', 'circuit', 'doublon'),
     'Sécuriser le workflow métier (enchaînement des statuts, validations, contrôle des doublons)'),
    (('mail', 'notification', 'sms', 'alerte'),
     "Vérifier l'envoi et le contenu des notifications"),
    (('affich', 'logo', 'bouton', 'ergonom', 'interface', 'design', 'alignement', 'responsive', 'loader', 'icone'),
     "Corriger les défauts d'interface et d'ergonomie relevés"),
    (('donnee', 'enregistr', 'calcul', 'montant', 'score', 'export', 'import'),
     "Contrôler l'intégrité des données et l'exactitude des calculs"),
]

SATISFACTION = {
    'very_satisfying': 'Très satisfaisant',
    'satisfying': 'Satisfaisant',
    'partial': 'Partiellement satisfaisant',
    'insufficient': 'Insuffisant',
    'not_evaluated': 'Non évaluable',
}

FULL_SUCCESS_NOTE = (
    "Ce taux de 100 % porte sur le périmètre testé : conformément aux principes du test logiciel, il atteste "
    "qu'aucun défaut n'a été détecté sur les cas exécutés, sans garantir l'absence totale de défauts. "
    'Une vigilance reste de mise lors de la mise en production (tests de non-régression, suivi des retours utilisateurs).'
)


# ─────────────────────────────────────────────────────────────────────────────
# Mise en forme française
# ─────────────────────────────────────────────────────────────────────────────

def pct(value):
    """75.0 → « 75,0 % »."""
    return f'{value:.1f}'.replace('.', ',') + ' %'


def cap(text):
    """Majuscule initiale sans modifier le reste (« CAS-001 », « FP-12 » restent intacts)."""
    return text[:1].upper() + text[1:]


def num(value):
    """78.5 → « 78,5 » ; 80.0 → « 80 »."""
    return f'{value:g}'.replace('.', ',')


NUMBER_WORDS = ['zéro', 'un', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf', 'dix',
                'onze', 'douze']


def plural(count, singular, plural_form=None, spell=False, feminine=False):
    """« 2 cas bloqués » ; spell=True : nombre en toutes lettres jusqu'à douze (« deux cas bloqués »)."""
    number = str(count)
    if spell and count < len(NUMBER_WORDS):
        number = 'une' if feminine and count == 1 else NUMBER_WORDS[count]
    return f'{number} {singular if count <= 1 else (plural_form or singular + "s")}'


def join(items, last='et'):
    items = [str(i) for i in items if i]
    if len(items) <= 1:
        return ''.join(items)
    return f'{", ".join(items[:-1])} {last} {items[-1]}'


def case_label(number):
    return f'CAS-{number:03d}'


def case_ref(item):
    return case_label(item['case_number']) + (f' ({item["jira_ticket"]})' if item.get('jira_ticket') else '')


def refs(items, limit=8):
    shown = [case_ref(i) for i in items[:limit]]
    rest = len(items) - limit
    return join(shown) + (f' et {plural(rest, "autre")}' if rest > 0 else '')


def fr_date(value):
    return date_format(value, 'd/m/Y') if value else ''


def _normalize(text):
    text = unicodedata.normalize('NFD', (text or '').lower())
    return ' ' + ''.join(c for c in text if unicodedata.category(c) != 'Mn') + ' '


def _excerpt(text, limit=COMMENT_EXCERPT):
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


# ─────────────────────────────────────────────────────────────────────────────
# Évaluation
# ─────────────────────────────────────────────────────────────────────────────

def satisfaction(stats, critical_open=0):
    """Niveau de satisfaction : réussite globale, avancement et anomalies critiques ouvertes."""
    if not stats['executed']:
        code = 'not_evaluated'
    elif stats['success_rate'] >= 95 and stats['execution_rate'] >= 100 and not critical_open:
        code = 'very_satisfying'
    elif stats['success_rate'] >= 80 and not critical_open:
        code = 'satisfying'
    elif stats['success_rate'] >= 60:
        code = 'partial'
    else:
        code = 'insufficient'
    return {'code': code, 'label': SATISFACTION[code]}


def verdict(stats, critical_open=0):
    """Qualification de la recette (phrase de conclusion)."""
    if not stats['executed']:
        return 'non engagée'
    if stats['passed'] == stats['total']:
        return 'validée'
    if not (stats['failed'] or stats['blocked']):
        return 'en cours (aucun échec à ce stade)'
    if stats['success_rate'] >= 80 and not critical_open:
        return 'validée avec réserves'
    if stats['success_rate'] >= 50:
        return 'partielle'
    return 'non validée'


# ─────────────────────────────────────────────────────────────────────────────
# Cas en échec : commentaires, responsables, anomalies
# ─────────────────────────────────────────────────────────────────────────────

def attention_cases(project, sprints, statuses):
    """Cas échoués ou bloqués du périmètre, avec ce qu'en disent les testeurs et qui doit agir."""
    ids = [cid for cid, status in statuses.items() if status in ATTENTION]
    if not ids:
        return []
    cases = (ExtractedUseCase.objects.filter(id__in=ids).order_by('case_number')
             .prefetch_related('comments__author', 'defects__assigned_to', 'related_defects__assigned_to'))
    assignments = UseCaseAssignment.objects.filter(use_case_id__in=ids, assigned_to__isnull=False)
    if sprints is not None:
        assignments = assignments.filter(sprint__in=sprints)
    testers = defaultdict(list)
    for a in assignments.select_related('assigned_to__user').order_by('assigned_at'):
        name = a.assigned_to.user.get_full_name() or a.assigned_to.user.username
        if name not in testers[a.use_case_id]:
            testers[a.use_case_id].append(name)

    sprint_ids = None if sprints is None else {s.id for s in sprints}
    items = []
    for case in cases:
        comments = sorted(case.comments.all(), key=lambda c: c.created_at)
        defects = {d.id: d for d in (*case.defects.all(), *case.related_defects.all())
                   if sprint_ids is None or d.sprint_id in (None, *sprint_ids)}
        open_defects = [d for d in defects.values() if d.is_open]
        last = comments[-1] if comments else None
        items.append({
            'case_number': case.case_number,
            'title': case.use_case_text or case.description,
            'jira_ticket': case.jira_ticket,
            'status': statuses[case.id],
            'observed': _excerpt(case.observed_results),
            'comment': _excerpt(last.content) if last else '',
            'comment_author': (last.author.get_full_name() or last.author.username) if last else '',
            'comments_text': ' '.join(c.content for c in comments),
            'testers': testers.get(case.id, []),
            'defects': [{'ticket_number': d.ticket_number, 'title': d.title, 'severity': d.severity,
                         'severity_label': d.get_severity_display(), 'status_label': d.get_status_display(),
                         'assigned_to': (d.assigned_to.get_full_name() or d.assigned_to.username) if d.assigned_to else ''}
                        for d in open_defects],
            'action': 'Corriger puis retester' if statuses[case.id] == 'Échoué' else 'Lever le blocage puis exécuter',
        })
    return items


def responsibilities(items, members):
    """Qui prend en charge quoi : retests, corrections, cas restant à exécuter."""
    plan = defaultdict(lambda: {'fix': [], 'unblock': [], 'retest': [], 'run': [], 'execute': 0})
    for item in items:
        failed = item['status'] == 'Échoué'
        developers = {d['assigned_to'] for d in item['defects'] if d['assigned_to']}
        for dev in developers or {'À attribuer (développement)'}:
            plan[dev]['fix' if failed else 'unblock'].append(item)
        for tester in item['testers'] or ['À attribuer (test)']:
            plan[tester]['retest' if failed else 'run'].append(item)
    for member in members:
        remaining = member['not_run'] + member['in_progress']
        if remaining and member['member'] != 'Non assigné':
            plan[member['member']]['execute'] = remaining
        elif remaining:
            plan['À attribuer (test)']['execute'] += remaining

    rows = []
    for owner in sorted(plan, key=lambda o: (o.startswith('À attribuer'), o.lower())):
        tasks = plan[owner]
        actions = []
        if tasks['fix']:
            actions.append(f'corriger {refs(tasks["fix"], 6)}')
        if tasks['unblock']:
            actions.append(f'lever le blocage de {refs(tasks["unblock"], 6)}')
        if tasks['retest']:
            actions.append(f'retester {refs(tasks["retest"], 6)} après correction')
        if tasks['run']:
            actions.append(f'exécuter {refs(tasks["run"], 6)} après déblocage')
        if tasks['execute']:
            actions.append(f'exécuter {plural(tasks["execute"], "cas restant", "cas restants")}')
        if actions:
            rows.append({'owner': owner, 'actions': cap(join(actions)) + '.'})
    return rows


def comment_themes(items):
    """Recommandations ciblées à partir des commentaires et résultats observés des cas en échec."""
    found = []
    for keywords, advice in THEMES:
        matched = [i for i in items
                   if any(k in _normalize(f'{i["observed"]} {i["comments_text"]}') for k in keywords)]
        if matched:
            found.append(f'{advice} — {refs(matched, 5)}.')
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Rapport d'un sprint, de plusieurs sprints ou du projet
# ─────────────────────────────────────────────────────────────────────────────

def project_narrative(project, sprints, data):
    from .analytics import scope_case_statuses
    perf, stats = data['performance'], data['performance']['stats']
    defects = perf['defects']
    kind = data['scope']['type']
    items = attention_cases(project, sprints, scope_case_statuses(project, sprints))
    failed = [i for i in items if i['status'] == 'Échoué']
    blocked = [i for i in items if i['status'] == 'Bloqué']
    critical_open = sum(1 for i in items for d in i['defects'] if d['severity'] == 'critical')
    critical_open = max(critical_open, _critical_active(data['defects']))
    level = satisfaction(stats, critical_open)
    regressions = data['regressions']

    # ── Introduction ────────────────────────────────────────────────────────
    team = data['project']['team']
    if kind == 'sprint':
        sprint = sprints[0]
        intro = [f"Ce rapport présente les résultats de la campagne de test du sprint « {sprint.name} » du projet "
                 f"{project.name}, conduite du {fr_date(sprint.start_date)} au {fr_date(sprint.end_date)} "
                 f"par l'équipe {team}."]
        if sprint.goal:
            intro.append(f'L\'objectif du sprint était : « {_excerpt(sprint.goal, 300)} ».')
    elif kind == 'sprints':
        start, end = min(s.start_date for s in sprints), max(s.end_date for s in sprints)
        intro = [f'Ce rapport consolide les résultats de {len(sprints)} sprints du projet {project.name} '
                 f'({join(s.name for s in sorted(sprints, key=lambda s: s.start_date))}), '
                 f'sur la période du {fr_date(start)} au {fr_date(end)}.']
    else:
        count = len(data['sprints'])
        intro = [f"Ce rapport présente la synthèse de l'ensemble des activités de test du projet {project.name} "
                 f"(équipe {team}), couvrant {plural(count, 'sprint')} à la date du {fr_date(timezone.localdate())}."]
    intro.append(f'Le périmètre couvre {plural(stats["total"], "cas de test", "cas de test")}, '
                 f'dont {stats["executed"]} exécutés ({pct(stats["execution_rate"])}).' if stats['total']
                 else "Aucun cas de test n'est encore rattaché à ce périmètre.")
    intro.append('Il dresse le bilan des résultats obtenus, détaille les cas en échec et les actions attendues '
                 'de chacun, puis formule des recommandations pour la suite.')

    # ── Points clés ─────────────────────────────────────────────────────────
    key_points = []
    if stats['total']:
        key_points.append(f'Taux de réussite global : {pct(stats["success_rate"])} '
                          f'({stats["passed"]} / {stats["total"]} cas validés).')
        if stats['executed'] and stats['executed'] < stats['total']:
            key_points.append(f'Sur les cas exécutés, le taux de réussite atteint {pct(stats["pass_rate"])} ; '
                              f'{plural(stats["total"] - stats["executed"], "cas reste", "cas restent")} à exécuter.')
    if items:
        parts = [plural(len(failed), 'cas en échec', 'cas en échec', spell=True) if failed else '',
                 plural(len(blocked), 'cas bloqué', 'cas bloqués', spell=True) if blocked else '']
        key_points.append(f'{cap(join(parts))} {"nécessite" if len(items) == 1 else "nécessitent"} '
                          "une action (voir « Cas en échec et plan d'action »).")
    if defects['active']:
        active_rows = [d for d in data['defects'] if d['status'] not in ('resolved', 'closed')]
        critical_n = sum(d['severity'] == 'critical' for d in active_rows)
        major_n = sum(d['severity'] == 'major' for d in active_rows)
        severity = join([plural(critical_n, 'critique', 'critiques') if critical_n else '',
                         plural(major_n, 'majeure', 'majeures') if major_n else ''])
        key_points.append(cap(plural(defects['active'], 'anomalie active', 'anomalies actives', spell=True,
                                     feminine=True)) + (f', dont {severity}.' if severity else '.'))
    elif stats['executed']:
        key_points.append('Aucune anomalie active sur le périmètre.')
    if regressions:
        key_points.append(cap(plural(len(regressions), 'cas a régressé', 'cas ont régressé', spell=True))
                          + (' (validé auparavant, en échec depuis).' if len(regressions) == 1
                             else ' (validés auparavant, en échec depuis).'))
    rated = [s for s in data['sprints'] if s['stats']['executed']]
    if kind != 'sprint' and len(rated) > 1:
        best = max(rated, key=lambda s: s['stats']['success_rate'])
        worst = min(rated, key=lambda s: s['stats']['success_rate'])
        key_points.append(f'Meilleur résultat : sprint « {best["name"]} » ({pct(best["stats"]["success_rate"])}) ; '
                          f'le plus en difficulté : « {worst["name"]} » ({pct(worst["stats"]["success_rate"])}).')
        trend = data['evolution']['trend']
        if trend:
            key_points.append(f'Le taux de réussite a évolué de {"+" if trend > 0 else ""}{num(trend)} '
                              'point(s) sur la période.')
    if perf['score'] is not None:
        key_points.append(f'Niveau de performance des tests : {perf["level_label"]} ({num(perf["score"])}/100).')
    key_points.append(f'Niveau de satisfaction : {level["label"]}.')

    # ── Recommandations ─────────────────────────────────────────────────────
    recommendations = []
    if failed:
        target = 'le cas en échec' if len(failed) == 1 else f'les {plural(len(failed), "cas", "cas", spell=True)} en échec'
        recommendations.append(f'Corriger {target}, {refs(failed)}, puis {"le" if len(failed) == 1 else "les"} '
                               'soumettre à un retest.')
    if blocked:
        recommendations.append(f'Lever le blocage de {refs(blocked)} afin de pouvoir exécuter ce cas.'
                               if len(blocked) == 1 else
                               f'Lever les blocages de {refs(blocked)} afin de pouvoir exécuter ces cas.')
    critical = sorted({(d['ticket_number'], d['title']) for i in items for d in i['defects']
                       if d['severity'] in ('critical', 'major')})
    if critical:
        recommendations.append('Traiter en priorité les anomalies critiques et majeures : '
                               + join(f'{t} « {_excerpt(title, 80)} »' for t, title in critical[:5]) + '.')
    recommendations += comment_themes(items)
    undocumented = [i for i in items if not i['observed'] and not i['comment']]
    if undocumented:
        recommendations.append(f'Compléter le résultat observé ou un commentaire pour {refs(undocumented)}, '
                               'afin de faciliter le diagnostic et le retest.')
    without_defect = [i for i in failed if not i['defects']]
    if without_defect:
        recommendations.append(f'Créer une anomalie pour {refs(without_defect)} afin d\'assurer le suivi de la correction.')
    unassigned = [i for i in items if not i['testers']]
    if unassigned:
        recommendations.append(f'Désigner un responsable de test pour {refs(unassigned)}.')
    if regressions:
        target = 'de la régression détectée' if len(regressions) == 1 else f'des {len(regressions)} régressions'
        recommendations.append(f'Analyser les causes {target} et renforcer la campagne de non-régression '
                               'sur les fonctionnalités concernées.')
    remaining = stats['total'] - stats['executed']
    if remaining:
        target = 'le cas restant' if remaining == 1 else f'les {remaining} cas restants'
        recommendations.append(f'Exécuter {target} avant la clôture du sprint.' if kind == 'sprint'
                               else f'Planifier l\'exécution de {target}.')
    if stats['total'] >= 10 and perf['automation_rate'] < 30:
        recommendations.append(f'Étudier l\'automatisation des cas stables (taux actuel : {pct(perf["automation_rate"])}) '
                               'pour fiabiliser les campagnes de non-régression.')
    if not recommendations:
        recommendations.append('Intégrer les cas validés à la campagne de non-régression et maintenir la couverture '
                               'de test sur les prochaines évolutions.')

    # ── Conclusion ──────────────────────────────────────────────────────────
    conclusion = []
    if stats['total']:
        subject = {'sprint': f'La recette du sprint « {sprints[0].name} »' if sprints else '',
                   'sprints': f'Sur les {len(sprints or [])} sprints analysés, la recette',
                   'project': f'À ce stade, la recette du projet {project.name}'}[kind]
        sentence = (f'{subject} est {verdict(stats, critical_open)} : {stats["passed"]} cas sur {stats["total"]} '
                    f'sont validés ({pct(stats["success_rate"])}).')
        if failed:
            sentence += f' {cap(plural(len(failed), "cas reste", "cas restent", spell=True))} en échec : {refs(failed)}.'
        if blocked:
            sentence += f' {cap(plural(len(blocked), "cas est bloqué", "cas sont bloqués", spell=True))} : {refs(blocked)}.'
        conclusion.append(sentence)
        if defects['active'] == 1:
            conclusion.append("Une anomalie demeure active et doit être suivie jusqu'à sa résolution.")
        elif defects['active']:
            conclusion.append(f'{defects["active"]} anomalies demeurent actives et doivent être suivies '
                              "jusqu'à leur résolution.")
        conclusion.append(f'Le niveau de satisfaction est jugé « {level["label"]} ».')
        if stats['passed'] == stats['total']:
            conclusion.append(FULL_SUCCESS_NOTE)
        elif kind == 'sprint':
            conclusion.append('La clôture du sprint est conditionnée à la correction et au retest des cas en échec, '
                              'conformément aux recommandations ci-dessous.' if items else
                              'La clôture du sprint suppose l\'exécution des cas restants.')
        else:
            conclusion.append('La mise en œuvre des recommandations ci-dessous permettra de réduire les risques '
                              'résiduels avant la mise en production.')
    else:
        conclusion.append('Aucun résultat ne peut être conclu tant que des cas de test ne sont pas rattachés '
                          'à ce périmètre et exécutés.')

    return {
        'introduction': intro,
        'key_points': key_points,
        'attention_cases': [{k: v for k, v in i.items() if k != 'comments_text'} for i in items],
        'responsibilities': responsibilities(items, data['members']),
        'recommendations': recommendations,
        'satisfaction': level,
        'verdict': verdict(stats, critical_open),
        'conclusion': conclusion,
    }


def _critical_active(defect_rows):
    return sum(1 for d in defect_rows if d['severity'] == 'critical' and d['status'] not in ('resolved', 'closed'))


# ─────────────────────────────────────────────────────────────────────────────
# Rapport global (ensemble des projets d'une équipe)
# ─────────────────────────────────────────────────────────────────────────────

def project_assessment(stats, perf):
    if not stats['total']:
        return 'Aucun cas de test'
    if not stats['executed']:
        return 'Tests non démarrés'
    level = satisfaction(stats, perf['defects']['critical'] and perf['defects']['active'])
    return level['label']


def portfolio_narrative(team, rows, kind='weekly'):
    """
    rows : [{'project': Project, 'stats': stats, 'performance': perf}] pour chaque projet visible.
    kind : 'weekly' (rapport hebdomadaire) ou 'daily' (Daily Scrum).
    """
    totals = {k: sum(r['stats'][k] for r in rows) for k in ('total', 'passed', 'failed', 'blocked', 'executed')}
    totals['success_rate'] = round(totals['passed'] / totals['total'] * 100, 1) if totals['total'] else 0
    totals['execution_rate'] = round(totals['executed'] / totals['total'] * 100, 1) if totals['total'] else 0
    active = [r for r in rows if r['project'].status == 'active']
    evaluated = [r for r in rows if r['stats']['executed']]
    at_risk = [r for r in evaluated if r['stats']['success_rate'] < 60 or r['performance']['level'] == 'critical']
    idle = [r for r in active if r['stats']['total'] and not r['stats']['executed']]
    empty = [r for r in active if not r['stats']['total']]
    from .models import Defect
    defects_active = sum(r['performance']['defects']['active'] for r in rows)
    critical = []
    for r in rows:
        count = (Defect.objects.filter(project=r['project'], severity=Defect.Severity.CRITICAL)
                 .exclude(status__in=Defect.DONE_STATUSES).count())
        if count:
            critical.append((r['project'].name, count))
    level = satisfaction({**totals, 'executed': totals['executed']}, sum(c for _, c in critical))
    today = fr_date(timezone.localdate())

    scope = 'du projet' if len(rows) == 1 else f'des {len(rows)} projets'
    if kind == 'daily':
        intro = [f"Ce point quotidien présente l'état d'avancement des tests {scope} "
                 f"de l'équipe {team.name} au {today}, afin d'orienter les priorités de la journée."]
    else:
        intro = [f"Ce rapport consolide l'état de la qualité {scope} de l'équipe "
                 f"{team.name} ({plural(len(active), 'projet actif', 'projets actifs')}) au {today}.",
                 'Il offre une vision transverse des résultats de test, des risques identifiés et des actions '
                 'prioritaires, afin d\'éclairer les arbitrages de pilotage.']

    key_points = []
    if totals['total']:
        key_points.append(f'Taux de réussite global : {pct(totals["success_rate"])} '
                          f'({totals["passed"]} / {totals["total"]} cas validés, tous projets confondus).')
        key_points.append(f'Avancement global des exécutions : {pct(totals["execution_rate"])}.')
    if len(evaluated) > 1:
        best = max(evaluated, key=lambda r: r['stats']['success_rate'])
        key_points.append(f'Meilleur résultat : {best["project"].name} ({pct(best["stats"]["success_rate"])}).')
    if at_risk:
        key_points.append('Projets à surveiller : ' + join(
            f'{r["project"].name} ({pct(r["stats"]["success_rate"])})' for r in at_risk) + '.')
    if totals['failed'] or totals['blocked']:
        parts = [plural(totals['failed'], 'cas en échec', 'cas en échec', spell=True) if totals['failed'] else '',
                 plural(totals['blocked'], 'cas bloqué', 'cas bloqués', spell=True) if totals['blocked'] else '']
        key_points.append(f'{cap(join(parts))} sur l\'ensemble des projets.')
    critical_total = sum(c for _, c in critical)
    key_points.append(cap(plural(defects_active, 'anomalie active', 'anomalies actives', spell=True, feminine=True))
                      + (f', dont {plural(critical_total, "critique", "critiques")} '
                         f'({join(f"{n} : {c}" for n, c in critical)}).' if critical else '.'))
    if idle:
        key_points.append('Tests non démarrés : ' + join(r['project'].name for r in idle) + '.')
    key_points.append(f'Niveau de satisfaction global : {level["label"]}.')

    recommendations = []
    if critical:
        recommendations.append('Traiter en priorité les anomalies critiques encore actives : '
                               + join(f'{plural(c, "anomalie", spell=True, feminine=True)} sur {n}' for n, c in critical)
                               + '.')
    if at_risk:
        recommendations.append('Mettre en place un plan de remédiation pour ' + join(r['project'].name for r in at_risk)
                               + ' : correction des cas en échec, retests ciblés et suivi rapproché.')
    if idle:
        recommendations.append('Engager les campagnes de test de ' + join(r['project'].name for r in idle)
                               + ' afin de sécuriser les livraisons.')
    if empty:
        recommendations.append('Définir et rattacher des cas de test aux projets actifs qui n\'en ont pas encore : '
                               + join(r['project'].name for r in empty) + '.')
    low_exec = [r for r in evaluated if r['stats']['execution_rate'] < 80 and r not in at_risk]
    if low_exec:
        recommendations.append('Accélérer l\'exécution des cas restants sur ' + join(r['project'].name for r in low_exec) + '.')
    low_auto = [r for r in evaluated if r['stats']['total'] >= 10 and r['performance']['automation_rate'] < 30]
    if low_auto:
        recommendations.append('Étudier l\'automatisation des cas stables sur ' + join(r['project'].name for r in low_auto)
                               + ' pour fiabiliser la non-régression.')
    if not recommendations:
        recommendations.append('Maintenir le niveau de qualité atteint et capitaliser sur les bonnes pratiques '
                               '(non-régression, documentation des résultats observés).')

    conclusion = []
    if totals['total']:
        conclusion.append(f'Sur l\'ensemble du portefeuille, {totals["passed"]} cas sur {totals["total"]} sont validés '
                          f'({pct(totals["success_rate"])}) ; le niveau de satisfaction global est jugé '
                          f'« {level["label"]} ».')
        if at_risk:
            conclusion.append(f'{plural(len(at_risk), "projet requiert", "projets requièrent")} une attention '
                              'particulière ; les recommandations ci-dessous précisent les actions prioritaires.')
        elif totals['passed'] == totals['total']:
            conclusion.append(FULL_SUCCESS_NOTE)
        else:
            conclusion.append('Aucun projet ne présente de risque majeur ; le suivi des cas restants doit se poursuivre.')
    else:
        conclusion.append("Aucun cas de test n'est encore rattaché aux projets de l'équipe : le niveau de qualité "
                          "ne peut pas être évalué.")

    return {
        'introduction': intro,
        'key_points': key_points,
        'projects': [{'name': r['project'].name, 'assessment': project_assessment(r['stats'], r['performance']),
                      'success_rate': r['stats']['success_rate'], 'execution_rate': r['stats']['execution_rate'],
                      'score': r['performance']['score']} for r in rows],
        'recommendations': recommendations,
        'satisfaction': level,
        'conclusion': conclusion,
    }
