import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_DOC_OLE_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


def _find_libreoffice() -> str | None:
    for candidate in [
        'soffice',
        'soffice.exe',
        r'C:\Program Files\LibreOffice\program\soffice.exe',
        r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
        '/usr/bin/libreoffice',
        '/usr/bin/soffice',
        '/snap/bin/libreoffice',
    ]:
        if candidate == 'soffice' or candidate == 'soffice.exe':
            try:
                subprocess.run(
                    [candidate, '--headless', '--version'],
                    capture_output=True, timeout=10, check=True
                )
                return candidate
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
                continue
        elif os.path.isfile(candidate):
            return candidate
    return None


def is_doc_file(file_path: str) -> bool:
    with open(file_path, 'rb') as f:
        header = f.read(8)
    return header.startswith(_DOC_OLE_SIGNATURE)


def is_libreoffice_available() -> bool:
    return _find_libreoffice() is not None


def convert_doc_to_docx(doc_path: str) -> str | None:
    soffice = _find_libreoffice()
    if soffice is None:
        logger.error("LibreOffice not found. Cannot convert .doc to .docx.")
        return None

    doc_path = os.path.abspath(doc_path)
    out_dir = tempfile.mkdtemp(prefix='doc_convert_')

    try:
        result = subprocess.run(
            [soffice, '--headless', '--convert-to', 'docx', '--outdir', out_dir, doc_path],
            capture_output=True, timeout=60, check=True
        )
        logger.info("LibreOffice conversion stdout: %s", result.stdout.decode('utf-8', errors='replace'))
        logger.info("LibreOffice conversion stderr: %s", result.stderr.decode('utf-8', errors='replace'))

        stem = Path(doc_path).stem
        converted = os.path.join(out_dir, f"{stem}.docx")
        if os.path.isfile(converted):
            return converted

        converted_lower = os.path.join(out_dir, f"{stem}.DOCX")
        if os.path.isfile(converted_lower):
            return converted_lower

        for f in os.listdir(out_dir):
            if f.lower().endswith('.docx'):
                return os.path.join(out_dir, f)

        logger.error("LibreOffice did not produce output .docx for %s (contents: %s)", doc_path, os.listdir(out_dir))
        return None

    except subprocess.TimeoutExpired:
        logger.error("LibreOffice conversion timed out for %s", doc_path)
        return None
    except subprocess.CalledProcessError as e:
        logger.error("LibreOffice conversion failed for %s: %s", doc_path, e)
        return None
    except Exception as e:
        logger.exception("Unexpected LibreOffice error for %s", doc_path)
        return None