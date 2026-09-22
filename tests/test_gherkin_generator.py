import io
import zipfile
import pytest
from apps.core.models import ConversionJob, ExtractedUseCase
from apps.parser.gherkin_generator import (
    _slugify, _split_lines, _format_step,
    generate_feature_file, generate_cypress_step_definitions,
    generate_cypress_project_zip, generate_gherkin_only_zip,
)


@pytest.fixture
def job(db):
    return ConversionJob.objects.create(
        source_filename='test.docx',
        word_file='uploads/word/test.docx',
    )


@pytest.fixture
def use_case(job):
    return ExtractedUseCase.objects.create(
        job=job, order=1, use_case_text='UC001',
        description='Test de connexion utilisateur',
        preconditions='Utilisateur existe\nMot de passe valide',
        steps='Ouvrir la page de login\nSaisir identifiants\nCliquer sur Connexion',
        expected_results='Redirection vers le dashboard\nMessage de bienvenue affiché',
        is_automated=True,
    )


class TestSlugify:
    def test_basic_slug(self):
        assert _slugify('Hello World') == 'hello_world'

    def test_accents(self):
        assert _slugify('Équipe Qualité') == 'equipe_qualite'

    def test_special_chars(self):
        assert _slugify('test@#$%^&*()') == 'test'

    def test_truncates_at_60(self):
        long = 'a' * 100
        assert len(_slugify(long)) <= 60

    def test_empty_fallsback(self):
        assert _slugify('') == 'use_case'

    def test_spaces_to_underscores(self):
        assert _slugify('one two three') == 'one_two_three'

    def test_leading_trailing_spaces(self):
        assert _slugify('  hello  ') == 'hello'


class TestSplitLines:
    def test_newline_separated(self):
        assert _split_lines('a\nb\nc') == ['a', 'b', 'c']

    def test_semicolon_separated(self):
        assert _split_lines('a;b;c') == ['a', 'b', 'c']

    def test_bullet_separated(self):
        assert _split_lines('a•b•c') == ['a', 'b', 'c']

    def test_empty_fallsback(self):
        assert _split_lines('') == ['(non défini)']

    def test_none_fallsback(self):
        assert _split_lines(None) == ['(non défini)']

    def test_double_dash_separator(self):
        assert _split_lines('a--b') == ['a', 'b']

    def test_removes_empty_lines(self):
        assert _split_lines('a\n\n\nb') == ['a', 'b']

    def test_strips_whitespace(self):
        assert _split_lines('  a  \n  b  ') == ['a', 'b']


class TestFormatStep:
    def test_basic_format(self):
        assert _format_step('ouvrir la page') == '    And Ouvrir la page'

    def test_removes_trailing_dot(self):
        assert _format_step('ouvrir la page.') == '    And Ouvrir la page'

    def test_capitalizes_first_letter(self):
        assert _format_step('ouvrir') == '    And Ouvrir'

    def test_empty_returns_empty(self):
        assert _format_step('') == ''


class TestGenerateFeatureFile:
    def test_generates_feature_content(self, use_case):
        content = generate_feature_file(use_case)
        assert 'Feature:' in content
        assert 'UC-001' in content
        assert 'Background:' in content
        assert 'Scenario:' in content
        assert 'Given' in content
        assert 'When' in content
        assert 'Then' in content

    def test_includes_preconditions(self, use_case):
        content = generate_feature_file(use_case)
        assert 'Utilisateur existe' in content
        assert 'Mot de passe valide' in content

    def test_includes_steps(self, use_case):
        content = generate_feature_file(use_case)
        assert 'Ouvrir la page de login' in content
        assert 'Saisir identifiants' in content

    def test_includes_expected_results(self, use_case):
        content = generate_feature_file(use_case)
        assert 'Redirection vers le dashboard' in content

    def test_no_preconditions_omits_background(self, job):
        uc = ExtractedUseCase.objects.create(job=job, order=1, description='Simple')
        content = generate_feature_file(uc)
        assert 'Background:' not in content

    def test_default_ids_when_missing(self, job):
        uc = ExtractedUseCase.objects.create(job=job, order=42, description='Test')
        content = generate_feature_file(uc)
        assert 'UC-042' in content

    def test_no_description_fallback(self, job):
        uc = ExtractedUseCase.objects.create(job=job, order=1)
        content = generate_feature_file(uc)
        assert 'Cas de test' in content


class TestGenerateCypressStepDefinitions:
    def test_generates_js_content(self, use_case):
        js = generate_cypress_step_definitions(use_case)
        assert 'Step definitions' in js
        assert 'Given(' in js
        assert 'When(' in js
        assert 'Then(' in js
        assert 'TODO' in js

    def test_handles_special_chars(self, job):
        uc = ExtractedUseCase.objects.create(
            job=job, order=1, description="Test d'API",
            steps="Lancer l'appel avec l'apostrophe",
            expected_results="Réponse 200 OK",
            is_automated=True,
        )
        js = generate_cypress_step_definitions(uc)
        assert "\\'" in js


class TestGenerateGherkinOnlyZip:
    def test_returns_valid_zip(self, use_case):
        buf = generate_gherkin_only_zip([use_case])
        assert buf.getvalue()[:2] == b'PK'
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            feature_files = [n for n in names if n.endswith('.feature')]
            assert len(feature_files) == 1

    def test_no_automated_adds_readme(self, job):
        uc = ExtractedUseCase.objects.create(job=job, order=1, description='Manual', is_automated=False)
        buf = generate_gherkin_only_zip([uc])
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert any('README' in n for n in names)

    def test_empty_list_adds_readme(self):
        buf = generate_gherkin_only_zip([])
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert any('README' in n for n in names)


class TestGenerateCypressProjectZip:
    def test_returns_valid_zip(self, use_case):
        buf = generate_cypress_project_zip([use_case])
        assert buf.getvalue()[:2] == b'PK'
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert 'cypress.config.js' in names
            assert 'package.json' in names
            assert '.gitignore' in names
            assert 'README.md' in names

    def test_includes_feature_and_step_defs(self, use_case):
        buf = generate_cypress_project_zip([use_case])
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            feature_files = [n for n in names if n.endswith('.feature')]
            step_defs = [n for n in names if 'step_definitions' in n]
            assert len(feature_files) >= 1
            assert len(step_defs) >= 1

    def test_no_automated_adds_placeholder(self, job):
        uc = ExtractedUseCase.objects.create(job=job, order=1, description='Manual', is_automated=False)
        buf = generate_cypress_project_zip([uc])
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert any('placeholder' in n for n in names)

    def test_company_name_in_readme(self, use_case):
        buf = generate_cypress_project_zip([use_case], company_name='MyCorp')
        with zipfile.ZipFile(buf) as zf:
            readme = zf.read('README.md').decode()
            assert 'MyCorp' in readme
