import sys
import pytest
from django.test import TestCase, Client
from django.urls import reverse
from apps.core.models import ConversionJob, ExtractedUseCase
import uuid

PY314 = sys.version_info >= (3, 14)
skip_if_py314 = pytest.mark.skipif(PY314, reason='Django 5.0.6 incompatible avec Python 3.14')


@pytest.mark.django_db
class TestConversionJobModel:
    def test_creates_with_uuid(self):
        job = ConversionJob.objects.create(
            source_filename='test.docx',
            word_file='uploads/word/test.docx',
            status=ConversionJob.Status.PENDING,
        )
        assert job.id is not None
        assert isinstance(job.id, uuid.UUID)

    def test_default_status_is_pending(self):
        job = ConversionJob.objects.create(
            source_filename='test.docx',
            word_file='uploads/word/test.docx',
        )
        assert job.status == ConversionJob.Status.PENDING

    def test_str_representation(self):
        job = ConversionJob.objects.create(
            source_filename='test.docx',
            word_file='uploads/word/test.docx',
            status=ConversionJob.Status.DONE,
        )
        assert 'test.docx' in str(job)
        assert 'Terminé' in str(job)

    def test_ordering_by_created_at_desc(self):
        job1 = ConversionJob.objects.create(source_filename='first.docx', word_file='f1.docx')
        job2 = ConversionJob.objects.create(source_filename='second.docx', word_file='f2.docx')
        jobs = list(ConversionJob.objects.all())
        assert jobs[0].source_filename == 'second.docx'


@pytest.mark.django_db
class TestExtractedUseCaseModel:
    def setup_method(self):
        self.job = ConversionJob.objects.create(
            source_filename='test.docx',
            word_file='uploads/word/test.docx',
        )

    def test_creates_use_case(self):
        uc = ExtractedUseCase.objects.create(
            job=self.job,
            order=1,
            use_case_text='UC001',
            description='Test login',
        )
        assert uc.id is not None
        assert uc.job == self.job

    def test_default_status(self):
        uc = ExtractedUseCase.objects.create(job=self.job, order=1)
        assert uc.status == ExtractedUseCase.UCStatus.TO_TEST

    def test_default_not_automated(self):
        uc = ExtractedUseCase.objects.create(job=self.job, order=1)
        assert uc.is_automated is False

    def test_ordering_by_order(self):
        ExtractedUseCase.objects.create(job=self.job, order=3)
        ExtractedUseCase.objects.create(job=self.job, order=1)
        ExtractedUseCase.objects.create(job=self.job, order=2)
        ucs = list(self.job.use_cases.all())
        orders = [uc.order for uc in ucs]
        assert orders == [1, 2, 3]

    def test_cascade_delete(self):
        ExtractedUseCase.objects.create(job=self.job, order=1, use_case_text='UC001')
        self.job.delete()
        assert ExtractedUseCase.objects.count() == 0


@pytest.mark.django_db
@skip_if_py314
class TestIndexView:
    def test_get_returns_200(self, client):
        response = client.get(reverse('core:index'))
        assert response.status_code == 200

    def test_uses_correct_template(self, client):
        response = client.get(reverse('core:index'))
        assert 'core/index.html' in [t.name for t in response.templates]

    def test_post_without_file_shows_error(self, client):
        response = client.post(reverse('core:index'), {})
        assert response.status_code == 200  # re-renders form with errors


@pytest.mark.django_db
class TestFileSearchAPI:
    def test_short_query_returns_error(self, client):
        response = client.get('/api/files/search/?q=a')
        assert response.status_code == 200
        data = response.json()
        assert data['results'] == []

    def test_missing_query_returns_empty(self, client):
        response = client.get('/api/files/search/')
        assert response.status_code == 200
        data = response.json()
        assert data['results'] == []

    def test_valid_query_returns_list(self, client):
        response = client.get('/api/files/search/?q=test')
        assert response.status_code == 200
        data = response.json()
        assert 'results' in data
        assert isinstance(data['results'], list)
