import json
import os
import signal
import subprocess
import sys
import threading


def main():
    # Job Objectへの割当が完了するまでネットワーク・外部ツールを起動しない。
    command = json.loads(sys.argv[1])
    if sys.stdin.readline().strip() != "GO":
        return 1
    if sys.platform == "darwin":
        if os.getpgrp() != os.getpid():
            return 1

        def watch_host():
            # 専用セッションの親が異常終了したとき、パイプのEOFで子孫も終了する。
            if not os.read(0, 1):
                os.killpg(os.getpid(), signal.SIGKILL)

        threading.Thread(target=watch_host, daemon=True).start()
    return subprocess.call(command, stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
