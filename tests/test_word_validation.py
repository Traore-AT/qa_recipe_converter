import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document

from apps.core.word_validation import WordFileValidationError, validate_word_upload


def _make_docx_bytes() -> bytes:
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = 'Use Case'
    table.rows[0].cells[1].text = 'Description'
    table.rows[1].cells[0].text = 'UC001'
    table.rows[1].cells[1].text = 'Test'
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


class TestValidateWordUpload:
    def test_accepts_valid_docx(self):
        content = _make_docx_bytes()
        uploaded = SimpleUploadedFile('recette.docx', content)
        validate_word_upload(uploaded)

    def test_rejects_missing_file_extension(self):
        uploaded = SimpleUploadedFile('recette.txt', _make_docx_bytes())
        with pytest.raises(WordFileValidationError, match='Format non supporté'):
            validate_word_upload(uploaded)

    def test_rejects_empty_file(self):
        uploaded = SimpleUploadedFile('recette.docx', b'')
        with pytest.raises(WordFileValidationError, match='vide'):
            validate_word_upload(uploaded)

    def test_rejects_fake_docx(self):
        uploaded = SimpleUploadedFile('recette.docx', b'not a zip file')
        with pytest.raises(WordFileValidationError, match='valide'):
            validate_word_upload(uploaded)

    def test_accepts_doc_ole_format(self):
        content = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 100
        uploaded = SimpleUploadedFile('ancien.doc', content)
        validate_word_upload(uploaded)

    def test_rejects_docx_without_document_xml(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('word/styles.xml', '<styles/>')
        uploaded = SimpleUploadedFile('broken.docx', buffer.getvalue())
        with pytest.raises(WordFileValidationError, match='incomplet ou corrompu'):
            validate_word_upload(uploaded)

    def test_rejects_corrupted_zip(self):
        uploaded = SimpleUploadedFile('broken.docx', b'PK\x03\x04corrupted')
        with pytest.raises(WordFileValidationError, match='corrompu'):
            validate_word_upload(uploaded)
