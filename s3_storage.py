"""
S3 image storage utility.

Folder structure on S3:
    {S3_BASE_PREFIX}upload/    ← user-uploaded original images
    {S3_BASE_PREFIX}generated/ ← Gemini-generated repair images

File naming: YYYYMMDD_{uuid4}.{ext}
"""
import os
import uuid
from datetime import datetime

import boto3
from botocore.config import Config as BotoConfig

_BUCKET = os.environ.get("S3_BUCKET_NAME", "")
_PREFIX = os.environ.get("S3_BASE_PREFIX", "")
_CDN_URL = os.environ.get("S3_CDN_URL", "")
_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _get_client():
    """Create S3 client. Uses explicit keys (dev) or instance profile (prod)."""
    key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")

    if key_id and secret:
        return boto3.client(
            "s3",
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            region_name=_REGION,
        )
    # EC2 Instance Profile (production)
    return boto3.client("s3", region_name=_REGION, config=BotoConfig(signature_version="s3v4"))


def _generate_key(folder: str, ext: str = ".png") -> str:
    """Generate S3 key: {prefix}{folder}/YYYYMMDD_{uuid}.{ext}"""
    date_str = datetime.now().strftime("%Y%m%d")
    unique_id = uuid.uuid4().hex[:12]
    filename = f"{date_str}_{unique_id}{ext}"
    return f"{_PREFIX}{folder}/{filename}"


def upload_image(image_bytes: bytes, folder: str, ext: str = ".png", content_type: str = "image/png") -> str:
    """
    Upload image bytes to S3 and return the CDN URL.

    Args:
        image_bytes: Raw image data
        folder: "upload" or "generated"
        ext: File extension (e.g. ".png", ".jpg")
        content_type: MIME type

    Returns:
        CDN URL string (e.g. https://cdn.../prefix/upload/20260311_abc123.png)
    """
    key = _generate_key(folder, ext)
    client = _get_client()
    client.put_object(
        Bucket=_BUCKET,
        Key=key,
        Body=image_bytes,
        ContentType=content_type,
    )
    return f"{_CDN_URL}/{key}"


def upload_original(image_bytes: bytes, ext: str = ".png") -> str:
    """Upload user-uploaded original image. Returns CDN URL."""
    content_type = "image/jpeg" if ext.lower() in (".jpg", ".jpeg") else "image/png"
    return upload_image(image_bytes, "upload", ext, content_type)


def upload_generated(image_bytes: bytes, ext: str = ".png") -> str:
    """Upload Gemini-generated repair image. Returns CDN URL."""
    return upload_image(image_bytes, "generated", ext, "image/png")
