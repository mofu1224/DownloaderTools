import json
import subprocess
import sys


def main():
    # Job Objectへの割当が完了するまでネットワーク・外部ツールを起動しない。
    command = json.loads(sys.argv[1])
    if sys.stdin.readline().strip() != "GO":
        return 1
    return subprocess.call(command, stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
