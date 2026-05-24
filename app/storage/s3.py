from collections.abc import AsyncIterator
from typing import BinaryIO

import aioboto3

from app.config import settings
from app.storage.base import StorageBackend


class S3Storage(StorageBackend):
    def __init__(self) -> None:
        self.session = aioboto3.Session()
        self.bucket = settings.s3_bucket_name
        self.client_kwargs: dict = {
            "service_name": "s3",
            "region_name": settings.s3_region or None,
            "aws_access_key_id": settings.s3_access_key_id,
            "aws_secret_access_key": settings.s3_secret_access_key,
        }
        if settings.s3_endpoint_url:
            self.client_kwargs["endpoint_url"] = settings.s3_endpoint_url

    async def upload(self, key: str, data: BinaryIO, content_type: str) -> str:
        async with self.session.client(**self.client_kwargs) as s3:
            await s3.upload_fileobj(
                data,
                self.bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
        return key

    async def download_stream(self, key: str, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        async with self.session.client(**self.client_kwargs) as s3:
            response = await s3.get_object(Bucket=self.bucket, Key=key)
            stream = response["Body"]
            async for chunk in stream.iter_chunks(chunk_size):
                yield chunk

    async def delete(self, key: str) -> None:
        async with self.session.client(**self.client_kwargs) as s3:
            await s3.delete_object(Bucket=self.bucket, Key=key)

    async def exists(self, key: str) -> bool:
        async with self.session.client(**self.client_kwargs) as s3:
            try:
                await s3.head_object(Bucket=self.bucket, Key=key)
                return True
            except s3.exceptions.ClientError:
                return False

    async def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        async with self.session.client(**self.client_kwargs) as s3:
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            return url
