from __future__ import annotations

import base64
import hashlib


def create_sha512_base64_unicode16le(data: str) -> str:
    """
    等价于 C#:
        SHA512.Create().ComputeHash(Encoding.Unicode.GetBytes(data))
        Convert.ToBase64String(hash)

    .NET 的 Encoding.Unicode = UTF-16LE。
    """
    digest = hashlib.sha512(data.encode("utf-16-le")).digest()
    return base64.b64encode(digest).decode("ascii")
