"""
Tests unitaires — Export CSV & Captures d'écran

Couvre :
- GenerateCSVAPIView (export CSV depuis un job de conversion)
- UseCaseDetailView (détail complet d'un assignment)
- UseCaseScreenshotListCreateView / UseCaseScreenshotDeleteView
- SprintCSVExportView (export CSV d'un sprint)
- Notifications email lors de l'assignation et de la complétion
"""
import csv
import io
import uuid
import pytest
from datetime import date, timedelta
from django.contrib.auth.models import User
from django.core import mail
from django.test.utils import override_settings
from django.urls import reverse

from rest_framework.test import APIClient
from rest_framework import status

from apps.core.models import ConversionJob, ExtractedUseCase
from apps.teams.models import Team, TeamMember, Project, ProjectMember
from apps.qamanagement.models import Sprint, UseCaseAssignment, UseCaseScreenshot, UseCaseComment


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def owner(db):
    return User.objects.create_user('owner', 'owner@test.com', 'pass1234')


@pytest.fixture
def lead_user(db):
    return User.objects.create_user('lead', 'lead@test.com', 'pass1234')


@pytest.fixture
def tester_user(db):
    return User.objects.create_user('tester', 'tester@test.com', 'pass1234')


@pytest.fixture
def outsider(db):
    return User.objects.create_user('outsider', 'outsider@test.com', 'pass1234')


@pytest.fixture
def team(db, owner, lead_user, tester_user):
    t = Team.objects.create(name='Test Team', owner=owner)
    TeamMember.objects.create(team=t, user=owner, role='owner')
    TeamMember.objects.create(team=t, user=lead_user, role='admin')
    TeamMember.objects.create(team=t, user=tester_user, role='member')
    return t


@pytest.fixture
def project(db, team, owner):
    p = Project.objects.create(team=team, name='Test Project', created_by=owner, visibility='team')
    ProjectMember.objects.create(project=p, user=owner, role='lead')
    return p


@pytest.fixture
def lead_pm(db, project, lead_user):
    return ProjectMember.objects.create(project=project, user=lead_user, role='lead')


@pytest.fixture
def tester_pm(db, project, tester_user):
    return ProjectMember.objects.create(project=project, user=tester_user, role='tester')


@pytest.fixture
def done_job(db, project, owner):
    job = ConversionJob.objects.create(
        source_filename='recette_test.docx',
        word_file='uploads/word/recette_test.docx',
        status=ConversionJob.Status.DONE,
        use_cases_count=3,
        project=project,
        uploaded_by=owner,
    )
    ucs = []
    for i in range(3):
        uc = ExtractedUseCase.objects.create(
            job=job, order=i + 1,
            use_case_text=f'UC{i+1:03d}',
            description=f'Cas de test #{i+1}',
            preconditions='Utilisateur connecté sur l\'application',
            steps=f'Étape {i+1}.1\nÉtape {i+1}.2\nÉtape {i+1}.3',
            expected_results=f'Résultat attendu {i+1}',
            observed_results=f'Résultat observé {i+1}' if i > 0 else '',
            is_automated=(i == 1),
            status='À tester' if i == 0 else 'Passé' if i == 1 else 'Échoué',
        )
        ucs.append(uc)
    return job, ucs


@pytest.fixture
def pending_job(db):
    return ConversionJob.objects.create(
        source_filename='pending.docx',
        word_file='uploads/word/pending.docx',
        status=ConversionJob.Status.PENDING,
    )


@pytest.fixture
def sprint(db, project, owner):
    return Sprint.objects.create(
        project=project, name='Sprint Test',
        goal='Valider les fonctionnalités',
        start_date=date.today(),
        end_date=date.today() + timedelta(days=14),
        status='active', created_by=owner,
    )


@pytest.fixture
def assignment(db, sprint, done_job, tester_pm, owner):
    uc = done_job[1][0]
    return UseCaseAssignment.objects.create(
        use_case=uc, sprint=sprint,
        assigned_to=tester_pm, assigned_by=owner,
        status='À tester',
    )


@pytest.fixture
def screenshot(db, assignment, owner):
    return UseCaseScreenshot.objects.create(
        assignment=assignment,
        image='screenshots/test.png',
        caption='Capture écran test',
        uploaded_by=owner,
    )


# ═════════════════════════════════════════════════════════════════════════════
# TESTS — GenerateCSVAPIView (conversion job → CSV)
# ═════════════════════════════════════════════════════════════════════════════

class TestGenerateCSVAPI:
    """Tests pour l'export CSV depuis un job de conversion"""

    def test_csv_returns_valid_content_type(self, api_client, done_job):
        resp = api_client.get(f'/api/jobs/{done_job[0].id}/csv/')
        assert resp.status_code == 200
        assert resp['Content-Type'] == 'text/csv; charset=utf-8'

    def test_csv_has_correct_filename(self, api_client, done_job):
        resp = api_client.get(f'/api/jobs/{done_job[0].id}/csv/')
        assert resp.status_code == 200
        assert 'recette' in resp['Content-Disposition']
        assert resp['Content-Disposition'].endswith('.csv"')

    def test_csv_headers(self, api_client, done_job):
        resp = api_client.get(f'/api/jobs/{done_job[0].id}/csv/')
        content = resp.content.decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        headers = next(reader)
        expected = ['N°', 'CAS', 'Use Case ID', 'Description', 'Préconditions',
                     'Étapes', 'Résultats Attendus', 'Résultats Observés',
                     'Statut', 'Automatisé']
        assert headers == expected

    def test_csv_includes_all_use_cases(self, api_client, done_job):
        resp = api_client.get(f'/api/jobs/{done_job[0].id}/csv/')
        content = resp.content.decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) == 4  # header + 3 use cases

    def test_csv_use_case_data(self, api_client, done_job):
        _, ucs = done_job
        resp = api_client.get(f'/api/jobs/{done_job[0].id}/csv/')
        content = resp.content.decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        next(reader)  # skip header
        rows = list(reader)
        row1 = rows[0]
        assert row1[0] == '1'
        assert row1[1] == f"UC-{ucs[0].order:03d}"
        assert row1[2] == ucs[0].use_case_text
        assert row1[3] == ucs[0].description
        assert row1[8] == ucs[0].status

    def test_csv_automated_column(self, api_client, done_job):
        resp = api_client.get(f'/api/jobs/{done_job[0].id}/csv/')
        content = resp.content.decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        next(reader)  # skip header
        rows = list(reader)
        assert rows[0][9] == 'Non'  # UC1 not automated
        assert rows[1][9] == 'Oui'  # UC2 automated
        assert rows[2][9] == 'Non'  # UC3 not automated

    def test_csv_rejects_non_done_job(self, api_client, pending_job):
        resp = api_client.get(f'/api/jobs/{pending_job.id}/csv/')
        assert resp.status_code == 404

    def test_csv_job_not_found(self, api_client, db):
        resp = api_client.get(f'/api/jobs/{uuid.uuid4()}/csv/')
        assert resp.status_code == 404

    def test_csv_empty_use_cases_returns_headers_only(self, api_client, db):
        job = ConversionJob.objects.create(
            source_filename='empty.docx',
            word_file='uploads/word/empty.docx',
            status=ConversionJob.Status.DONE,
            use_cases_count=0,
        )
        resp = api_client.get(f'/api/jobs/{job.id}/csv/')
        assert resp.status_code == 200
        content = resp.content.decode('utf-8')
        assert content.strip().startswith('N°')


# ═════════════════════════════════════════════════════════════════════════════
# TESTS — UseCaseDetailView
# ═════════════════════════════════════════════════════════════════════════════

class TestUseCaseDetailView:
    def test_detail_returns_all_sections(self, client, team, project, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:uc-detail', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert resp.status_code == 200
        assert 'assignment' in resp.data
        assert 'use_case' in resp.data
        assert 'screenshots' in resp.data
        assert 'comments' in resp.data

    def test_detail_contains_assignment_info(self, client, team, project, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:uc-detail', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert resp.data['assignment']['id'] == str(assignment.id)
        assert resp.data['assignment']['status'] == 'À tester'

    def test_detail_contains_use_case_data(self, client, team, project, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:uc-detail', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert resp.data['use_case']['id'] == str(assignment.use_case.id)
        assert resp.data['use_case']['description'] == assignment.use_case.description
        assert resp.data['use_case']['steps'] == assignment.use_case.steps

    def test_detail_includes_screenshots(self, client, team, project, assignment, screenshot, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:uc-detail', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert len(resp.data['screenshots']) == 1
        assert resp.data['screenshots'][0]['caption'] == 'Capture écran test'

    def test_detail_includes_comments(self, client, team, project, assignment, owner, tester_user):
        UseCaseComment.objects.create(
            use_case=assignment.use_case, author=tester_user,
            content='Test effectué avec succès'
        )
        client.force_authenticate(owner)
        url = reverse('qamanagement:uc-detail', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert len(resp.data['comments']) == 1
        assert resp.data['comments'][0]['content'] == 'Test effectué avec succès'

    def test_detail_not_found(self, client, team, project, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:uc-detail', args=[team.slug, project.slug, uuid.uuid4()])
        resp = client.get(url)
        assert resp.status_code == 404

    def test_detail_requires_auth(self, client, team, project, assignment):
        url = reverse('qamanagement:uc-detail', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert resp.status_code in (401, 403)

    def test_detail_outsider_forbidden(self, client, team, project, assignment, outsider):
        client.force_authenticate(outsider)
        url = reverse('qamanagement:uc-detail', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert resp.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# TESTS — UseCaseScreenshot views
# ═════════════════════════════════════════════════════════════════════════════

class TestUseCaseScreenshotViews:
    def test_list_screenshots_empty(self, client, team, project, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:screenshot-list', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.data['count'] == 0
        assert resp.data['results'] == []

    def test_list_screenshots_with_data(self, client, team, project, assignment, screenshot, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:screenshot-list', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.data['count'] == 1

    def test_upload_screenshot(self, client, team, project, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:screenshot-list', args=[team.slug, project.slug, assignment.id])
        resp = client.post(url, {
            'image': io.BytesIO(b'fake-image-data'),
            'caption': 'Nouvelle capture',
        }, format='multipart')
        assert resp.status_code == 201
        assert resp.data['caption'] == 'Nouvelle capture'
        assert str(resp.data['assignment']) == str(assignment.id)

    def test_upload_screenshot_without_image_returns_400(self, client, team, project, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:screenshot-list', args=[team.slug, project.slug, assignment.id])
        resp = client.post(url, {'caption': 'No image'}, format='multipart')
        assert resp.status_code == 400

    def test_delete_screenshot(self, client, team, project, assignment, screenshot, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:screenshot-delete', args=[
            team.slug, project.slug, assignment.id, screenshot.id
        ])
        resp = client.delete(url)
        assert resp.status_code == 204
        assert UseCaseScreenshot.objects.filter(id=screenshot.id).count() == 0

    def test_delete_screenshot_not_found(self, client, team, project, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:screenshot-delete', args=[
            team.slug, project.slug, assignment.id, uuid.uuid4()
        ])
        resp = client.delete(url)
        assert resp.status_code == 404

    def test_screenshot_requires_auth(self, client, team, project, assignment):
        url = reverse('qamanagement:screenshot-list', args=[team.slug, project.slug, assignment.id])
        resp = client.get(url)
        assert resp.status_code in (401, 403)

    def test_upload_creates_activity_log(self, client, team, project, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:screenshot-list', args=[team.slug, project.slug, assignment.id])
        resp = client.post(url, {
            'image': io.BytesIO(b'fake-image'),
            'caption': 'Activity test',
        }, format='multipart')
        assert resp.status_code == 201
        from apps.qamanagement.models import ActivityLog
        logs = ActivityLog.objects.filter(action_type='screenshot_uploaded')
        assert logs.count() >= 1
        assert 'Capture ajoutée' in logs.first().description


# ═════════════════════════════════════════════════════════════════════════════
# TESTS — SprintCSVExportView
# ═════════════════════════════════════════════════════════════════════════════

class TestSprintCSVExport:
    def test_sprint_csv_returns_valid_content(self, client, team, project, sprint, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:sprint-csv-export', args=[team.slug, project.slug, sprint.id])
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp['Content-Type'] == 'text/csv; charset=utf-8'

    def test_sprint_csv_has_correct_headers(self, client, team, project, sprint, assignment, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:sprint-csv-export', args=[team.slug, project.slug, sprint.id])
        resp = client.get(url)
        content = resp.content.decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        headers = next(reader)
        expected = ['N°', 'CAS', 'Use Case ID', 'Description', 'Préconditions',
                     'Étapes', 'Résultats Attendus', 'Résultats Observés',
                     'Assigné à', 'Statut', 'Automatisé']
        assert headers == expected

    def test_sprint_csv_includes_assignment_data(self, client, team, project, sprint, assignment, owner, tester_user):
        client.force_authenticate(owner)
        url = reverse('qamanagement:sprint-csv-export', args=[team.slug, project.slug, sprint.id])
        resp = client.get(url)
        content = resp.content.decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        next(reader)  # skip header
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0][8] == tester_user.get_full_name() or tester_user.username

    def test_sprint_csv_multiple_assignments(self, client, team, project, sprint, done_job, tester_pm, owner, lead_user):
        client.force_authenticate(owner)
        ucs = done_job[1]
        for i, uc in enumerate(ucs):
            UseCaseAssignment.objects.create(
                use_case=uc, sprint=sprint,
                assigned_to=tester_pm, assigned_by=owner,
                status='Passé' if i == 0 else 'En cours',
            )
        url = reverse('qamanagement:sprint-csv-export', args=[team.slug, project.slug, sprint.id])
        resp = client.get(url)
        content = resp.content.decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) == 4  # header + 3 use cases

    def test_sprint_csv_not_found(self, client, team, project, owner):
        client.force_authenticate(owner)
        url = reverse('qamanagement:sprint-csv-export', args=[team.slug, project.slug, uuid.uuid4()])
        resp = client.get(url)
        assert resp.status_code == 404

    def test_sprint_csv_requires_auth(self, client, team, project, sprint):
        url = reverse('qamanagement:sprint-csv-export', args=[team.slug, project.slug, sprint.id])
        resp = client.get(url)
        assert resp.status_code in (401, 403)


# ═════════════════════════════════════════════════════════════════════════════
# TESTS — Notifications email
# ═════════════════════════════════════════════════════════════════════════════

class TestEmailNotifications:
    """Teste l'envoi d'emails lors des assignations et complétions"""

    def test_assignment_triggers_email(self, client, team, project, sprint, done_job, tester_pm, owner, lead_user):
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            ucs = done_job[1]
            mail.outbox.clear()
            client.force_authenticate(lead_user)
            url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
            resp = client.post(url, {
                'use_case_ids': [str(ucs[0].id)],
                'sprint_id': str(sprint.id),
                'user_id': tester_pm.user.id,
            }, format='json')
            assert resp.status_code == 201
            assert len(mail.outbox) >= 1
            assert 'Nouveau cas assigné' in mail.outbox[0].subject

    def test_assignment_email_sent_to_assigned_user(self, client, team, project, sprint, done_job, tester_pm, owner, lead_user):
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            ucs = done_job[1]
            mail.outbox.clear()
            client.force_authenticate(lead_user)
            url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
            resp = client.post(url, {
                'use_case_ids': [str(ucs[0].id)],
                'sprint_id': str(sprint.id),
                'user_id': tester_pm.user.id,
            }, format='json')
            assert resp.status_code == 201
            assert tester_pm.user.email in mail.outbox[0].to

    def test_assignment_email_not_sent_without_sprint(self, client, team, project, done_job, tester_pm, owner, lead_user):
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            ucs = done_job[1]
            mail.outbox.clear()
            client.force_authenticate(lead_user)
            url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
            resp = client.post(url, {
                'use_case_ids': [str(ucs[0].id)],
                'user_id': tester_pm.user.id,
            }, format='json')
            assert resp.status_code == 201
            assert len(mail.outbox) == 0

    def test_completion_triggers_email_to_leads(self, client, team, project, sprint, done_job, tester_pm, owner, lead_user):
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            ucs = done_job[1]
            # Reset all UC statuses to 'À tester'
            for uc in ucs:
                uc.status = 'À tester'
                uc.save()
            mail.outbox.clear()
            client.force_authenticate(lead_user)
            assign_url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
            client.post(assign_url, {
                'use_case_ids': [str(uc.id) for uc in ucs],
                'sprint_id': str(sprint.id),
                'user_id': tester_pm.user.id,
            }, format='json')

            mail.outbox.clear()

            client.force_authenticate(tester_pm.user)
            for uc in ucs:
                client.patch(
                    reverse('qamanagement:uc-status-update', args=[team.slug, project.slug, uc.id]),
                    {'status': 'Passé'}, format='json'
                )

            assert len(mail.outbox) >= 1
            assert 'terminé' in mail.outbox[0].subject.lower()

    def test_completion_email_not_sent_when_not_all_done(self, client, team, project, sprint, done_job, tester_pm, owner, lead_user):
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            ucs = done_job[1]
            # Reset all UC statuses to 'À tester'
            for uc in ucs:
                uc.status = 'À tester'
                uc.save()
            mail.outbox.clear()
            client.force_authenticate(lead_user)
            assign_url = reverse('qamanagement:assignment-list', args=[team.slug, project.slug])
            client.post(assign_url, {
                'use_case_ids': [str(uc.id) for uc in ucs],
                'sprint_id': str(sprint.id),
                'user_id': tester_pm.user.id,
            }, format='json')

            mail.outbox.clear()

            client.force_authenticate(tester_pm.user)
            client.patch(
                reverse('qamanagement:uc-status-update', args=[team.slug, project.slug, ucs[0].id]),
                {'status': 'Passé'}, format='json'
            )

            assert len(mail.outbox) == 0


# ═════════════════════════════════════════════════════════════════════════════
# TESTS — Modèle UseCaseScreenshot
# ═════════════════════════════════════════════════════════════════════════════

class TestUseCaseScreenshotModel:
    def test_create_screenshot(self, db, assignment, owner):
        ss = UseCaseScreenshot.objects.create(
            assignment=assignment, image='screenshots/test.png',
            caption='Test', uploaded_by=owner,
        )
        assert ss.id is not None
        assert str(ss) == f'Screenshot for {assignment} — {ss.uploaded_at:%d/%m/%Y %H:%M}'

    def test_screenshot_default_ordering(self, db, assignment, owner):
        ss1 = UseCaseScreenshot.objects.create(
            assignment=assignment, image='screenshots/a.png', uploaded_by=owner,
        )
        ss2 = UseCaseScreenshot.objects.create(
            assignment=assignment, image='screenshots/b.png', uploaded_by=owner,
        )
        qs = UseCaseScreenshot.objects.all()
        assert list(qs) == [ss1, ss2]

    def test_screenshot_cascade_delete(self, db, assignment, owner):
        ss = UseCaseScreenshot.objects.create(
            assignment=assignment, image='screenshots/test.png',
            caption='To be deleted', uploaded_by=owner,
        )
        ss_id = ss.id
        assignment.delete()
        assert UseCaseScreenshot.objects.filter(id=ss_id).count() == 0

    def test_screenshot_null_uploaded_by(self, db, assignment):
        ss = UseCaseScreenshot.objects.create(
            assignment=assignment, image='screenshots/anonymous.png',
        )
        assert ss.uploaded_by is None
