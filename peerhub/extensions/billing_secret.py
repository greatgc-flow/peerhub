"""Private, atomic key storage beside a billing observation database."""
from __future__ import annotations

import ctypes
import os
import secrets
import stat
import sys
from pathlib import Path


def _private_directory(path: Path, *, create: bool) -> None:
    if create:
        try:
            if os.name == "nt" and sys.version_info < (3, 13):
                _windows_private_directory(path)
            else:
                path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError("billing key directory is not a private directory")
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise OSError("billing key directory is not private")


def _windows_private_directory(path: Path) -> None:
    # Python before 3.13 ignores mkdir's mode on Windows. Use a protected,
    # inheritable DACL for OWNER RIGHTS, SYSTEM and Administrators.
    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("descriptor", ctypes.c_void_p), ("inherit", ctypes.c_int)]

    load = getattr(ctypes, "WinDLL")
    advapi = load("advapi32", use_last_error=True)
    kernel = load("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    if not convert("D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)", 1, ctypes.byref(descriptor), None):
        raise OSError("cannot secure the billing key directory")
    try:
        create = kernel.CreateDirectoryW
        create.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(SecurityAttributes)]
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, 0)
        if not create(str(path), ctypes.byref(attributes)):
            if getattr(ctypes, "get_last_error")() == 183:
                raise FileExistsError
            raise OSError("cannot create the private billing key directory")
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(descriptor)


def store_secret(db_path: str | Path, *, create: bool) -> bytes:
    """Read the private key. Callers serialize creation with the store's write lock."""
    db = Path(db_path).resolve(strict=True)
    directory = db.with_name(f".{db.name}.billing")
    _private_directory(directory, create=create)
    key_path = directory / "key"
    if create and not key_path.exists():
        pending = directory / "key.pending"
        pending.unlink(missing_ok=True)  # at most one interrupted publication per store
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(secrets.token_bytes(32))
                out.flush()
                os.fsync(out.fileno())
            os.replace(pending, key_path)  # publish a complete key
            if os.name != "nt":
                dir_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            pending.unlink(missing_ok=True)
    info = key_path.lstat()
    if (not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
        raise OSError("billing key file is not private")
    with key_path.open("rb") as stream:
        key = stream.read(33)
    if len(key) != 32:
        raise OSError("invalid billing key file")
    return key
