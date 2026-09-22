import os
import zipfile

from django.conf import settings

# Signature OLE — anciens fichiers .doc (Word 97-2003)
_DOC_OLE_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


class WordFileValidationError(Exception):
    """Fichier Word rejeté avant parsing (erreur côté client)."""


def validate_word_upload(file_obj) -> None:
    """
    Valide un fichier Word uploadé (extension, taille, format réel).
    Lève WordFileValidationError avec un message utilisateur en cas d'échec.
    """
    name = getattr(file_obj, 'name', '') or ''
    ext = os.path.splitext(name)[1].lower()

    if ext not in settings.ALLOWED_WORD_EXTENSIONS:
        allowed = ', '.join(settings.ALLOWED_WORD_EXTENSIONS)
        raise WordFileValidationError(
            f"Format non supporté. Formats acceptés : {allowed}"
        )

    size = getattr(file_obj, 'size', None)
    if size is not None and size == 0:
        raise WordFileValidationError("Le fichier est vide.")

    if size is not None and size > settings.MAX_UPLOAD_SIZE:
        max_mb = settings.MAX_UPLOAD_SIZE // (1024 * 1024)
        raise WordFileValidationError(
            f"Fichier trop volumineux. Taille maximale : {max_mb} MB"
        )

    pos = file_obj.tell()
    try:
        header = file_obj.read(8)
        file_obj.seek(pos)

        if header.startswith(_DOC_OLE_SIGNATURE):
            # Old .doc (Word 97-2003) format is accepted; skip ZIP-based checks
            return

        if not zipfile.is_zipfile(file_obj):
            raise WordFileValidationError(
                "Le fichier n'est pas un document Word .docx valide. "
                "Vérifiez qu'il n'est pas corrompu ou qu'il s'agit bien d'un fichier .docx."
            )

        file_obj.seek(pos)
        try:
            with zipfile.ZipFile(file_obj) as zf:
                if 'word/document.xml' not in zf.namelist():
                    raise WordFileValidationError(
                        "Le fichier .docx est incomplet ou corrompu. "
                        "Ouvrez-le dans Word et enregistrez-le à nouveau."
                    )
        except zipfile.BadZipFile as exc:
            raise WordFileValidationError(
                "Le fichier .docx est corrompu ou illisible. "
                "Ouvrez-le dans Word et enregistrez-le à nouveau."
            ) from exc
    finally:
        file_obj.seek(pos)
