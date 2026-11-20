import json
import time
from pathlib import Path

from .paths import private_directory
from .store import Store, uid


def normalize_cookie(content: str) -> tuple[str, list[str]]:
    if len(content.encode()) > 256 * 1024:
        raise ValueError("COOKIE_TOO_LARGE")
    domains: set[str] = set()
    lines = ["# Netscape HTTP Cookie File"]
    if content.lstrip().startswith(("[", "{")):
        data = json.loads(content)
        cookies = data.get("cookies") if isinstance(data, dict) else data
        if not isinstance(cookies, list) or not cookies:
            raise ValueError("COOKIE_INVALID")
        for cookie in cookies:
            domain, name, value = cookie["domain"], cookie["name"], cookie["value"]
            path = cookie.get("path", "/")
            if any(
                not isinstance(item, str) or any(c in item for c in "\r\n\t")
                for item in (domain, name, value, path)
            ):
                raise ValueError("COOKIE_INVALID")
            if not domain or not name or "/" in domain or " " in domain:
                raise ValueError("COOKIE_INVALID")
            domains.add(domain.lstrip("."))
            expiry = max(0, int(cookie.get("expirationDate", cookie.get("expires", 0)) or 0))
            lines.append(
                "\t".join(
                    (
                        domain,
                        "TRUE" if domain.startswith(".") else "FALSE",
                        path,
                        "TRUE" if cookie.get("secure") else "FALSE",
                        str(expiry),
                        name,
                        value,
                    )
                )
            )
    else:
        for line in content.splitlines():
            if not line.strip() or (line.startswith("#") and not line.startswith("#HttpOnly_")):
                continue
            fields = line.split("\t")
            if len(fields) != 7 or not fields[0] or not fields[5]:
                raise ValueError("COOKIE_INVALID")
            if fields[1] not in ("TRUE", "FALSE") or fields[3] not in ("TRUE", "FALSE"):
                raise ValueError("COOKIE_INVALID")
            int(fields[4] or 0)
            domains.add(fields[0].removeprefix("#HttpOnly_").lstrip("."))
            lines.append(line)
    if not domains:
        raise ValueError("COOKIE_EMPTY")
    return "\n".join(lines) + "\n", sorted(domains)


class Credentials:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, root

    async def create(self, name: str, content: str):
        suffix = ".txt"
        try:
            data = json.loads(content) if content.lstrip().startswith("{") else None
            if isinstance(data, dict) and data.get("type") == "pixiv-oauth":
                text = data["refresh_token"]
                if (
                    not isinstance(text, str)
                    or not 1 <= len(text) <= 4096
                    or not text.isascii()
                    or not text.isprintable()
                    or any(c.isspace() for c in text)
                ):
                    raise ValueError("CREDENTIAL_INVALID")
                domains, suffix = ["www.pixiv.net"], ".pixiv-token"
            else:
                text, domains = normalize_cookie(content)
        except KeyError, TypeError, OverflowError, AttributeError, ValueError:
            raise ValueError("COOKIE_INVALID") from None
        folder = self.root / "data/credentials"
        private_directory(folder)
        identity = uid()
        path = folder / (identity + suffix)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        try:
            await self.store.write(
                lambda c: c.execute(
                    "INSERT INTO credentials VALUES(?,?,1,?,?,?)",
                    (
                        identity,
                        name,
                        str(path.relative_to(self.root)),
                        json.dumps(domains),
                        time.time(),
                    ),
                )
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"id": identity, "name": name, "version": 1, "domains": domains}

    async def list(self):
        return [
            {**row, "domains": json.loads(row["domains"])}
            for row in await self.store.read(
                "SELECT id,name,version,domains,created_at FROM credentials WHERE path!='' ORDER BY created_at DESC"
            )
        ]

    async def delete(self, identity):
        def remove(conn):
            if conn.execute(
                "SELECT 1 FROM sources WHERE credential_id=? AND state NOT IN "
                "('succeeded','partial','failed','cancelled')",
                (identity,),
            ).fetchone():
                raise ValueError("CREDENTIAL_IN_USE")
            row = conn.execute("SELECT path FROM credentials WHERE id=?", (identity,)).fetchone()
            if not row or not row[0]:
                raise ValueError("CREDENTIAL_NOT_FOUND")
            conn.execute("UPDATE credentials SET path='' WHERE id=?", (identity,))
            return row[0]

        path = await self.store.write(remove)
        (self.root / path).unlink(missing_ok=True)
