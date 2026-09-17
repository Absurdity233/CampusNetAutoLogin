from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    password: str


def _advapi32() -> ctypes.WinDLL:
    if not hasattr(ctypes, "WinDLL"):
        raise OSError("Windows 凭据管理器仅支持 Windows")
    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    library.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    library.CredWriteW.restype = wintypes.BOOL
    library.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(PCREDENTIALW),
    ]
    library.CredReadW.restype = wintypes.BOOL
    library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    library.CredDeleteW.restype = wintypes.BOOL
    library.CredFree.argtypes = [wintypes.LPVOID]
    library.CredFree.restype = None
    return library


def write_credentials(target: str, username: str, password: str) -> None:
    password_bytes = password.encode("utf-16-le")
    password_buffer = (ctypes.c_ubyte * len(password_bytes)).from_buffer_copy(password_bytes)
    credential = CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = "校园网自动登录凭据"
    credential.CredentialBlobSize = len(password_bytes)
    credential.CredentialBlob = ctypes.cast(password_buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = username

    if not _advapi32().CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def read_credentials(target: str) -> Credentials | None:
    credential_pointer = PCREDENTIALW()
    library = _advapi32()
    if not library.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(credential_pointer)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise ctypes.WinError(error)

    try:
        credential = credential_pointer.contents
        username = credential.UserName or ""
        if credential.CredentialBlobSize:
            password_bytes = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            password = password_bytes.decode("utf-16-le")
        else:
            password = ""
        return Credentials(username=username, password=password)
    finally:
        library.CredFree(credential_pointer)


def delete_credentials(target: str) -> bool:
    if _advapi32().CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == ERROR_NOT_FOUND:
        return False
    raise ctypes.WinError(error)
