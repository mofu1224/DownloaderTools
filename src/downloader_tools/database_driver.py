"""macOSの組み込みSQLiteを使わず、更新を固定したDB-APIを選択する。"""

import sys

if sys.platform == "darwin":
    from pysqlite3 import dbapi2 as sqlite3
else:
    import sqlite3

__all__ = ["sqlite3"]
