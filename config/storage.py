"""Configuration du stockage S3 (AWS, Cloudflare R2, Scaleway, MinIO…) depuis l'environnement."""


def s3_storage(env):
    from botocore.config import Config

    style = env.get('S3_ADDRESSING_STYLE')
    return {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'bucket_name': env['S3_BUCKET'],
            'access_key': env.get('S3_ACCESS_KEY') or None,
            'secret_key': env.get('S3_SECRET_KEY') or None,
            'region_name': env.get('S3_REGION') or None,
            'endpoint_url': env.get('S3_ENDPOINT_URL') or None,
            'location': env.get('S3_PREFIX') or 'media',
            # Fichiers privés : aucun ACL public, accès par URL signée d'une heure.
            'default_acl': None,
            'querystring_auth': True,
            'querystring_expire': 3600,
            'file_overwrite': False,
            'client_config': Config(
                signature_version='s3v4',
                s3={'addressing_style': style} if style else None,
                # Les sommes de contrôle systématiques de boto3 ne sont pas acceptées par tous
                # les services compatibles S3.
                request_checksum_calculation='when_required',
                response_checksum_validation='when_required',
                connect_timeout=10, read_timeout=30, retries={'max_attempts': 3},
            ),
        },
    }
