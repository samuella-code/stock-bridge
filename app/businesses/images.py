"""Validated logos stored outside SQL. Private reads always go through authorization."""
import io
import os
import re
import warnings
from pathlib import Path
from uuid import uuid4
from typing import Protocol
from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError


class StorageError(Exception):
    pass


class ImageStorage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


def storage_configured():
    config = current_app.config
    if config['BUSINESS_IMAGE_STORAGE'] == 'local':
        return not os.getenv('VERCEL') and os.getenv('FLASK_ENV') != 'production'
    return config['BUSINESS_IMAGE_STORAGE'] == 's3' and bool(config['BUSINESS_IMAGE_S3_BUCKET'])


def validate_image(upload):
    extensions = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'webp': 'WEBP'}
    extension = (upload.filename or '').rsplit('.', 1)[-1].lower()
    if (extension not in extensions or upload.mimetype not in ('image/jpeg', 'image/png', 'image/webp')
            or upload.mimetype != {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}.get(extensions.get(extension))):
        raise ValueError('Choose a JPEG, PNG or WebP image.')
    data = upload.stream.read(current_app.config['BUSINESS_LOGO_MAX_BYTES'] + 1)
    if not data or len(data) > current_app.config['BUSINESS_LOGO_MAX_BYTES']:
        raise ValueError('Choose an image no larger than 5 MB (5 MiB).')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=['JPEG', 'PNG', 'WEBP']) as image:
                if image.format != extensions[extension] or image.width * image.height > 12_000_000 or getattr(image, 'n_frames', 1) != 1:
                    raise ValueError
                image.verify()
            with Image.open(io.BytesIO(data), formats=['JPEG', 'PNG', 'WEBP']) as image:
                image.load()
                clean = ImageOps.exif_transpose(image).convert('RGBA')
                clean.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                # Re-encode pixels only: discard filename, EXIF, profiles and appended payloads.
                output = io.BytesIO()
                clean.save(output, format='WEBP', quality=85, method=6)
                if output.tell() > 500 * 1024:
                    output = io.BytesIO()
                    clean.save(output, format='WEBP', quality=75, method=6)
        return output.getvalue()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('This image could not be read. Choose a valid, non-animated JPEG, PNG or WebP under 12 megapixels.') from None


def checked_key(key):
    if not re.fullmatch(r'businesses/[1-9][0-9]*/[a-f0-9]{32}\.webp', key or ''):
        raise StorageError('Invalid image reference')
    return key


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, key):
        path = (self.root / checked_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError('Invalid image reference')
        return path

    def put(self, key, data):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)

    def get(self, key):
        return self.path(key).read_bytes()

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self):
        import boto3
        from botocore.config import Config
        config = current_app.config
        self.bucket = config['BUSINESS_IMAGE_S3_BUCKET']
        if not self.bucket:
            raise StorageError('Image storage unavailable')
        endpoint = config.get('BUSINESS_IMAGE_S3_ENDPOINT') or None
        if endpoint and not endpoint.startswith('https://'):
            raise StorageError('Image storage requires HTTPS')
        self.client = boto3.client('s3', region_name=config['BUSINESS_IMAGE_S3_REGION'],
            endpoint_url=endpoint,
            aws_access_key_id=config.get('BUSINESS_IMAGE_S3_ACCESS_KEY_ID') or None,
            aws_secret_access_key=config.get('BUSINESS_IMAGE_S3_SECRET_ACCESS_KEY') or None,
            config=Config(connect_timeout=3, read_timeout=5, retries={'max_attempts': 1}))

    def put(self, key, data):
        self.client.put_object(Bucket=self.bucket, Key=checked_key(key), Body=data, ContentType='image/webp')

    def get(self, key):
        response = self.client.get_object(Bucket=self.bucket, Key=checked_key(key))
        with response['Body'] as stream:
            return stream.read(current_app.config['BUSINESS_LOGO_MAX_BYTES'] + 1)

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=checked_key(key))


def image_storage() -> ImageStorage:
    config = current_app.config
    backend = config['BUSINESS_IMAGE_STORAGE']
    if backend == 'local':
        if os.getenv('VERCEL') or os.getenv('FLASK_ENV') == 'production':
            raise StorageError('Local image storage is development-only')
        return LocalStorage(config['BUSINESS_IMAGE_LOCAL_DIR'] or str(Path(current_app.instance_path) / 'business-images'))
    if backend == 's3':
        return S3Storage()
    raise StorageError('Image storage unavailable')


def verify_upload(storage, key, data, business_id):
    """Read back the private optimized object before publishing its reference."""
    if not checked_key(key).startswith(f'businesses/{business_id}/'):
        raise StorageError('Image object belongs to another business')
    storage.put(key, data)
    if storage.get(key) != data:
        raise StorageError('Uploaded image verification failed')


def cleanup(storage, key, business_id):
    if key:
        try:
            valid_owner = checked_key(key).startswith(f'businesses/{business_id}/')
        except StorageError:
            valid_owner = False
        if not valid_owner:
            current_app.logger.warning('Business image cleanup refused: object ownership mismatch.')
            return False
        try:
            storage.delete(key)
        except Exception:
            # Only the validated object key is recorded for a safe cleanup retry.
            # Provider exceptions can contain URLs or credentials and are omitted.
            current_app.logger.warning('Business image cleanup deferred: business=%s object=%s.', business_id, key)
            return False
    return True


def new_key(business_id):
    return f'businesses/{business_id}/{uuid4().hex}.webp'
