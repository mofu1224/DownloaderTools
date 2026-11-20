import json
import os
import stat
import sys
from pathlib import Path


def root_path() -> Path:
    return Path(__file__).resolve().parents[2]


def contained(root: Path, path: Path) -> Path:
    root = root.resolve()
    raw = path if path.is_absolute() else root / path
    if not raw.resolve().is_relative_to(root):
        raise ValueError("PATH_OUTSIDE_ROOT")
    current = raw
    while current != root:
        if current.exists() and (current.is_symlink() or current.is_junction()):
            raise ValueError("PATH_LINK_FORBIDDEN")
        if current == current.parent:
            raise ValueError("PATH_OUTSIDE_ROOT")
        current = current.parent
    return raw


def atomic_json(path: Path, value: object):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import ntsecuritycon
        import win32api
        import win32security

        sid = win32security.GetTokenInformation(
            win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY),
            win32security.TokenUser,
        )[0]
        acl = win32security.ACL()
        acl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
            ntsecuritycon.FILE_ALL_ACCESS,
            sid,
        )
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            acl,
            None,
        )
    else:
        path.chmod(stat.S_IRWXU)
