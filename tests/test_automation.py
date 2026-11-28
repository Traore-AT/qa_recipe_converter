"""
Tests — alertes e-mail, rapport automatique, report de cas, campagnes de non-régression,
Jira (faux serveur en mémoire), import de résultats, exports Excel, stockage distant.
"""
import hashlib
import hmac
import io
import json
import re
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.utils import timezone
from openpyxl import load_workbook

from apps.core.files import local_copy
from apps.core.models import ConversionJob, ExtractedUseCase
from apps.parser.gherkin_generator import generate_cypress_step_definitions, generate_feature_file
from apps.qamanagement import campaigns, jira, test_results
from apps.qamanagement.models import (
    ActivityLog, Defect, DefectComment, ProjectQASettings, Sprint, UseCaseAssignment, UseCaseComment, UseCaseScreenshot,
)
from apps.qamanagement.notifications import project_recipients
from apps.teams.models import Project, ProjectMember
from apps.teams.reports import COMPANY_FOOTER_LINES

from .test_reports_performance import api, env, set_status  # noqa: F401

pytestmark = pytest.mark.django_db


def settings_for(env, **values):  # noqa: F811
    config = ProjectQASettings.for_project(env.project)
    for key, value in values.items():
        setattr(config, key, value)
    config.save()
    return config


# ═════════════════════════════════════════════════════════════════════════════
class TestAlerts:
    def test_regression_email(self, env):  # noqa: F811
        ProjectMember.objects.create(project=env.project, user=env.member, role='lead')
        for status in ('Passé', 'Échoué'):
            set_status(env.cases[0], status, sprint=env.s1, actor=env.tester)
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert set(message.to) == {'owner@test.com', 'admin@test.com', 'member@test.com'}
        assert 'égression' in message.subject and 'CAS-001' in message.body and 'tester' in message.body
        assert 'CAS-001' in message.alternatives[0][0]

    def test_bulk_update_sends_a_single_digest(self, env):  # noqa: F811
        job = ConversionJob.objects.create(source_filename='a.docx', word_file='a.docx', project=env.project)
        cases = [ExtractedUseCase.objects.create(job=job, order=i, status='Passé') for i in range(3)]
        ids = [str(c.id) for c in cases]
        api(env.tester).post(f'/api/jobs/{job.id}/bulk-status/', {'uc_ids': ids, 'status': 'Échoué'}, format='json')
        assert len(mail.outbox) == 1 and '3 régressions' in mail.outbox[0].subject
        api(env.tester).post(f'/api/jobs/{job.id}/save/', {'use_cases': [
            {'id': i, 'status': s} for s in ('Passé', 'Échoué') for i in ids]}, format='json')
        assert len(mail.outbox) == 2

    def test_alerts_can_be_disabled(self, env):  # noqa: F811
        settings_for(env, alert_on_regression=False, alert_on_critical_defect=False)
        for status in ('Passé', 'Échoué'):
            set_status(env.cases[0], status)
        api(env.tester).post(f'{env.base}/defects/', {'title': 'x', 'severity': 'critical'}, format='json')
        assert mail.outbox == []

    def test_critical_defect_alerts_once_per_escalation(self, env):  # noqa: F811
        client = api(env.tester)
        resp = client.post(f'{env.base}/defects/', {'title': 'Panne\nSubject: x', 'severity': 'critical', 'assigned_to': env.member.id}, format='json')
        assert resp.status_code == 201
        assert len(mail.outbox) == 1 and 'member@test.com' in mail.outbox[0].to and '\n' not in mail.outbox[0].subject
        url = f'{env.base}/defects/{resp.data["id"]}/'
        client.patch(url, {'description': 'détail'}, format='json')
        assert len(mail.outbox) == 1
        client.patch(url, {'status': 'resolved'}, format='json')
        assert len(mail.outbox) == 1
        client.patch(url, {'status': 'reopened'}, format='json')
        assert len(mail.outbox) == 2
        other = client.post(f'{env.base}/defects/', {'title': 'Mineure', 'severity': 'minor'}, format='json').data['id']
        client.patch(f'{env.base}/defects/{other}/', {'severity': 'critical'}, format='json')
        assert len(mail.outbox) == 3

    def test_recipients_exclude_inactive_and_emailless(self, env):  # noqa: F811
        env.admin.is_active = False
        env.admin.save()
        env.tester.email = ''
        env.tester.save()
        assert project_recipients(env.project, [env.tester, None]) == ['owner@test.com']

    def test_mail_failure_never_breaks_the_action(self, env, monkeypatch):  # noqa: F811
        monkeypatch.setattr('django.core.mail.EmailMultiAlternatives.send', lambda self: 1 / 0)
        resp = api(env.tester).post(f'{env.base}/defects/', {'title': 'x', 'severity': 'critical'}, format='json')
        assert resp.status_code == 201

    def test_background_failure_never_breaks_the_action(self, env, monkeypatch):  # noqa: F811
        monkeypatch.setattr('apps.qamanagement.notifications.project_recipients', lambda *a: 1 / 0)
        resp = api(env.tester).post(f'{env.base}/defects/', {'title': 'x', 'severity': 'critical'}, format='json')
        assert resp.status_code == 201


class TestSprintReportMail:
    def close(self, env, value='completed'):  # noqa: F811
        return api(env.owner).patch(f'{env.base}/sprints/{env.s1.id}/', {'status': value}, format='json')

    def test_sent_once_with_pdf_on_completion(self, env):  # noqa: F811
        assert self.close(env).status_code == 200
        assert len(mail.outbox) == 1
        name, content, mime = mail.outbox[0].attachments[0]
        assert name == 'rapport-portail-sprint-1.pdf' and mime == 'application/pdf' and content[:4] == b'%PDF'
        env.s1.refresh_from_db()
        assert env.s1.report_sent_at
        self.close(env, 'closed')
        api(env.owner).patch(f'{env.base}/sprints/{env.s1.id}/', {'name': 'Renommé'}, format='json')
        assert len(mail.outbox) == 1
        self.close(env, 'active')
        self.close(env)
        assert len(mail.outbox) == 2

    def test_disabled(self, env):  # noqa: F811
        settings_for(env, auto_report_on_close=False)
        self.close(env)
        assert mail.outbox == []

    def test_manual_send(self, env):  # noqa: F811
        settings_for(env, auto_report_on_close=False)
        url = f'{env.base}/sprints/{env.s1.id}/report/send/'
        resp = api(env.owner).post(url)
        assert resp.status_code == 202 and resp.data['recipients'] == 2 and len(mail.outbox) == 1
        assert api(env.tester).post(url).status_code == 403
        for user in (env.owner, env.admin):
            user.email = ''
            user.save()
        assert api(env.owner).post(url).status_code == 400


# ═════════════════════════════════════════════════════════════════════════════
class TestCarryOver:
    def post(self, env, user=None, **data):  # noqa: F811
        return api(user or env.owner).post(f'{env.base}/sprints/{env.s1.id}/carry-over/', data, format='json')

    def test_preview(self, env):  # noqa: F811
        set_status(env.cases[0], 'Échoué')
        get = lambda q='': api(env.viewer).get(f'{env.base}/sprints/{env.s1.id}/carry-over/{q}').data  # noqa: E731
        assert [r['case_number'] for r in get()['results']] == [2]
        assert get('?include_failed=true')['count'] == 2

    def test_to_new_sprint_and_close(self, env):  # noqa: F811
        set_status(env.cases[0], 'Passé')
        resp = self.post(env, new_sprint={'name': 'Sprint 3', 'start_date': '2026-12-01', 'duration_days': 10}, close_source=True)
        assert resp.status_code == 201 and resp.data['created'] == 1
        target = Sprint.objects.get(name='Sprint 3')
        copy = target.assignments.get()
        assert copy.use_case == env.cases[1] and copy.assigned_to == env.pm and copy.carried_from == env.s1
        env.s1.refresh_from_db()
        assert env.s1.status == 'completed' and env.s1.assignments.count() == 2
        assert len(mail.outbox) == 1  # rapport de clôture
        board = api(env.owner).get(f'{env.base}/sprints/{target.id}/board/').data
        assert board['columns']['À tester'][0]['carried_from_name'] == 'Sprint 1'

    def test_existing_target_skips_duplicates(self, env):  # noqa: F811
        resp = self.post(env, target_sprint=str(env.s2.id))
        assert resp.status_code == 201 and resp.data['created'] == 1 and resp.data['skipped'] == [2]

    def test_selection(self, env):  # noqa: F811
        resp = self.post(env, target_sprint=str(env.s2.id), case_ids=[str(env.cases[0].id)])
        assert resp.data['created'] == 1 and resp.data['skipped'] == []

    def test_case_with_two_assignees(self, env):  # noqa: F811
        other = ProjectMember.objects.create(project=env.project, user=env.member)
        UseCaseAssignment.objects.create(use_case=env.cases[0], sprint=env.s1, assigned_to=other)
        assert self.post(env, target_sprint=str(env.s2.id)).data['created'] == 1
        assert env.s2.assignments.filter(use_case=env.cases[0]).count() == 2

    def test_case_passed_elsewhere_is_not_carried(self, env):  # noqa: F811
        env.s1.status = 'completed'
        env.s1.save()
        set_status(env.cases[1], 'Passé')
        assert [a.use_case for a in campaigns.carry_over_candidates(env.s1)] == [env.cases[0]]

    def test_refusals_leave_nothing_behind(self, env):  # noqa: F811
        done = Sprint.objects.create(project=env.project, name='Fini', status='closed', start_date='2026-01-01', end_date='2026-01-05')
        foreign = Sprint.objects.create(project=Project.objects.create(team=env.team, name='Autre'), name='X',
                                        start_date='2026-01-01', end_date='2026-01-05')
        new = {'name': 'N', 'start_date': '2026-12-01', 'duration_days': 3}
        assert self.post(env).status_code == 400
        assert self.post(env, target_sprint=str(env.s2.id), new_sprint=new).status_code == 400
        assert self.post(env, target_sprint=str(env.s1.id)).status_code == 400
        assert self.post(env, target_sprint=str(done.id)).status_code == 400
        assert self.post(env, target_sprint=str(foreign.id)).status_code == 404
        assert self.post(env, new_sprint={'name': ''}).status_code == 400
        assert self.post(env, user=env.tester, target_sprint=str(env.s2.id)).status_code == 403
        for case in env.cases:
            set_status(case, 'Passé')
        assert self.post(env, new_sprint=new, close_source=True).status_code == 400
        assert not Sprint.objects.filter(name='N').exists()


class TestRegressionCampaign:
    def regress(self, env):  # noqa: F811
        for status in ('Passé', 'Échoué'):
            set_status(env.cases[0], status, sprint=env.s1)
        for status in ('Passé', 'Échoué', 'Passé'):
            set_status(env.cases[1], status, sprint=env.s1)
        Defect.objects.create(project=env.project, title='d', use_case=env.cases[2], is_regression=True)

    def post(self, env, user=None, **data):  # noqa: F811
        return api(user or env.owner).post(f'{env.base}/regression-campaign/', data, format='json')

    def test_one_click(self, env):  # noqa: F811
        self.regress(env)
        assert api(env.viewer).get(f'{env.base}/regression-campaign/').data == {'all': 3, 'active': 1}
        resp = self.post(env)
        assert resp.status_code == 201 and resp.data['cases'] == 3
        sprint = Sprint.objects.get(id=resp.data['sprint']['id'])
        assert sprint.kind == 'regression' and sprint.status == 'active' and sprint.duration_days == 7
        assert sprint.name.startswith('Non-régression du')
        assert set(sprint.case_statuses().values()) == {'À tester'}
        assert all(a.assigned_to == env.pm for a in sprint.assignments.all())

    def test_active_scope_without_reset_in_the_future(self, env):  # noqa: F811
        self.regress(env)
        start = timezone.localdate() + timedelta(days=3)
        resp = self.post(env, scope='active', reset_status=False, name='NR', start_date=str(start), duration_days=2)
        sprint = Sprint.objects.get(id=resp.data['sprint']['id'])
        assert list(sprint.case_statuses().values()) == ['Échoué'] and sprint.status == 'planned'

    def test_frozen_results_are_preserved(self, env):  # noqa: F811
        self.regress(env)
        env.s1.status = 'completed'
        env.s1.save()
        self.post(env)
        assert env.s1.stats['failed'] == 1 and env.s1.stats['passed'] == 1

    def test_former_member_is_not_reassigned(self, env):  # noqa: F811
        self.regress(env)
        api(env.owner).delete(f'/api/teams/{env.team.slug}/members/{env.tester.id}/')
        resp = self.post(env)
        assert UseCaseAssignment.objects.filter(sprint_id=resp.data['sprint']['id'], assigned_to__isnull=False).count() == 0

    def test_nothing_to_replay_and_rights(self, env):  # noqa: F811
        assert self.post(env).status_code == 400
        assert self.post(env, user=env.tester).status_code == 403
        assert self.post(env, duration_days=0).status_code == 400


# ═════════════════════════════════════════════════════════════════════════════
JUNIT = b'''<?xml version="1.0"?><testsuites><testsuite name="Feature: UC-001">
<testcase name="Feature [CAS-001] Connexion" classname="Connexion"/>
<testcase name="Autre [CAS-002]"><failure message="Timed out \x1b[31mretrying\x1b[39m">stack</failure></testcase>
<testcase name="Second test CAS-2"/>
<testcase name="CAS-003"><skipped/></testcase><testcase name="Inconnu"/></testsuite></testsuites>'''
MOCHAWESOME = {'stats': {}, 'results': [{'title': '', 'tests': [], 'suites': [{'title': 'Login', 'suites': [], 'tests': [
    {'title': 'CAS-001 ok', 'fullTitle': 'Login CAS-001 ok', 'state': 'passed', 'pass': True, 'err': {}},
    {'title': 'CAS-002 ko', 'fullTitle': 'Login CAS-002 ko', 'state': 'failed', 'err': {'message': 'boom'}}]}]}]}
MOCHA = {'tests': [{'fullTitle': 'CAS-001', 'err': {}}, {'fullTitle': 'CAS-002', 'err': {'message': 'boom'}},
                   {'fullTitle': 'CAS-003', 'err': {}}], 'pending': [{'fullTitle': 'CAS-003'}], 'failures': [{'fullTitle': 'CAS-002'}]}
CYPRESS = {'runs': [{'tests': [{'title': ['Login', 'CAS-001'], 'state': 'passed'},
                               {'title': ['Login', 'CAS-002'], 'state': 'failed', 'displayError': 'boom'}]}]}
CUCUMBER = [{'name': 'F', 'tags': [{'name': '@CAS-001'}], 'elements': [
    {'type': 'background', 'steps': []},
    {'type': 'scenario', 'name': 's', 'steps': [{'result': {'status': 'passed'}}]}]},
    {'name': 'G', 'elements': [{'type': 'scenario', 'name': 'CAS-002', 'steps': [
        {'result': {'status': 'passed'}}, {'result': {'status': 'failed', 'error_message': 'boom'}}]}]}]


class TestResultsImport:
    def post(self, env, *contents, user=None, **data):  # noqa: F811
        files = [SimpleUploadedFile(f'r{i}.xml', c if isinstance(c, bytes) else json.dumps(c).encode())
                 for i, c in enumerate(contents)]
        return api(user or env.tester).post(f'{env.base}/test-results/import/', {'files': files, **data}, format='multipart')

    @pytest.mark.parametrize('content', [JUNIT, MOCHAWESOME, MOCHA, CYPRESS, CUCUMBER],
                             ids=['junit', 'mochawesome', 'mocha', 'cypress', 'cucumber'])
    def test_formats(self, env, content):  # noqa: F811
        resp = self.post(env, content)
        assert resp.status_code == 200, resp.data
        assert [(c['case_number'], c['new_status']) for c in resp.data['cases']] == [(1, 'Passé'), (2, 'Échoué')]
        statuses = list(ExtractedUseCase.objects.order_by('case_number').values_list('status', flat=True))
        assert statuses == ['Passé', 'Échoué', 'À tester', 'À tester']
        assert ExtractedUseCase.objects.filter(is_automated=True).count() == 2

    def test_dry_run_changes_nothing(self, env):  # noqa: F811
        resp = self.post(env, JUNIT, dry_run='true')
        assert resp.data['applied'] is False and resp.data['failed'] == 1 and resp.data['skipped'] == 1
        assert resp.data['unmatched_count'] == 1 and 'Inconnu' in resp.data['unmatched'][0]
        assert resp.data['cases'][1]['tests'] == 2 and '\x1b' not in resp.data['cases'][1]['message']
        assert not ExtractedUseCase.objects.exclude(status='À tester').exists()

    def test_history_comment_and_idempotence(self, env):  # noqa: F811
        self.post(env, JUNIT, sprint=str(env.s1.id))
        execution = env.cases[0].executions.get()
        assert execution.executed_by == env.tester and execution.sprint == env.s1
        self.post(env, JUNIT, sprint=str(env.s1.id))
        assert env.cases[0].executions.count() == 1
        assert UseCaseComment.objects.filter(use_case=env.cases[1]).count() == 1

    def test_several_files(self, env):  # noqa: F811
        resp = self.post(env, CYPRESS, {'tests': [{'fullTitle': 'CAS-004', 'err': {}}]})
        assert resp.data['passed'] == 2 and resp.data['failed'] == 1

    def test_restricted_to_sprint(self, env):  # noqa: F811
        resp = self.post(env, CYPRESS, sprint=str(env.s2.id))
        assert [c['case_number'] for c in resp.data['cases']] == [2] and resp.data['outside_sprint'] == [1]
        env.s2.status = 'closed'
        env.s2.save()
        assert self.post(env, CYPRESS, sprint=str(env.s2.id)).status_code == 400

    def test_creates_defects_once_and_one_digest(self, env):  # noqa: F811
        for case in env.cases[:2]:
            set_status(case, 'Passé')
        both = {'tests': [{'fullTitle': f'CAS-00{n}', 'err': {'message': 'boom'}} for n in (1, 2)]}
        resp = self.post(env, both, sprint=str(env.s1.id), create_defects='true')
        assert resp.data['defects_created'] == 2
        defect = Defect.objects.get(use_case=env.cases[0])
        assert defect.is_regression and defect.sprint == env.s1 and defect.actual_behavior == 'boom'
        assert len(mail.outbox) == 1 and '2 régressions' in mail.outbox[0].subject
        assert self.post(env, both, create_defects='true').data['defects_created'] == 0

    def test_matching_rules(self, env):  # noqa: F811
        ExtractedUseCase.objects.filter(pk=env.cases[0].pk).update(jira_ticket='QA-7')
        ExtractedUseCase.objects.filter(pk=env.cases[2].pk).update(use_case_text='Paiement par carte')
        ExtractedUseCase.objects.filter(pk=env.cases[3].pk).update(use_case_text='Paiement par carte')
        matcher = test_results.CaseMatcher(env.project)
        number = lambda t: getattr(matcher.match(t), 'case_number', None)  # noqa: E731
        assert number('[cas-001] x') == 1 and number('Login QA-7 ok') == 1
        assert number('Suite Cas é2') is None  # intitulé trop court pour une correspondance partielle
        assert number('Cas é2') == 2
        assert number('Paiement par carte') is None  # ambigu
        assert number('CAS-001 et CAS-002') is None  # ambigu
        assert number('CAS-999') is None
        ExtractedUseCase.objects.filter(pk=env.cases[3].pk).update(use_case_text='Autre')
        matcher = test_results.CaseMatcher(env.project)
        assert number('Boutique  paiement PAR carte') == 3

    @pytest.mark.parametrize('content', [
        b'', b'{', b'[]x', b'<html/>', b'<a', b'{"foo": 1}', b'[1]', b'{"tests": [1]}', b'{"runs": [{"tests": [{"title": [1]}]}]}',
        b'<!DOCTYPE x [<!ENTITY a "b">]><testsuite/>', b'\xff\xfe\x00', b'[' * 100000, b'{"results": [{"suites": [1]}]}',
    ], ids=lambda c: f'{len(c)}B-{c[:24]!r}')  # ids courts : Windows limite PYTEST_CURRENT_TEST à 32 767 caractères
    def test_invalid_files_are_rejected(self, env, content):  # noqa: F811
        resp = self.post(env, content)
        assert resp.status_code == 400 and resp.data['error']

    def test_limits_and_rights(self, env, monkeypatch):  # noqa: F811
        assert self.post(env).status_code == 400
        assert self.post(env, JUNIT, user=env.viewer).status_code == 403
        assert self.post(env, JUNIT, user=env.outsider).status_code == 403
        assert self.post(env, JUNIT, sprint='x').status_code == 400
        assert self.post(env, {'tests': [{'fullTitle': 'rien', 'err': {}}]}).status_code == 400
        monkeypatch.setattr(test_results, 'MAX_FILE_BYTES', 10)
        assert self.post(env, JUNIT).status_code == 400

    def test_generated_project_round_trips(self, env):  # noqa: F811
        case = env.cases[0]
        case.steps = "Cliquer sur 'OK' (bouton)\nAller à /accueil\nAller à /accueil"
        case.expected_results = 'Aller à /accueil'
        feature, steps = generate_feature_file(case), generate_cypress_step_definitions(case)
        assert '@CAS-001' in feature and 'Scenario: [CAS-001]' in feature
        assert "import { Given, When, Then } from" in steps and 'And(' not in steps
        assert steps.count('accueil') == 1  # une seule définition par libellé
        assert r"\'OK\' \\(bouton)" in steps and r'\\/accueil' in steps
        scenario = re.search(r'Scenario: (.*)', feature).group(1)
        assert test_results.CaseMatcher(env.project).match(f'Feature {scenario}') == case


# ═════════════════════════════════════════════════════════════════════════════
class TestExcelExports:
    def load(self, response):
        assert response.status_code == 200, response.content
        assert response['Content-Disposition'].endswith('.xlsx"')
        return load_workbook(io.BytesIO(response.content))

    def test_project_report(self, env):  # noqa: F811
        for status in ('Passé', 'Échoué'):
            set_status(env.cases[0], status, sprint=env.s1)
        ExtractedUseCase.objects.filter(pk=env.cases[1].pk).update(use_case_text='=HYPERLINK("http://x")\x07')
        Defect.objects.create(project=env.project, title='@SUM(1)', use_case=env.cases[0], sprint=env.s1)
        wb = self.load(api(env.viewer).get(f'{env.base}/report/xlsx/'))
        assert wb.sheetnames == ['Rapport', 'Synthèse', 'Évolution', 'Sprints', 'Anomalies', 'Régressions', 'Membres', 'Cas de test']
        for ws in wb:
            assert len(ws._images) == 1
            texts = [c.value for row in ws.iter_rows() for c in row if c.value]
            assert all(line in texts for line in COMPANY_FOOTER_LINES)
            assert 'GLOBAL ITEC' in ws.oddFooter.center.text
            # Logo centré à moins d'une demi-colonne près sur la largeur du tableau
            anchor = ws._images[0].anchor
            widths = [round(ws.column_dimensions[chr(65 + i)].width * 7 + 5) for i in range(ws.max_column if ws.title != 'Évolution' else 9)]
            middle = sum(widths[:anchor._from.col]) + anchor._from.colOff / 9525 + 150 / 2
            assert abs(middle - sum(widths) / 2) < 12, ws.title
        assert len(wb['Évolution']._charts) == 1
        cell = next(c for row in wb['Cas de test'].iter_rows() for c in row if str(c.value).startswith('=HYPER'))
        assert cell.data_type == 's' and '\x07' not in cell.value
        assert wb['Anomalies']['C6'].data_type == 's'
        assert wb['Sprints']['B6'].is_date

    def test_sprint_scope_and_rights(self, env):  # noqa: F811
        resp = api(env.owner).get(f'{env.base}/report/xlsx/?sprints={env.s1.id}')
        assert 'sprint-1' in resp['Content-Disposition']
        assert self.load(resp)['Cas de test'].max_row == 5 + 2 + 3
        assert api(env.outsider).get(f'{env.base}/report/xlsx/').status_code == 403

    def test_defects_use_the_list_filters(self, env):  # noqa: F811
        Defect.objects.create(project=env.project, title='Critique', severity='critical', sprint=env.s1)
        Defect.objects.create(project=env.project, title='Mineure', severity='minor')
        rows = lambda q: [r[2] for r in self.load(api(env.viewer).get(f'{env.base}/defects/export/xlsx/{q}'))['Anomalies']  # noqa: E731
                          .iter_rows(min_row=6, values_only=True) if r[0] and r[0].startswith('TKT')]
        assert sorted(rows('')) == ['Critique', 'Mineure']
        assert rows(f'?severity=critical&sprint={env.s1.id}') == ['Critique']
        assert rows('?search=rien') == []

    def test_empty_project(self, env):  # noqa: F811
        empty = Project.objects.create(team=env.team, name='Vide')
        base = f'/api/teams/{env.team.slug}/projects/{empty.slug}'
        self.load(api(env.owner).get(f'{base}/report/xlsx/'))
        self.load(api(env.owner).get(f'{base}/defects/export/xlsx/'))


# ═════════════════════════════════════════════════════════════════════════════
class FakeJira:
    """Jira en mémoire : même contrat que jira.api()."""
    TRANSITIONS = [('11', 'new'), ('21', 'indeterminate'), ('31', 'done')]

    def __init__(self):
        self.issues, self.comments, self.links, self.calls, self.fail = {}, {}, [], [], None

    def add(self, key, summary='Bug', category='new', priority='Medium', labels=(), issuetype='Bug', comments=()):
        self.issues[key] = {'summary': summary, 'status': {'statusCategory': {'key': category}}, 'priority': {'name': priority},
                            'labels': list(labels), 'issuetype': {'name': issuetype}, 'description': jira.to_adf('desc'),
                            'comment': {'comments': list(comments)}}
        return {'key': key, 'fields': self.issues[key]}

    def category(self, key):
        return self.issues[key]['status']['statusCategory']['key']

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path))
        if self.fail:
            raise jira.JiraError(self.fail)
        if path.startswith('project/'):
            return {}
        if (method, path) == ('POST', 'issue'):
            key = f'QA-{len(self.issues) + 1}'
            fields = payload['fields']
            self.add(key, fields['summary'], 'new', fields.get('priority', {}).get('name', 'Medium'), fields['labels'], fields['issuetype']['name'])
            return {'key': key}
        if path == 'issueLink':
            self.links.append(payload)
            return {}
        if path == 'search/jql':
            return self.search(payload)
        key = path.split('/')[1].split('?')[0]
        if key not in self.issues:
            raise jira.JiraError('Jira (HTTP 404) : absent')
        if path.endswith('/transitions'):
            if method == 'GET':
                return {'transitions': [{'id': i, 'to': {'statusCategory': {'key': c}}} for i, c in self.TRANSITIONS]}
            self.issues[key]['status']['statusCategory']['key'] = dict(self.TRANSITIONS)[payload['transition']['id']]
            return {}
        if path.endswith('/comment'):
            self.comments.setdefault(key, []).append(jira.from_adf(payload['body']).strip())
            return {'id': str(900 + len(self.comments[key]))}
        if method == 'PUT':
            self.issues[key].update(payload['fields'])
            return {}
        return {'key': key, 'fields': self.issues[key]}

    def search(self, payload):
        jql = payload['jql']
        if jql.startswith('key in'):
            found = [k for k in re.findall(r'[A-Z]+-\d+', jql) if k in self.issues]
        else:
            found = [k for k, f in self.issues.items() if self.category(k) != 'done' and 'qa-recipe' not in f['labels']]
        # Une page par ticket : vérifie la pagination.
        index = int(payload.get('nextPageToken') or 0)
        page = {'issues': [{'key': k, 'fields': self.issues[k]} for k in found[index:index + 1]]}
        if index + 1 < len(found):
            page['nextPageToken'] = str(index + 1)
        return page


@pytest.fixture
def fake(env, settings, monkeypatch):  # noqa: F811
    settings.JIRA_BASE_URL, settings.JIRA_EMAIL, settings.JIRA_API_TOKEN = 'https://acme.atlassian.net', 'bot@acme.com', 'token'
    settings.JIRA_WEBHOOK_SECRET = 's3cret'
    settings_for(env, jira_enabled=True, jira_project_key='QA', jira_import=True)
    server = FakeJira()
    monkeypatch.setattr(jira, 'api', server)
    return server


class TestJiraPush:
    def create(self, env, **data):  # noqa: F811
        resp = api(env.tester).post(f'{env.base}/defects/', {'title': 'Panne', **data}, format='json')
        assert resp.status_code == 201, resp.data
        return resp.data

    def test_creates_issue_with_link(self, env, fake):  # noqa: F811
        ExtractedUseCase.objects.filter(pk=env.cases[0].pk).update(jira_ticket='QA-900')
        data = self.create(env, priority='high', use_case=str(env.cases[0].id))
        assert data['jira_key'] == 'QA-1' and data['jira_url'] == 'https://acme.atlassian.net/browse/QA-1'
        issue = fake.issues['QA-1']
        assert issue['priority']['name'] == 'High' and issue['labels'] == ['qa-recipe', data['ticket_number']]
        assert fake.links[0]['outwardIssue']['key'] == 'QA-900'

    def test_updates_fields_status_and_comments(self, env, fake):  # noqa: F811
        url = f'{env.base}/defects/{self.create(env)["id"]}/'
        client = api(env.tester)
        client.patch(url, {'title': 'Panne totale', 'status': 'in_progress'}, format='json')
        assert fake.issues['QA-1']['summary'] == 'Panne totale' and fake.category('QA-1') == 'indeterminate'
        client.patch(url, {'status': 'closed'}, format='json')
        assert fake.category('QA-1') == 'done'
        client.patch(url, {'status': 'reopened'}, format='json')
        assert fake.category('QA-1') == 'new'
        client.post(f'{url}comments/', {'content': 'Vu'}, format='json')
        assert fake.comments['QA-1'] == ['[QA Recipe] tester : Vu']
        assert len(fake.issues) == 1

    def test_failure_is_kept_then_retried(self, env, fake):  # noqa: F811
        fake.fail = 'Jira est injoignable.'
        data = self.create(env)
        assert data['jira_key'] is None and data['jira_sync_error'] == 'Jira est injoignable.'
        url = f'{env.base}/defects/{data["id"]}/jira/'
        assert api(env.tester).post(url).status_code == 502
        fake.fail = None
        resp = api(env.tester).post(url)
        assert resp.status_code == 200 and resp.data['jira_key'] == 'QA-1' and resp.data['jira_sync_error'] == ''
        assert api(env.viewer).post(url).status_code == 403

    def test_key_is_kept_when_transition_fails(self, env, fake, monkeypatch):  # noqa: F811
        monkeypatch.setattr(FakeJira, 'TRANSITIONS', [('11', 'new')])
        data = self.create(env, status='in_progress')
        assert data['jira_key'] == 'QA-1' and 'transition' in data['jira_sync_error']

    def test_priority_not_on_screen(self, env, fake, monkeypatch):  # noqa: F811
        original = FakeJira.__call__

        def strict(self, method, path, payload=None):
            if path == 'issue' and 'priority' in payload['fields']:
                raise jira.JiraError("Jira (HTTP 400) : priority : Field 'priority' cannot be set")
            return original(self, method, path, payload)
        monkeypatch.setattr(FakeJira, '__call__', strict)
        assert self.create(env)['jira_key'] == 'QA-1'

    def test_disabled_project_never_calls_jira(self, env, fake):  # noqa: F811
        settings_for(env, jira_enabled=False)
        data = self.create(env)
        assert fake.calls == [] and data['jira_key'] is None
        assert api(env.tester).post(f'{env.base}/defects/{data["id"]}/jira/').status_code == 400
        assert api(env.owner).post(f'{env.base}/jira/sync/').status_code == 400


class TestJiraWebhook:
    URL = '/api/integrations/jira/webhook/'

    def send(self, event, issue=None, secret='s3cret', **extra):
        body = json.dumps({'webhookEvent': event, 'issue': issue, **extra}).encode()
        signature = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return api(None).post(self.URL, body, content_type='application/json', HTTP_X_HUB_SIGNATURE=signature)

    def defect(self, env, **kwargs):  # noqa: F811
        return Defect.objects.create(project=env.project, title='Bug', jira_key='QA-1', reported_by=env.tester, **kwargs)

    def test_authentication(self, env, fake, settings):  # noqa: F811
        assert self.send('x', secret='faux').status_code == 401
        assert api(None).post(self.URL, {}, format='json').status_code == 401
        body = b'nope'
        sig = 'sha256=' + hmac.new(b's3cret', body, hashlib.sha256).hexdigest()
        assert api(None).post(self.URL, body, content_type='application/json', HTTP_X_HUB_SIGNATURE=sig).status_code == 400
        settings.JIRA_WEBHOOK_SECRET = ''
        assert self.send('x', secret='').status_code == 404

    @pytest.mark.parametrize('category,before,after', [
        ('done', 'open', 'resolved'), ('done', 'closed', 'closed'), ('indeterminate', 'open', 'in_progress'),
        ('new', 'resolved', 'reopened'), ('new', 'reopened', 'reopened'), ('new', 'in_progress', 'open'), ('inconnu', 'open', 'open'),
    ])
    def test_status_mapping(self, env, fake, category, before, after):  # noqa: F811
        defect = self.defect(env, status=before)
        self.send('jira:issue_updated', fake.add('QA-1', category=category))
        defect.refresh_from_db()
        assert defect.status == after

    def test_update_is_traced_and_not_echoed(self, env, fake):  # noqa: F811
        defect = self.defect(env)
        resp = self.send('jira:issue_updated', fake.add('QA-1', 'Nouveau titre', 'done', 'Highest'))
        assert resp.data['result'] == 'updated'
        defect.refresh_from_db()
        assert (defect.title, defect.priority, defect.status) == ('Nouveau titre', 'high', 'resolved') and defect.resolved_at
        assert defect.history.filter(field='Statut', actor=None).exists()
        assert ActivityLog.objects.filter(action_type='defect_jira_sync', target_users=env.tester).exists()
        assert fake.calls == []  # aucune boucle vers Jira
        assert self.send('jira:issue_updated', fake.add('QA-1', 'Nouveau titre', 'done', 'High')).data['result'] == 'unchanged'
        assert api(env.tester).get('/api/notifications/').status_code == 200

    def test_truncated_title_is_not_a_change(self, env, fake):  # noqa: F811
        defect = self.defect(env)
        Defect.objects.filter(pk=defect.pk).update(title='a' * 290)
        assert self.send('jira:issue_updated', fake.add('QA-1', 'a' * 255)).data['result'] == 'unchanged'

    def test_pending_local_change_wins(self, env, fake):  # noqa: F811
        fake.add('QA-1', 'Distant', 'done')
        defect = self.defect(env, jira_sync_error='échec précédent')
        assert self.send('jira:issue_updated', {'key': 'QA-1', 'fields': fake.issues['QA-1']}).data['result'] == 'pending_push'
        defect.refresh_from_db()
        assert defect.title == 'Bug' and fake.issues['QA-1']['summary'] == 'Bug' and defect.jira_sync_error == ''

    def test_import_rules(self, env, fake):  # noqa: F811
        assert self.send('jira:issue_created', fake.add('QA-5', 'Vu en prod', 'indeterminate')).data['result'] == 'imported'
        defect = Defect.objects.get(jira_key='QA-5')
        assert defect.project == env.project and defect.status == 'in_progress' and defect.description.strip() == 'desc'
        assert self.send('jira:issue_created', fake.add('QA-6', labels=['qa-recipe'])).data['result'] == 'ignored'
        assert self.send('jira:issue_created', fake.add('QA-7', issuetype='Story')).data['result'] == 'ignored'
        assert self.send('jira:issue_created', fake.add('AUTRE-1')).data['result'] == 'ignored'
        settings_for(env, jira_import=False)
        assert self.send('jira:issue_created', fake.add('QA-8')).data['result'] == 'ignored'

    def test_comments(self, env, fake):  # noqa: F811
        defect = self.defect(env)
        issue = fake.add('QA-1')
        comment = {'id': '55', 'body': jira.to_adf('Bonjour\nà tous'), 'author': {'displayName': 'Awa'}}
        assert self.send('comment_created', issue, comment=comment).data['result'] == 'commented'
        assert self.send('comment_created', issue, comment=comment).data['result'] == 'ignored'
        echo = {'id': '56', 'body': jira.to_adf('[QA Recipe] tester : Vu')}
        assert self.send('comment_created', issue, comment=echo).data['result'] == 'ignored'
        row = api(env.viewer).get(f'{env.base}/defects/{defect.id}/comments/').data['results'][0]
        assert row['author'] is None and row['external_author'] == 'Awa (Jira)' and row['content'] == 'Bonjour\nà tous'

    def test_deletion_and_noise(self, env, fake):  # noqa: F811
        defect = self.defect(env)
        assert self.send('jira:issue_deleted', fake.add('QA-1')).data['result'] == 'unlinked'
        defect.refresh_from_db()
        assert defect.jira_key is None
        assert self.send('jira:issue_deleted', {'key': 'QA-1'}).data['result'] == 'ignored'
        assert self.send('comment_created', {'key': 'QA-404'}).data['result'] == 'ignored'
        assert self.send('sprint_started').data['result'] == 'ignored'
        assert self.send('jira:issue_updated', {'key': 'QA-1', 'fields': None}).status_code == 200


class TestJiraSync:
    def test_full_sync_both_ways(self, env, fake):  # noqa: F811
        Defect.objects.create(project=env.project, title='Jamais envoyée')
        Defect.objects.create(project=env.project, title='Ancienne', status='closed')
        linked = Defect.objects.create(project=env.project, title='Liée', jira_key='QA-50')
        fake.add('QA-50', 'Liée', 'done', comments=[{'id': '1', 'body': 'Corrigé', 'author': {'displayName': 'Awa'}}])
        fake.add('QA-60', 'À importer')
        fake.add('QA-61', 'À importer aussi')
        unsent = DefectComment.objects.create(defect=linked, author=env.tester, content='Oublié')
        resp = api(env.owner).post(f'{env.base}/jira/sync/')
        assert resp.status_code == 200, resp.data
        assert resp.data == {'pushed': 1, 'updated': 1, 'imported': 2, 'errors': 0}
        linked.refresh_from_db()
        unsent.refresh_from_db()
        assert linked.status == 'resolved' and unsent.jira_comment_id
        assert linked.comments.filter(external_author='Awa (Jira)').exists()
        assert api(env.owner).post(f'{env.base}/jira/sync/').data == {'pushed': 0, 'updated': 0, 'imported': 0, 'errors': 0}
        assert api(env.tester).post(f'{env.base}/jira/sync/').status_code == 403

    def test_unreachable(self, env, fake):  # noqa: F811
        fake.fail = 'Jira est injoignable.'
        resp = api(env.owner).post(f'{env.base}/jira/sync/')
        assert resp.status_code == 400 and 'injoignable' in resp.data['error']
        with pytest.raises(SystemExit):
            call_command('sync_jira')

    def test_command(self, env, fake, settings, capsys):  # noqa: F811
        call_command('sync_jira')
        assert 'Portail' in capsys.readouterr().out
        settings.JIRA_API_TOKEN = ''
        call_command('sync_jira')
        assert 'non configuré' in capsys.readouterr().out


class TestJiraHttp:
    """Client HTTP réel contre un serveur local."""

    @pytest.fixture
    def server(self, settings):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                received.append((self.path, self.headers['Authorization'], json.loads(body)))
                code, answer = (201, b'{"key":"QA-1"}') if 'issue' in self.path else (
                    (400, b'{"errors":{"summary":"requis"}}') if 'bad' in self.path else (401, b'non'))
                self.send_response(code)
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *args):
                pass
        httpd = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        settings.JIRA_BASE_URL, settings.JIRA_EMAIL, settings.JIRA_API_TOKEN = f'http://127.0.0.1:{httpd.server_port}/', 'a', 'b'
        yield received
        httpd.shutdown()
        httpd.server_close()

    def test_requests_and_errors(self, server, settings):
        assert jira.api('POST', 'issue', {'é': 1}) == {'key': 'QA-1'}
        assert server[0] == ('/rest/api/3/issue', 'Basic YTpi', {'é': 1})
        with pytest.raises(jira.JiraError, match='summary : requis'):
            jira.api('POST', 'bad', {})
        with pytest.raises(jira.JiraError, match='refusé'):
            jira.api('POST', 'autre', {})
        settings.JIRA_BASE_URL = 'http://127.0.0.1:1'
        with pytest.raises(jira.JiraError, match='injoignable'):
            jira.api('POST', 'issue', {})

    def test_adf_round_trip(self):
        assert jira.from_adf(jira.to_adf('a\n\nb')) == 'a\n\nb'
        assert jira.from_adf(None) == '' and jira.from_adf('texte') == 'texte' and jira.from_adf([1]) == ''


class TestQASettings:
    def test_defaults_rights_and_validation(self, env, settings):  # noqa: F811
        url = f'{env.base}/qa-settings/'
        data = api(env.viewer).get(url).data
        assert data['alert_on_regression'] and data['auto_report_on_close'] and not data['jira_enabled']
        assert data['jira_configured'] is False and data['recipients_count'] == 2 and data['can_edit_jira'] is False
        assert api(env.tester).patch(url, {'alert_on_regression': False}, format='json').status_code == 403
        assert api(env.owner).patch(url, {'alert_on_regression': False}, format='json').data['alert_on_regression'] is False
        # La liaison Jira est réservée aux super-admins, même pour le propriétaire.
        assert api(env.owner).patch(url, {'jira_project_key': 'QA'}, format='json').status_code == 400
        assert api(env.owner).patch(url, {'jira_project_key': '', 'alert_on_regression': True}, format='json').status_code == 200
        env.owner.is_superuser = True
        env.owner.save()
        client = api(env.owner)
        assert client.patch(url, {'jira_enabled': True}, format='json').status_code == 400
        assert client.patch(url, {'jira_project_key': 'qa"; x'}, format='json').status_code == 400
        assert client.patch(url, {'jira_project_key': 'QA', 'jira_enabled': True}, format='json').status_code == 200
        other = Project.objects.create(team=env.team, name='Autre')
        resp = client.patch(f'/api/teams/{env.team.slug}/projects/{other.slug}/qa-settings/', {'jira_project_key': 'QA'}, format='json')
        assert resp.status_code == 400

    def test_secrets_are_never_exposed(self, env, settings):  # noqa: F811
        settings.JIRA_API_TOKEN = 'jeton-secret'
        assert 'jeton-secret' not in json.dumps(api(env.owner).get(f'{env.base}/qa-settings/').json())


# ═════════════════════════════════════════════════════════════════════════════
@pytest.fixture
def remote_storage(settings):
    """Stockage sans accès disque (même contrainte que S3)."""
    settings.STORAGES = {**settings.STORAGES, 'default': {'BACKEND': 'tests.remote_storage.RemoteStorage'}}


def png():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buffer, 'PNG')
    return buffer.getvalue()


class TestRemoteStorage:
    def test_local_copy_is_temporary(self, remote_storage):
        job = ConversionJob.objects.create(source_filename='a.docx', word_file=ContentFile(b'abc', 'a.docx'))
        with local_copy(job.word_file) as path:
            assert open(path, 'rb').read() == b'abc' and path.endswith('.docx')
        with pytest.raises(FileNotFoundError):
            open(path, 'rb')

    def test_word_upload_excel_logo_and_screenshots(self, env, remote_storage):  # noqa: F811
        from docx import Document
        document = Document()
        table = document.add_table(rows=2, cols=3)
        for i, text in enumerate(('Use Case', 'Description', 'Étapes')):
            table.rows[0].cells[i].text, table.rows[1].cells[i].text = text, f'{text} 1'
        buffer = io.BytesIO()
        document.save(buffer)
        resp = api(env.tester).post('/api/upload/', {
            'word_file': SimpleUploadedFile('r.docx', buffer.getvalue()), 'project': str(env.project.id),
            'company_logo': SimpleUploadedFile('l.png', png())}, format='multipart')
        assert resp.status_code == 201, resp.data
        case = ExtractedUseCase.objects.get(job_id=resp.data['id'])
        assignment = UseCaseAssignment.objects.create(use_case=case, sprint=env.s1)
        resp2 = api(env.tester).post(f'{env.base}/assignments/{assignment.id}/screenshots/',
                                     {'image': SimpleUploadedFile('c.png', png(), 'image/png')}, format='multipart')
        assert resp2.status_code == 201
        excel = api(env.tester).get(f'/api/jobs/{resp.data["id"]}/generate/')
        assert excel.status_code == 200
        assert len(load_workbook(io.BytesIO(excel.content))['Use Cases']._images) == 2

    def test_unreadable_remote_file_is_skipped(self, env, remote_storage):  # noqa: F811
        job = ConversionJob.objects.create(source_filename='a.docx', word_file='a.docx', status='DONE', project=env.project, company_logo='absent.png')
        ExtractedUseCase.objects.create(job=job, order=1)
        assert api(env.tester).get(f'/api/jobs/{job.id}/generate/').status_code == 200

    def test_files_are_deleted_with_their_record(self, env):  # noqa: F811
        defect = Defect.objects.create(project=env.project, title='x', attachment=ContentFile(b'x', 'x.txt'))
        shot = UseCaseScreenshot.objects.create(assignment=env.s1.assignments.first(), image=ContentFile(png(), 'x.png'))
        names = [defect.attachment.name, shot.image.name]
        assert all(default_storage.exists(n) for n in names)
        defect.delete()
        shot.delete()
        assert not any(default_storage.exists(n) for n in names)

    def test_check_and_migrate_commands(self, env, settings, tmp_path, capsys):  # noqa: F811
        Defect.objects.create(project=env.project, title='x', attachment=ContentFile(b'contenu', 'doc.txt'))
        Defect.objects.create(project=env.project, title='y', attachment='defects/perdu.txt')
        with pytest.raises(CommandError):
            call_command('media_storage', '--migrate')
        with pytest.raises(CommandError):
            call_command('media_storage')
        call_command('media_storage', '--check')
        settings.STORAGES = {**settings.STORAGES, 'default': {'BACKEND': 'tests.remote_storage.RemoteStorage'}}
        call_command('media_storage', '--check', '--migrate', '--dry-run')
        assert not default_storage.exists('defects/doc.txt')
        call_command('media_storage', '--migrate')
        call_command('media_storage', '--migrate')
        out = capsys.readouterr().out
        assert default_storage.open('defects/doc.txt').read() == b'contenu'
        assert '1 fichier(s) copié(s), 0 déjà présent(s), 1 introuvable(s)' in out and '0 fichier(s) copié(s), 1 déjà' in out

    @pytest.fixture
    def s3(self):
        """Serveur S3 minimal : vérifie le protocole réel (signature, en-têtes, chemins)."""
        objects, requests = {}, []

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, code, body=b''):
                self.send_response(code)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Last-Modified', 'Tue, 24 Nov 2026 10:00:00 GMT')
                self.send_header('ETag', '"x"')
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(body)

            def do_PUT(self):
                requests.append(dict(self.headers))
                objects[self.path.split('?')[0]] = self.rfile.read(int(self.headers['Content-Length']))
                self._answer(200)

            def do_GET(self):
                key = self.path.split('?')[0]
                self._answer(200, objects[key]) if key in objects else self._answer(404)

            do_HEAD = do_GET

            def do_DELETE(self):
                objects.pop(self.path.split('?')[0], None)
                self._answer(204)

            def log_message(self, *args):
                pass
        httpd = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        yield f'http://127.0.0.1:{httpd.server_port}', objects, requests
        httpd.shutdown()
        httpd.server_close()

    def test_real_s3_backend(self, env, s3, settings, capsys):  # noqa: F811
        from config.storage import s3_storage
        endpoint, objects, requests = s3
        settings.STORAGES = {**settings.STORAGES, 'default': s3_storage({
            'S3_BUCKET': 'qa', 'S3_ACCESS_KEY': 'k', 'S3_SECRET_KEY': 's', 'S3_REGION': 'eu-west-3',
            'S3_ENDPOINT_URL': endpoint, 'S3_ADDRESSING_STYLE': 'path'})}
        call_command('media_storage', '--check')
        assert 'S3Storage' in capsys.readouterr().out and objects == {}
        resp = api(env.tester).post(f'{env.base}/defects/', {
            'title': 'x', 'attachment': SimpleUploadedFile('log.txt', b'trace', 'text/plain')}, format='multipart')
        assert resp.status_code == 201
        assert objects == {'/qa/media/defects/log.txt': b'trace'}
        assert requests[-1]['Authorization'].startswith('AWS4-HMAC-SHA256')
        assert not any(h.lower() in ('x-amz-acl', 'x-amz-sdk-checksum-algorithm') for h in requests[-1])
        url = resp.data['attachment']
        assert 'X-Amz-Signature' in url and 'X-Amz-Expires=3600' in url
        # Même nom : le premier fichier n'est jamais écrasé.
        api(env.tester).post(f'{env.base}/defects/', {
            'title': 'y', 'attachment': SimpleUploadedFile('log.txt', b'autre', 'text/plain')}, format='multipart')
        assert len(objects) == 2 and objects['/qa/media/defects/log.txt'] == b'trace'
        defect = Defect.objects.get(title='x')
        with local_copy(defect.attachment) as path:
            assert open(path, 'rb').read() == b'trace'
        defect.delete()
        assert len(objects) == 1


class TestCleanupCommand:
    def test_only_anonymous_conversions_are_removed(self, env):  # noqa: F811
        old = timezone.now() - timedelta(days=2)
        anonymous = ConversionJob.objects.create(source_filename='a', word_file=ContentFile(b'x', 'a.docx'))
        mine = ConversionJob.objects.create(source_filename='b', word_file='b.docx', uploaded_by=env.owner)
        project = ConversionJob.objects.create(source_filename='c', word_file='c.docx', project=env.project)
        ConversionJob.objects.update(created_at=old)
        call_command('cleanup_old_files', '--dry-run')
        assert ConversionJob.objects.count() == 3 and default_storage.exists(anonymous.word_file.name)
        call_command('cleanup_old_files')
        assert set(ConversionJob.objects.all()) == {mine, project}
        assert not default_storage.exists(anonymous.word_file.name)


@pytest.mark.django_db(transaction=True)
class TestBackgroundExecution:
    def test_runs_in_another_thread_after_commit(self, settings):
        from django.db import transaction
        from apps.core import background
        settings.BACKGROUND_TASKS_INLINE = False
        done, seen = threading.Event(), []

        def task():
            seen.append(threading.current_thread().name)
            done.set()
        with transaction.atomic():
            background.run_in_background(task)
            assert not done.is_set()
        assert done.wait(5) and seen[0].startswith('qa-bg')

    def test_rolled_back_transaction_cancels_the_task(self, settings):
        from django.db import transaction
        from apps.core import background
        settings.BACKGROUND_TASKS_INLINE = False
        done = threading.Event()
        with pytest.raises(ZeroDivisionError), transaction.atomic():
            background.run_in_background(done.set)
            1 / 0
        assert not done.wait(0.3)
