"""File storage: Azure Blob Storage when configured (needed on Container Apps - local disk is wiped on restart),
otherwise a local folder for development."""
import asyncio
import datetime
import logging
import os

from ..config import settings

log = logging.getLogger("storage")
_container = None


def _blob():
    global _container
    if _container is None:
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(settings.AZURE_STORAGE_CONNECTION_STRING)
        _container = svc.get_container_client(settings.AZURE_STORAGE_CONTAINER)
        try:
            _container.create_container()
        except Exception:
            pass        # already exists
    return _container


def using_blob() -> bool:
    return bool(settings.AZURE_STORAGE_CONNECTION_STRING)


def _local(path: str) -> str:
    p = os.path.normpath(os.path.join(settings.UPLOAD_DIR, path))
    if not p.startswith(os.path.normpath(settings.UPLOAD_DIR)):
        raise ValueError("bad path")
    return p


def put_file(path: str, src_file: str, content_type: str = "application/octet-stream"):
    if using_blob():
        from azure.storage.blob import ContentSettings
        with open(src_file, "rb") as f:
            _blob().upload_blob(path, f, overwrite=True, content_settings=ContentSettings(content_type=content_type))
    else:
        dst = _local(path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src_file, "rb") as s, open(dst, "wb") as d:
            while chunk := s.read(1 << 20):
                d.write(chunk)


def put_bytes(path: str, data: bytes, content_type: str = "application/octet-stream"):
    if using_blob():
        from azure.storage.blob import ContentSettings
        _blob().upload_blob(path, data, overwrite=True, content_settings=ContentSettings(content_type=content_type))
    else:
        dst = _local(path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)


def get_bytes(path: str) -> bytes:
    if using_blob():
        return _blob().download_blob(path).readall()
    with open(_local(path), "rb") as f:
        return f.read()


def delete(path: str):
    try:
        if using_blob():
            _blob().delete_blob(path)
        else:
            os.remove(_local(path))
    except Exception:
        pass


def local_path(path: str) -> str | None:
    return None if using_blob() else _local(path)


def signed_url(path: str, minutes: int = 30) -> str | None:
    """Short-lived direct download URL (Blob SAS). None for local storage - the API streams the file instead."""
    if not using_blob():
        return None
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas
    c = _blob()
    sas = generate_blob_sas(account_name=c.account_name, container_name=c.container_name, blob_name=path,
                            account_key=c.credential.account_key, permission=BlobSasPermissions(read=True),
                            expiry=datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes))
    return f"{c.url}/{path}?{sas}"


async def aput_file(path: str, src_file: str, content_type: str):
    await asyncio.to_thread(put_file, path, src_file, content_type)
