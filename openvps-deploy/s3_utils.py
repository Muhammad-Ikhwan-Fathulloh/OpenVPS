import boto3
import logging
import concurrent.futures
from botocore.exceptions import ClientError
from pathlib import Path
import config
import mimetypes

logger = logging.getLogger(__name__)

class S3Storage:
    def __init__(self):
        self.enabled = bool(config.S3_BUCKET_NAME and config.S3_ACCESS_KEY and config.S3_SECRET_KEY)
        if self.enabled:
            # Menggunakan endpoint kustom misalnya untuk Cloudeka
            self.s3_client = boto3.client(
                's3',
                endpoint_url=config.S3_ENDPOINT if config.S3_ENDPOINT else None,
                region_name=config.S3_REGION if config.S3_REGION else None,
                aws_access_key_id=config.S3_ACCESS_KEY,
                aws_secret_access_key=config.S3_SECRET_KEY
            )
            self.bucket_name = config.S3_BUCKET_NAME
        else:
            self.s3_client = None
            self.bucket_name = None

    def upload_file(self, file_path: str | Path, object_name: str = None) -> bool:
        """Kirim file dari disk ke S3 Bucket. Berguna untuk sinkronisasi async."""
        if not self.enabled:
            return False

        if object_name is None:
            object_name = str(file_path)

        file_path_str = str(file_path)
        content_type, _ = mimetypes.guess_type(file_path_str)
        if content_type is None:
            content_type = 'application/octet-stream'

        try:
            self.s3_client.upload_file(
                file_path_str, 
                self.bucket_name, 
                object_name,
                ExtraArgs={'ContentType': content_type}
            )
            return True
        except ClientError as e:
            logger.error(f"Gagal mengunggah {file_path_str} ke S3: {e}")
            return False

    def upload_fileobj(self, file_obj, object_name: str, content_type: str = 'application/octet-stream') -> bool:
        """Kirim buffer memori langsung ke S3 Bucket."""
        if not self.enabled:
            return False
            
        try:
            self.s3_client.upload_fileobj(
                file_obj, 
                self.bucket_name, 
                object_name,
                ExtraArgs={'ContentType': content_type}
            )
            return True
        except ClientError as e:
            logger.error(f"Gagal mengunggah objek {object_name} ke S3: {e}")
            return False

    def upload_fileobj_batch(self, items: list[tuple], max_workers: int = 5) -> int:
        """
        Kirim multiple buffer memori secara bersamaan ke S3 Bucket menggunakan thread pool.
        items: list of tuple (file_obj, object_name, content_type)
        Mencegah pemblokiran aplikasi pada upload masal dan menstabilkan I/O.
        """
        if not self.enabled:
            return 0
            
        n_saved = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self.upload_fileobj, f_obj, obj_name, ctype)
                for (f_obj, obj_name, ctype) in items
            ]
            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    n_saved += 1
        return n_saved
            
    def get_object_url(self, object_name: str) -> str:
        """Mengembalikan Public URL untuk bucket asalkan bucket S3 diatur untuk public read."""
        if not self.enabled:
            return f"/{object_name}"
            
        endpoint = config.S3_ENDPOINT.rstrip('/')
        return f"{endpoint}/{self.bucket_name}/{object_name}"

storage = S3Storage()
