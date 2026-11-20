import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from .diagnostics import activity_changed, append_log, process_activity
from .engines import parse_line
from .paths import atomic_json


def main(config_path: str):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    receipt_root = Path(config["receipts"])
    receipt_root.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    started = threading.Event()
    messages: queue.Queue = queue.Queue(256)
    identity = {key: config[key] for key in ("source_id", "attempt_id", "run_token")}
    sequence = 0
    failures = 0
    items = 0
    acknowledged = 0
    backlog = False
    rate_limited = False
    protocol_error = False
    logs_truncated = 0
    began = time.monotonic()
    pending_times: dict[int, float] = {}
    pending_lock = threading.Lock()
    log_path = Path(config["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    last_activity = time.monotonic()
    stalled = False

    def log(component, text):
        nonlocal logs_truncated
        if not append_log(log_path, component, text):
            logs_truncated += 1

    log("host", f"attempt_start attempt={config['attempt_id']}")

    def notify(event, critical=False):
        event = {**identity, **event}
        try:
            messages.put(event, timeout=5 if critical else 0)
        except queue.Full:
            if critical:
                stop.set()

    def send():
        nonlocal sequence
        while True:
            event = messages.get()
            try:
                if event is None:
                    return
                sequence += 1
                print(json.dumps({**event, "seq": sequence}, ensure_ascii=True), flush=True)
            except OSError, ValueError:
                stop.set()
                return
            finally:
                messages.task_done()

    def control():
        nonlocal acknowledged
        try:
            for line in sys.stdin:
                message = json.loads(line)
                if message.get("action") == "start":
                    started.set()
                elif message.get("action") == "stop":
                    log("host", "stop_requested by parent")
                    stop.set()
                elif message.get("action") == "ack":
                    acknowledged = max(acknowledged, min(items, int(message["receipt_id"])))
                    with pending_lock:
                        for identity in list(pending_times):
                            if identity <= acknowledged:
                                del pending_times[identity]
        except ValueError, OSError:
            pass
        finally:
            log("host", "control_pipe_closed")
            stop.set()

    sender = threading.Thread(target=send, daemon=True)
    sender.start()
    threading.Thread(target=control, daemon=True).start()
    owner = {
        **identity,
        "pid": os.getpid(),
        "created": psutil.Process().create_time(),
        "executable": psutil.Process().exe(),
        "config": config_path,
    }
    atomic_json(Path(config["owner_path"]), owner)
    notify({"type": "ready", "owner": owner}, True)
    deadline = time.monotonic() + 10
    while not started.is_set() and not stop.is_set() and time.monotonic() < deadline:
        stop.wait(0.05)
    if not started.is_set() or stop.is_set():
        atomic_json(
            receipt_root / "exit.json",
            {
                **identity,
                "exit_code": 1,
                "stopped": True,
                "failures": 0,
                "counts_complete": False,
                "items": 0,
                "descendants_stopped": True,
            },
        )
        messages.put(None)
        sender.join(1)
        return 0
    job: Any = None
    if sys.platform == "win32":
        import win32api
        import win32job

        job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    gate = Path(__file__).with_name("engine_gate.py")
    process = subprocess.Popen(
        [sys.executable, str(gate), json.dumps(config["command"])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=sys.platform != "win32",
        bufsize=0,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        if job:
            handle = win32api.OpenProcess(0x0100 | 0x0200 | 0x0001, False, process.pid)
            try:
                win32job.AssignProcessToJobObject(job, handle)
            finally:
                win32api.CloseHandle(handle)
        atomic_json(
            Path(config["owner_path"]),
            {
                **owner,
                "engine_pid": process.pid,
                "engine_created": psutil.Process(process.pid).create_time(),
            },
        )
        assert process.stdin
        process.stdin.write(b"GO\n")
        process.stdin.flush()
        if sys.platform != "darwin":
            process.stdin.close()
        notify({"type": "started"}, True)
        log("host", f"engine_started pid={process.pid}")

        def read_stream(stream, is_stdout):
            nonlocal items, failures, rate_limited, protocol_error, backlog, last_activity
            progress_logged = 0.0
            with stream:
                while chunk := stream.readline(65537):
                    last_activity = time.monotonic()
                    line = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
                    if len(chunk) > 65536 and line.startswith("DT_"):
                        protocol_error = True
                        failures += 1
                        stop.set()
                        continue
                    try:
                        event = parse_line(line) if is_stdout else None
                    except ValueError, TypeError:
                        failures += 1
                        protocol_error = True
                        stop.set()
                        continue
                    if event:
                        if event["type"] == "item_ready":
                            items += 1
                            event["receipt_id"] = items
                            with pending_lock:
                                pending_times[items] = time.monotonic()
                            atomic_json(
                                receipt_root / f"item-{items:08d}.json", {**identity, **event}
                            )
                            if items - acknowledged >= 1000:
                                backlog = True
                                stop.set()
                            log(
                                "host", f"item_ready receipt={items} pending={items - acknowledged}"
                            )
                        elif time.monotonic() - progress_logged >= 5:
                            log("progress", json.dumps(event, ensure_ascii=False))
                            progress_logged = time.monotonic()
                        notify(event, event["type"] == "item_ready")
                    else:
                        # 失敗の判定は終了コードが正本。429は起動抑制にのみ使用する。
                        if "429" in line and ("HTTP" in line or "Too Many Requests" in line):
                            rate_limited = True
                        log("stdout" if is_stdout else "stderr", line)

        def drain(stream, is_stdout):
            nonlocal failures, protocol_error
            try:
                read_stream(stream, is_stdout)
            except OSError, ValueError:
                failures += 1
                protocol_error = True
                stop.set()

        readers = [
            threading.Thread(target=drain, args=(process.stdout, True), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, False), daemon=True),
        ]
        for reader in readers:
            reader.start()
        previous_activity = process_activity(process.pid)
        monitor_interval = config.get("monitor_interval", 1)
        idle_timeout = config.get("idle_timeout", 300)
        heartbeat = time.monotonic()
        while process.poll() is None and not stop.wait(monitor_interval):
            now = time.monotonic()
            current_activity = process_activity(process.pid)
            if activity_changed(previous_activity, current_activity):
                last_activity = now
            previous_activity = current_activity
            if now - last_activity >= idle_timeout:
                stalled = True
                log("host", f"ENGINE_STALLED idle_seconds={now - last_activity:.1f}")
                stop.set()
            if now - heartbeat >= 15:
                log(
                    "host",
                    f"heartbeat elapsed={now - began:.1f}s idle={now - last_activity:.1f}s items={items} pending={items - acknowledged}",
                )
                heartbeat = now
            with pending_lock:
                if pending_times and time.monotonic() - next(iter(pending_times.values())) > 300:
                    backlog = True
                    log("host", "publish_backlog: receipt acknowledgement timed out")
                    stop.set()
        stopped = stop.is_set()
        if stopped and process.poll() is None:
            if sys.platform == "win32":
                win32job.TerminateJobObject(job, 1)
            else:
                os.kill(-process.pid, signal.SIGTERM)
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    os.kill(-process.pid, signal.Signals(9))
        code = process.wait(timeout=10)
        # 正常終了時にも残存した孫プロセスを終了してから枠を返す。
        if job:
            win32job.TerminateJobObject(job, 1)
        elif not stopped:
            try:
                os.kill(-process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for reader in readers:
            reader.join(2)
        if any(reader.is_alive() for reader in readers):
            protocol_error = True
            failures += 1
        descendants_stopped = False
        deadline = time.monotonic() + 5
        if job:
            while time.monotonic() < deadline:
                accounting = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectBasicAccountingInformation
                )
                if not accounting["ActiveProcesses"]:
                    descendants_stopped = True
                    break
                time.sleep(0.05)
        else:
            while time.monotonic() < deadline:
                try:
                    os.kill(-process.pid, 0)
                except ProcessLookupError:
                    descendants_stopped = True
                    break
                time.sleep(0.05)
            if not descendants_stopped:
                try:
                    os.kill(-process.pid, signal.Signals(9))
                except ProcessLookupError:
                    descendants_stopped = True
        result = {
            **identity,
            "exit_code": code,
            "stopped": stopped,
            "stalled": stalled,
            "failures": failures,
            "counts_complete": code == 0 and not stopped and not protocol_error and items > 0,
            "items": items,
            "rate_limited": rate_limited,
            "protocol_error": protocol_error,
            "backlog": backlog,
            "logs_truncated": logs_truncated,
            "elapsed_seconds": time.monotonic() - began,
            "descendants_stopped": descendants_stopped,
        }
        atomic_json(receipt_root / "exit.json", result)
        log(
            "host",
            "engine_exit "
            + json.dumps({key: value for key, value in result.items() if key not in identity}),
        )
        notify({"type": "engine_exit", **result}, True)
    finally:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if job:
            win32api.CloseHandle(job)
        elif process.poll() is None:
            try:
                os.kill(-process.pid, signal.Signals(9))
            except ProcessLookupError:
                pass
        messages.put(None, timeout=1)
        sender.join(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
