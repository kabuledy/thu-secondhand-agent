"""
图片存储后端 — 支持本地文件系统和腾讯云 COS

用法：
  开发环境：无需额外配置，默认存本地 data/uploads/
  生产环境：设置以下环境变量即可切换到 COS

    变量名            必填  说明
    ────────────────────────────────────────────
    COS_BUCKET         ✅   COS 存储桶名称，如 thu-secondhand-images
    COS_REGION         ✅   存储桶地域，如 ap-guangzhou
    COS_SECRET_ID      ✅   API 密钥 ID（在 API 密钥管理页面获取）
    COS_SECRET_KEY     ✅   API 密钥 Key
    ────────────────────────────────────────────

  设置了 COS_BUCKET + COS_REGION 后自动启用 COS 存储。
"""

import os
import io
from typing import BinaryIO


# ═══════════════════════════════════════════════════════════
# 抽象基类
# ═══════════════════════════════════════════════════════════

class StorageBackend:
    """图片存储后端抽象接口"""

    def save(self, data: bytes, filename: str, content_type: str = "image/jpeg") -> str:
        """
        保存图片，返回可公开访问的 URL。
        参数：
            data:        图片二进制数据
            filename:    保存的文件名（含扩展名）
            content_type: MIME 类型
        返回：
            图片的公开访问 URL
        """
        raise NotImplementedError

    def delete(self, filename: str) -> bool:
        """删除已保存的图片"""
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════
# 本地文件系统（开发环境）
# ═══════════════════════════════════════════════════════════

class LocalStorage(StorageBackend):
    """本地文件系统存储，适合开发环境"""

    def __init__(self):
        # uploads 目录
        default_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "uploads"
        )
        self.upload_dir = os.environ.get("UPLOAD_DIR", default_dir)
        os.makedirs(self.upload_dir, exist_ok=True)

        # 公网访问地址前缀（通过 Flask /uploads/ 路由提供）
        self.public_base = os.environ.get(
            "PUBLIC_BASE",
            "http://localhost:5000/uploads"
        )

    def save(self, data: bytes, filename: str, content_type: str = "image/jpeg") -> str:
        filepath = os.path.join(self.upload_dir, filename)
        with open(filepath, "wb") as f:
            f.write(data)
        return self.get_public_url(filename)

    def delete(self, filename: str) -> bool:
        filepath = os.path.join(self.upload_dir, filename)
        try:
            os.remove(filepath)
            return True
        except FileNotFoundError:
            return False

    def get_upload_dir(self) -> str:
        """返回本地存储目录路径（仅用于 Flask 路由）"""
        return self.upload_dir

    def get_public_url(self, filename: str) -> str:
        return f"{self.public_base}/{filename}"


# ═══════════════════════════════════════════════════════════
# 腾讯云 COS（生产环境）
# ═══════════════════════════════════════════════════════════

class CosStorage(StorageBackend):
    """腾讯云对象存储 COS，适合生产环境"""

    def __init__(self):
        from qcloud_cos import CosConfig, CosS3Client

        self.bucket = os.environ["COS_BUCKET"]
        self.region = os.environ["COS_REGION"]
        secret_id = os.environ.get("COS_SECRET_ID", "")
        secret_key = os.environ.get("COS_SECRET_KEY", "")

        config = CosConfig(
            Region=self.region,
            SecretId=secret_id,
            SecretKey=secret_key,
            Scheme="https",
        )
        self.client = CosS3Client(config)

    def save(self, data: bytes, filename: str, content_type: str = "image/jpeg") -> str:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Body=data,
                Key=filename,
                ContentType=content_type,
            )
        except Exception as e:
            raise RuntimeError(f"COS 上传失败: {e}")

        return self.get_public_url(filename)

    def delete(self, filename: str) -> bool:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=filename)
            return True
        except Exception:
            return False

    def get_public_url(self, filename: str) -> str:
        return f"https://{self.bucket}.cos.{self.region}.myqcloud.com/{filename}"


# ═══════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════

_STORAGE_INSTANCE = None


def get_storage() -> StorageBackend:
    """
    根据环境变量自动选择合适的存储后端。

    规则：
      - 设置了 COS_BUCKET + COS_REGION → 使用 CosStorage
      - 否则 → 使用 LocalStorage
    """
    global _STORAGE_INSTANCE
    if _STORAGE_INSTANCE is not None:
        return _STORAGE_INSTANCE

    # 生产环境：腾讯云 COS
    if os.environ.get("COS_BUCKET") and os.environ.get("COS_REGION"):
        _STORAGE_INSTANCE = CosStorage()
    else:
        # 开发环境：本地文件系统
        _STORAGE_INSTANCE = LocalStorage()

    return _STORAGE_INSTANCE


def reset_storage():
    """重置存储后端（仅用于测试/热切换）"""
    global _STORAGE_INSTANCE
    _STORAGE_INSTANCE = None
