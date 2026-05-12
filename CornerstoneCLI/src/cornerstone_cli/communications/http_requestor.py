from __future__ import annotations

import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Optional
from xml.etree import ElementTree as ET

from ..crypto.hash_creator import create_sha512_base64_unicode16le


DEFAULT_SERVER = "remote.lecosoftware.com"


@dataclass(frozen=True)
class ServerResponseErrorInfo:
    status_code: int
    status_description: str = ""


class ServerResponseErrorParser:
    # 与 C# 版 `ServerResponseErrorParser` 常量对齐
    ERROR_CODE_TIMEOUT = 1
    ERROR_CODE_UNKNOWN_INSTRUMENT = 5
    ERROR_CODE_EXCEPTION = 6
    ERROR_UNABLE_TO_EXECUTE_COMMAND = 7
    ERROR_CODE_FAILED_USER_VALIDATION = -1
    ERROR_UNKNOWN_ERROR = -2

    @staticmethod
    def parse_server_error(xml_text: str, expected_root_name: str) -> Optional[ServerResponseErrorInfo]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return ServerResponseErrorInfo(status_code=ServerResponseErrorParser.ERROR_UNKNOWN_ERROR, status_description=xml_text)

        def get_child_text(name: str) -> Optional[str]:
            el = root.find(name)
            return el.text if el is not None else None

        def get_status_code() -> int:
            for key in ("StatusCode", "ErrorCode"):
                txt = get_child_text(key)
                if txt is not None:
                    try:
                        return int(txt)
                    except ValueError:
                        return 0
            return 0

        status_code = get_status_code()

        if root.tag == expected_root_name:
            if status_code == 0:
                return None
            return ServerResponseErrorInfo(status_code=status_code, status_description=get_child_text("StatusDescription") or "")

        # root 非预期：兼容 USERVALIDATION 特例
        if status_code == 0 and root.tag.upper() == "USERVALIDATION":
            passed = (root.text or "").strip().lower() == "true"
            if not passed:
                return ServerResponseErrorInfo(status_code=ServerResponseErrorParser.ERROR_CODE_FAILED_USER_VALIDATION)
            return None

        if status_code == 0:
            return ServerResponseErrorInfo(status_code=ServerResponseErrorParser.ERROR_UNKNOWN_ERROR, status_description=(root.text or "").strip())
        return ServerResponseErrorInfo(status_code=status_code, status_description=get_child_text("StatusDescription") or "")

    @staticmethod
    def get_appropriate_error_message(error: Optional[ServerResponseErrorInfo]) -> str:
        if error is None:
            return ""
        code = error.status_code
        if code == ServerResponseErrorParser.ERROR_CODE_EXCEPTION:
            return error.status_description
        if code == ServerResponseErrorParser.ERROR_CODE_TIMEOUT:
            return "等待仪器响应超时，请检查网络连接。"
        if code == ServerResponseErrorParser.ERROR_CODE_UNKNOWN_INSTRUMENT:
            return "仪器当前不在线。"
        if code == ServerResponseErrorParser.ERROR_CODE_FAILED_USER_VALIDATION:
            return "仪器返回凭据无效，请确认密码正确。"
        if code == ServerResponseErrorParser.ERROR_UNABLE_TO_EXECUTE_COMMAND:
            return "无法执行远程命令，请检查 Cornerstone 远程查询授权是否过期。"
        if code == ServerResponseErrorParser.ERROR_UNKNOWN_ERROR:
            return f"与服务器通信发生未知错误。状态码: {code}。{error.status_description}"
        return f"与服务器通信发生未知错误。状态码: {code}。"


class WebRequestor:
    """
    对齐 C# `WebRequestor` 的最小 Python 实现：
    - CreateUri: 生成 https://{server}/{page}?k=v...
    - MakeRequest: POST text/xml，返回 XML 文本
    """

    def create_uri(
        self,
        page: str,
        parameters: Optional[Mapping[str, str]] = None,
        server: str = "",
    ) -> str:
        server = (server or DEFAULT_SERVER).replace("/", "")
        params = dict(parameters or {})
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        return f"https://{server}/{page}{query}"

    def create_uri_for_instrument(
        self,
        page: str,
        instrument_id: str,
        parameters: Optional[Mapping[str, str]] = None,
        server: str = "",
    ) -> str:
        params = dict(parameters or {})
        params.setdefault("Id", instrument_id)
        return self.create_uri(page=page, parameters=params, server=server)

    def create_uri_legacy_with_data(
        self,
        page: str,
        instrument_id: str,
        data: str,
        parameters: Optional[Mapping[str, str]] = None,
        server: str = "",
    ) -> str:
        params = dict(parameters or {})
        params.setdefault("Id", instrument_id)
        params.setdefault("data", data)
        return self.create_uri(page=page, parameters=params, server=server)

    def make_request(self, uri: str, post_content: str) -> str:
        data = None if not post_content.strip() else post_content.encode("utf-8")
        req = urllib.request.Request(uri, data=data, method="POST")
        if data is not None:
            req.add_header("Content-Type", "text/xml")

        # 使用系统默认 CA；显式创建 context 以便未来可扩展
        context = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, context=context, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            # 对齐 C#：网络不可用时返回包含 StatusCode/StatusDescription 的 XML
            root = ET.Element("root")
            ET.SubElement(root, "StatusCode").text = str(ServerResponseErrorParser.ERROR_CODE_EXCEPTION)
            ET.SubElement(root, "StatusDescription").text = "A network connection is not available."
            return ET.tostring(root, encoding="unicode")


def build_user_lab_info_xml(user: str, password: str, labname: str, labkey: str) -> str:
    """
    对齐 C# `ConnectionViewModel.XmlFormattedUserAndLabInfo()`：
    - pwd 是 SHA512(UTF-16LE) 后的 base64
    """
    root = ET.Element("UserLabInfo")
    ET.SubElement(root, "user").text = user or ""
    ET.SubElement(root, "pwd").text = create_sha512_base64_unicode16le(password or "")
    ET.SubElement(root, "labname").text = labname or ""
    ET.SubElement(root, "labkey").text = labkey or ""
    return ET.tostring(root, encoding="unicode")


def build_post_data(user_lab_info_xml: str, command_xml: str = "") -> str:
    """
    对齐 C# `ConnectionViewModel.GeneratePostData()`：
    - 无 command: 仅返回 UserLabInfo
    - 有 command: <UserLabInfo> 下追加 <Command> 节点并包含 command root
    """
    if not command_xml:
        return user_lab_info_xml
    root = ET.fromstring(user_lab_info_xml)
    cmd_container = ET.SubElement(root, "Command")
    cmd_container.append(ET.fromstring(command_xml))
    return ET.tostring(root, encoding="unicode")
