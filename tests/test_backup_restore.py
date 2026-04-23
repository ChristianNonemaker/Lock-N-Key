from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def test_sqlite_backup_restore_round_trip() -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is required for backup/restore script smoke test")
    bash_probe = subprocess.run([bash, "-lc", "true"], text=True, capture_output=True)
    if bash_probe.returncode != 0:
        pytest.skip(f"bash is not usable in this environment: {bash_probe.stderr}")

    repo_root = Path(__file__).resolve().parents[1]
    backup_script = repo_root / "scripts" / "backup_sqlite_to_gcs.sh"
    restore_script = repo_root / "scripts" / "restore_sqlite_backup.sh"
    temp_root = repo_root / ".test_tmp"
    temp_root.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(dir=temp_root) as raw_tmp:
        tmp_path = Path(raw_tmp)
        source_db = tmp_path / "source.sqlite3"
        with sqlite3.connect(source_db) as conn:
            conn.execute("CREATE TABLE smoke (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO smoke (value) VALUES (?)", ("restored",))

        backup_dir = tmp_path / "backups"
        env = os.environ.copy()
        env["PYTHON_CMD"] = sys.executable

        subprocess.run(
            [
                bash,
                str(backup_script),
                "--db-path",
                str(source_db),
                "--backup-dir",
                str(backup_dir),
                "--dry-run",
            ],
            cwd=repo_root,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )

        archives = sorted(backup_dir.glob("sqlite_backup_*.tar.gz"))
        assert archives, "dry-run backup should still create a local archive"

        restored_db = tmp_path / "restored.sqlite3"
        subprocess.run(
            [
                bash,
                str(restore_script),
                "--archive",
                str(archives[-1]),
                "--db-path",
                str(restored_db),
            ],
            cwd=repo_root,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )

        with sqlite3.connect(restored_db) as conn:
            assert conn.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert conn.execute("SELECT value FROM smoke").fetchone() == ("restored",)

        refusal = subprocess.run(
            [
                bash,
                str(restore_script),
                "--archive",
                str(archives[-1]),
                "--db-path",
                str(restored_db),
            ],
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
        )
        assert refusal.returncode != 0
        assert "Refusing to overwrite" in refusal.stderr
