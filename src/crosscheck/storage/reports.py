import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC
from pathlib import Path

from crosscheck.domain.models import VerificationReport


class ReportRepository:
    """Store completed report snapshots; never store provider credentials."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A connection per operation is safe across FastAPI worker threads.
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS reports (
                        request_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        claim_text TEXT NOT NULL,
                        conclusion TEXT NOT NULL,
                        evidence_count INTEGER NOT NULL,
                        report_json TEXT NOT NULL
                    )
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS reports_created_at
                    ON reports(created_at DESC, request_id DESC)
                """)
                yield connection

    def save(self, report: VerificationReport) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO NOTHING""",
                (
                    report.request_id,
                    report.created_at.astimezone(UTC).isoformat(),
                    report.claim.text,
                    report.conclusion.value,
                    len(report.evidence),
                    report.model_dump_json(),
                ),
            )

    def list_reports(self, limit: int, offset: int) -> dict:
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            rows = connection.execute(
                """SELECT request_id, created_at, claim_text, conclusion, evidence_count
                FROM reports ORDER BY created_at DESC, request_id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def get(self, request_id: str) -> VerificationReport | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM reports WHERE request_id = ?", (request_id,)
            ).fetchone()
        return VerificationReport.model_validate_json(row[0]) if row else None

    def delete(self, request_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM reports WHERE request_id = ?", (request_id,)
            )
            return cursor.rowcount > 0
