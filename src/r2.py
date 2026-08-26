"""Thin R2 (S3-compatible) client: upload, get, list, delete.

Port of family-tree's src/lib/backup/r2.ts. Every credential-bearing env var
is checked explicitly and fails loudly, naming the missing variable — a
backup job that silently does nothing is worse than one that crashes.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

import boto3

log = logging.getLogger("r2")


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def r2_client():
    account = require_env("R2_ACCOUNT_ID")
    access_key = require_env("R2_ACCESS_KEY_ID")
    secret_key = require_env("R2_SECRET_ACCESS_KEY")
    return boto3.client(
        "s3",
        region_name="auto",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _bucket() -> str:
    return require_env("R2_BUCKET")


@dataclass(frozen=True)
class R2Object:
    key: str
    last_modified: datetime


def upload_to_r2(key: str, body: bytes) -> None:
    log.info("uploading %s (%d bytes)", key, len(body))
    r2_client().put_object(Bucket=_bucket(), Key=key, Body=body)


def get_from_r2(key: str) -> bytes:
    res = r2_client().get_object(Bucket=_bucket(), Key=key)
    return res["Body"].read()


def list_r2(prefix: str) -> list[R2Object]:
    client = r2_client()
    bucket = _bucket()
    objects: list[R2Object] = []
    continuation_token: str | None = None
    while True:
        kwargs: dict = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        res = client.list_objects_v2(**kwargs)
        for obj in res.get("Contents", []):
            objects.append(R2Object(key=obj["Key"], last_modified=obj["LastModified"]))
        if res.get("IsTruncated"):
            continuation_token = res.get("NextContinuationToken")
            if not continuation_token:
                # IsTruncated true but no token to page with -- can't make
                # forward progress. Re-listing page one forever would hang
                # the nightly job, so stop here instead.
                break
        else:
            break
    return objects


def delete_r2(key: str) -> None:
    r2_client().delete_object(Bucket=_bucket(), Key=key)
