import re
from datetime import datetime
from pathlib import Path

import portalocker
import psutil


def redact(line: str) -> str:
    line = re.sub(r"https?://[^\s\"<>]+", "[URL非表示]", line)
    line = re.sub(
        r"(?i)(cookie|authorization|token|password|secret)[\"']?\s*[=: ].*", r"\1=[非表示]", line
    )
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line)[:2048]


def append_log(path: Path, component: str, message: str) -> bool:
    text = "".join(
        f"{datetime.now().astimezone().isoformat(timespec='seconds')} [{component}] {redact(line)}\n"
        for line in str(message).splitlines()
    ).encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(path.parent / ".quota.lock"), timeout=1):
            own = path.stat().st_size if path.exists() else 0
            total = sum(p.stat().st_size for p in path.parent.glob("*.log"))
            if own + len(text) > 5 * 1024**2 or total + len(text) > 100 * 1024**2:
                return False
            with path.open("ab") as log:
                log.write(text)
        return True
    except OSError, portalocker.LockException:
        return False


def process_activity(pid: int) -> dict | None:
    try:
        parent = psutil.Process(pid)
        processes = [parent, *parent.children(recursive=True)]
    except psutil.Error:
        return None
    activity = {}
    for process in processes:
        try:
            cpu = process.cpu_times()
            io = process.io_counters()
            activity[(process.pid, process.create_time())] = (
                cpu.user + cpu.system,
                io.read_bytes,
                io.write_bytes,
            )
        except psutil.NoSuchProcess:
            continue
        except psutil.Error, AttributeError:
            # 監視権限がない場合、正常な無出力処理を停止しない。
            return None
    return activity


def activity_changed(previous: dict | None, current: dict | None) -> bool:
    if previous is None or current is None or previous.keys() != current.keys():
        return True
    return any(
        values[1:] != previous[key][1:] or values[0] > previous[key][0]
        for key, values in current.items()
    )
