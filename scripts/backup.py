import datetime,os,shutil,sqlite3
from pathlib import Path
source=Path(os.getenv("SQLITE_PATH","instance/stockbridge.db"))
target=Path(os.getenv("BACKUP_DIR","backups"));target.mkdir(parents=True,exist_ok=True)
if not source.exists():raise SystemExit(f"Database not found: {source}")
name=target/f"stockbridge-{datetime.datetime.now():%Y%m%d-%H%M%S}.db"
with sqlite3.connect(source) as src,sqlite3.connect(name) as dst:src.backup(dst)
print(f"Backup created: {name}")
