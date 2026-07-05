"""
图片处理工具 — 下载、保存、替换 URL

架构（2026-07-03 更新）：
  原来：直接读写本地文件系统
  现在：通过 StorageBackend 抽象层，开发环境存本地，生产环境存腾讯云 COS

流程：
  用户发图 → detect 多模态消息 → 下载图片 → storage.save() → 返回公网 URL + base64 data URL

说明：
  - public_url:  文件在服务器/云上的地址（用于生产环境）
  - base64_url:  图片的 base64 data URL（内嵌在 JSON 中，测试/开发环境可靠显示）
  两者同时返回，list_item 自动使用 base64_url 存储，确保查图片时永远能显示。
"""

import os
import uuid
import base64
import requests
from typing import Optional

from .storage_backend import get_storage


# ── 文件扩展名推断 ──────────────────────────────────

# HTTP Content-Type 到文件扩展名的映射
EXT_MAP = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
}


def _infer_ext(content_type: str, image_url: str = "") -> str:
    """从 Content-Type 或 URL 推断扩展名"""
    # 1. 优先 Content-Type
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in EXT_MAP:
            return EXT_MAP[ct]

    # 2. 从 URL 文件名推断
    if image_url:
        path = image_url.split("?")[0].split("/")[-1]
        if "." in path:
            ext = path.rsplit(".", 1)[-1].lower()
            if ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
                return ext if ext != "jpeg" else "jpg"

    # 3. 兜底
    return "jpg"


def _make_base64_url(data: bytes, content_type: str = "image/jpeg") -> str:
    """将二进制图片数据转为 base64 data URL"""
    b64_str = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{b64_str}"


# ═══════════════════════════════════════════════════════════
# 下载并保存图片
# ═══════════════════════════════════════════════════════════

def download_and_save_image(image_url: str) -> dict:
    """
    从 URL 下载图片并通过存储后端保存。

    参数：
        image_url: 图片 URL（http/https 或 data:image 格式）

    返回：
        {
            "success": true,
            "public_url": "https://...",   # 服务器上的地址（生产环境用）
            "base64_url": "data:image/...", # base64 data URL（内嵌显示用）
            "filename": "xxx.jpg"
        }
        或 {"success": false, "error": "..."}
    """
    # ── base64 data URL → 解码后再处理 ──
    if image_url.startswith("data:image/"):
        return _process_data_url(image_url)

    # ── 普通 HTTP URL ──
    try:
        resp = requests.get(image_url, timeout=30, headers={
            "User-Agent": "THU-SecondHand-Agent/1.0",
        })
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        ext = _infer_ext(content_type, image_url)
        filename = f"{uuid.uuid4().hex}.{ext}"

        # 生成 base64 data URL（用于内嵌存储）
        base64_url = _make_base64_url(resp.content, content_type)

        # 通过存储后端保存到文件/云
        storage = get_storage()
        public_url = storage.save(resp.content, filename, content_type)

        return {
            "success": True,
            "public_url": public_url,
            "base64_url": base64_url,
            "filename": filename,
        }

    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"下载图片失败: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": f"保存图片失败: {str(e)}"}


def _process_data_url(data_url: str) -> dict:
    """
    处理 data:image 格式的 base64 图片 URL。
    解码后存到本地/云，同时返回 base64 data URL。
    """
    try:
        # 格式：data:image/png;base64,xxxxx
        header, encoded = data_url.split(",", 1)

        # 从 header 推断类型
        ct = header.replace("data:", "").split(";")[0]
        ext = _infer_ext(ct)
        filename = f"{uuid.uuid4().hex}.{ext}"

        decoded = base64.b64decode(encoded)

        # 生成 base64 data URL
        base64_url = _make_base64_url(decoded, ct)

        # 通过存储后端保存
        storage = get_storage()
        public_url = storage.save(decoded, filename, ct)

        return {
            "success": True,
            "public_url": public_url,
            "base64_url": base64_url,
            "filename": filename,
        }

    except Exception as e:
        return {"success": False, "error": f"解析 base64 图片失败: {str(e)}"}


# ═══════════════════════════════════════════════════════════
# 消息中的图片处理
# ═══════════════════════════════════════════════════════════

def process_images_in_messages(messages: list) -> list:
    """
    扫描消息列表中的多模态图片，下载并替换为永久 URL。

    参数：
        messages: OpenAI 格式的消息列表（会被原地修改）

    返回：
        图片处理结果列表，每个元素含 public_url 和 base64_url
    """
    results = []

    for msg in messages:
        if msg.get("role") != "user":
            continue

        content = msg.get("content")
        # 只处理多模态格式（content 为 list 的情况）
        if not isinstance(content, list):
            continue

        for part in content:
            if part.get("type") == "image_url":
                url = part.get("image_url", {}).get("url", "")
                if not url:
                    continue

                # 下载并保存图片
                save_result = download_and_save_image(url)
                if save_result["success"]:
                    # 替换为永久 URL（给 DeepSeek 视觉模型用）
                    part["image_url"]["url"] = save_result["public_url"]
                    results.append({
                        "original_url": url,
                        "public_url": save_result["public_url"],
                        "base64_url": save_result.get("base64_url", ""),
                        "success": True,
                    })
                else:
                    results.append({
                        "original_url": url,
                        "public_url": url,
                        "base64_url": "",
                        "success": False,
                        "error": save_result.get("error", ""),
                    })

    return results
