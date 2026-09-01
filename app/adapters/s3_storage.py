from __future__ import annotations


class S3StatementStorage:
    """APP_ENV=aws statement store. Local Docker Compose keeps using the filesystem."""

    def __init__(self, bucket: str, *, client=None, prefix: str = "statements") -> None:
        if not bucket:
            raise ValueError("statements_bucket is required when APP_ENV=aws")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def save(self, name: str, data: bytes) -> str:
        key = self._key(name)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ServerSideEncryption="AES256",
        )
        return self._uri(key)

    def load(self, name: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(name))
        return response["Body"].read()

    def path_for(self, name: str) -> str:
        return self._uri(self._key(name))

    def _key(self, name: str) -> str:
        safe = name.replace("\\", "/").lstrip("/")
        return f"{self.prefix}/{safe}"

    def _uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"
