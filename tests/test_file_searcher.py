import os
import pytest
from apps.parser.file_searcher import search_files, _human_size


class TestHumanSize:
    def test_bytes(self):
        assert _human_size(512) == "512.0 B"

    def test_kilobytes(self):
        assert _human_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _human_size(5 * 1024 * 1024) == "5.0 MB"


class TestSearchFiles:
    def test_empty_query_returns_empty(self):
        result = search_files('')
        assert result == []

    def test_short_query_returns_empty(self):
        result = search_files('a')
        assert result == []

    def test_search_in_temp_dir(self, tmp_path):
        # Create test files
        (tmp_path / "recette_sprint.docx").write_bytes(b"fake")
        (tmp_path / "recette_backup.docx").write_bytes(b"fake")
        (tmp_path / "template.xlsx").write_bytes(b"fake")
        (tmp_path / "notes.txt").write_bytes(b"text file")

        results = search_files("recette", search_root=str(tmp_path))
        names = [r['name'] for r in results]
        assert "recette_sprint.docx" in names
        assert "recette_backup.docx" in names
        assert "notes.txt" not in names  # wrong extension

    def test_extension_filter(self, tmp_path):
        (tmp_path / "file.docx").write_bytes(b"fake")
        (tmp_path / "file.xlsx").write_bytes(b"fake")

        results = search_files("file", search_root=str(tmp_path), extensions=['.xlsx'])
        names = [r['name'] for r in results]
        assert "file.xlsx" in names
        assert "file.docx" not in names

    def test_result_structure(self, tmp_path):
        (tmp_path / "test_file.docx").write_bytes(b"content")
        results = search_files("test_file", search_root=str(tmp_path))
        assert len(results) == 1
        r = results[0]
        assert 'name' in r
        assert 'path' in r
        assert 'size' in r
        assert 'size_human' in r
        assert 'extension' in r
        assert r['extension'] == '.docx'

    def test_max_results_respected(self, tmp_path):
        for i in range(10):
            (tmp_path / f"document_{i}.docx").write_bytes(b"fake")
        results = search_files("document", search_root=str(tmp_path), max_results=3)
        assert len(results) <= 3

    def test_case_insensitive_search(self, tmp_path):
        (tmp_path / "Recette_QA.docx").write_bytes(b"fake")
        results = search_files("recette_qa", search_root=str(tmp_path))
        assert len(results) == 1

    def test_nonexistent_root(self):
        results = search_files("test", search_root="/nonexistent/path/xyz")
        assert results == []
