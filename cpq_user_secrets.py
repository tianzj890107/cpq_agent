# -*- coding: utf-8 -*-
"""账号级密钥的加密封装（AEAD）。

账号级模型与 API Key 现在与账号主数据一起落在 `cpq_wf`（Postgres）里，**一律
密文**：`cpq_wf_user_llm_setting.model_cipher / keys_cipher`。密文自包含 nonce 与
认证标签，格式固定：

    v1:<b64 nonce>:<b64 ciphertext+tag>        （两段都是标准 base64）

密钥材料来自环境变量 `CPQ_USER_SECRET_KEY`（base64 或 hex，解出来必须 32 字节）。
**每次调用都重新读环境变量**：容器里换密钥、临时摘掉密钥都能立刻生效，也不会把
密钥材料缓存进进程内存后忘掉。

失败一律「响亮」：未配置 / 长度不对 / 解不开 → `SecretKeyMissing`。绝不静默改成明文
落库，也绝不把解不开的密文原样当明文返回 —— 那等于把加密做成摆设。
"""
from __future__ import annotations

import base64
import binascii
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV_VAR = "CPQ_USER_SECRET_KEY"
VERSION = "v1"
NONCE_BYTES = 12          # AES-GCM 的标准 nonce 长度
KEY_BYTES = 32            # AES-256


class SecretKeyMissing(RuntimeError):
    """没有可用的密钥材料（或密钥材料不合法）。不回落明文，直接失败。"""


def _decode_key(raw: str):
    text = str(raw or "").strip()
    if not text:
        return None
    if len(text) == KEY_BYTES * 2:                 # 纯 hex 优先（64 个十六进制字符）
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        pass
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def _key() -> bytes:
    raw = os.environ.get(ENV_VAR)
    material = _decode_key(raw)
    if material is None or len(material) != KEY_BYTES:
        raise SecretKeyMissing(
            f"{ENV_VAR} 未配置或不是 {KEY_BYTES} 字节的 base64/hex 材料："
            "账号级模型与密钥必须加密落库，拒绝以明文写入。")
    return material


def seal(plain: str) -> str:
    """明文 -> `v1:<b64 nonce>:<b64 ciphertext+tag>`。每次都用新的随机 nonce。"""
    key = _key()
    nonce = os.urandom(NONCE_BYTES)
    box = AESGCM(key).encrypt(nonce, str(plain).encode("utf-8"), None)
    return "%s:%s:%s" % (VERSION,
                         base64.b64encode(nonce).decode("ascii"),
                         base64.b64encode(box).decode("ascii"))


def open(sealed: str) -> str:                      # noqa: A001 - 与 Spec 的函数名一致
    """密文 -> 明文。非本模块的密文、被改动过的密文一律抛错。"""
    key = _key()
    parts = str(sealed or "").strip().split(":")
    if len(parts) != 3 or parts[0] != VERSION:
        raise ValueError("不是本模块产出的密文（缺少 v1 版本前缀）")
    try:
        nonce = base64.b64decode(parts[1], validate=True)
        box = base64.b64decode(parts[2], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("密文不是合法的 base64") from exc
    if len(nonce) != NONCE_BYTES or not box:
        raise ValueError("密文结构不合法")
    try:
        return AESGCM(key).decrypt(nonce, box, None).decode("utf-8")
    except Exception as exc:                       # AEAD 校验失败 / 密钥不对
        raise ValueError("密文校验失败（被改动，或密钥材料与写入时不一致）") from exc
