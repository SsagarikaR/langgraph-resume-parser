import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

from app.core.config import get_settings
from app.graph.state import config_for
from app.utils.files import safe_filename, write_private

UPLOAD_KINDS = ("resume", "jd")

# The app's own index of screenings, in the checkpoint DB: which exist, who owns them, when they
# were created. LangGraph's tables are its internals, so the app never queries them directly.
REGISTRY_TABLE = """CREATE TABLE IF NOT EXISTS screenings (
    thread_id TEXT PRIMARY KEY,
    owner TEXT,  -- caller that created it (key name, or key/user); NULL = CLI or open API, which only those see
    created_at TEXT NOT NULL,
    summary TEXT,  -- JSON of what GET /screenings lists, saved when a run ends; NULL = read the state
    last_active TEXT  -- when a run last started or ended: what the retention purge reads
)"""


class ScreeningRepository:
    """Data access for screenings.

    Workflow state lives in the LangGraph checkpointer (checkpoints.db), next to the app's
    screenings table; uploaded files and exported reports live on disk. Nothing else in the app
    touches any of them directly. Running the workflow is WorkflowRunner's job, not this one's.

    `graph` returns the compiled graph, whose state API (pending tasks, interrupts) and
    checkpointer this reads and writes. It is passed in, not imported, so storage does not depend
    on how the workflow is built.
    """

    def __init__(
        self,
        graph: Callable[[], CompiledStateGraph],
        uploads_dir: Path | None = None,
        reports_dir: Path | None = None,
    ):
        self._graph = graph
        self.uploads_dir = uploads_dir or get_settings().uploads_dir
        self.reports_dir = reports_dir or get_settings().reports_dir
        self._registry_lock = threading.Lock()
        self._registry_ready: SqliteSaver | None = None  # the checkpointer whose DB has the table

    def get_state(self, thread_id: str) -> StateSnapshot:
        return self._graph().get_state(config_for(thread_id))

    def update_state(self, thread_id: str, values: dict, as_node: str) -> None:
        """Write values into a paused thread as if `as_node` had produced them."""
        self._graph().update_state(config_for(thread_id), values, as_node=as_node)

    def register(self, thread_id: str, owner: str | None) -> None:
        """Record a new screening and the API key name that owns it (None = shared)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._registry().cursor() as cur:
            cur.execute(
                "INSERT INTO screenings (thread_id, owner, created_at, last_active) VALUES (?, ?, ?, ?)",
                (thread_id, owner, now, now),
            )

    def list_screenings(self, owner: str | None, limit: int) -> list[tuple[str, str, dict | None]]:
        """(thread_id, created_at, summary) of the newest screenings, newest first: those `owner`
        started, or every screening when `owner` is None (no access control)."""
        columns = "SELECT thread_id, created_at, summary FROM screenings"
        with self._registry().cursor(transaction=False) as cur:
            if owner is None:
                cur.execute(f"{columns} ORDER BY created_at DESC LIMIT ?", (limit,))
            else:
                cur.execute(f"{columns} WHERE owner = ? ORDER BY created_at DESC LIMIT ?", (owner, limit))
            return [(row[0], row[1], json.loads(row[2]) if row[2] else None) for row in cur.fetchall()]

    def save_summary(self, thread_id: str, summary: dict | None) -> None:
        """Store what GET /screenings lists for the screening; None = unknown (a run in progress).
        Every run calls this when it starts and ends, so it also records the screening's activity."""
        with self._registry().cursor() as cur:
            cur.execute(
                "UPDATE screenings SET summary = ?, last_active = ? WHERE thread_id = ?",
                (
                    json.dumps(summary) if summary is not None else None,
                    datetime.now(timezone.utc).isoformat(),
                    thread_id,
                ),
            )

    def owner_of(self, thread_id: str) -> str | None:
        """The API key name that owns the screening; None if it is shared or not registered."""
        with self._registry().cursor(transaction=False) as cur:
            cur.execute("SELECT owner FROM screenings WHERE thread_id = ?", (thread_id,))
            row = cur.fetchone()
        return row[0] if row else None

    def ping(self) -> None:
        """Raise if the checkpoint database cannot be read."""
        with self._registry().cursor(transaction=False) as cur:
            cur.execute("SELECT 1 FROM screenings LIMIT 1")

    def upload_path(self, thread_id: str, kind: str, suffix: str) -> Path:
        """Where save_upload puts the file: data/uploads/<thread_id>_<kind><suffix>."""
        return self.uploads_dir / f"{thread_id}_{kind}{suffix}"

    def save_upload(self, thread_id: str, kind: str, data: bytes, suffix: str) -> Path:
        """Save an uploaded file at upload_path, readable by the app only."""
        self.uploads_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.upload_path(thread_id, kind, suffix)
        write_private(path, data)
        return path

    def save_report(self, thread_id: str, report: dict) -> Path:
        """Write the report as reports/<thread_id>.json, readable by the app only. Named by thread id:
        unique, traceable to the screening, and no candidate name on disk or in logs."""
        self.reports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._report_path(thread_id)
        write_private(path, json.dumps(report, indent=2).encode())
        return path

    def delete(self, thread_id: str) -> None:
        """Erase a screening: its workflow state (resume text included), uploads and report."""
        self._checkpointer().delete_thread(thread_id)
        with self._registry().cursor() as cur:
            cur.execute("DELETE FROM screenings WHERE thread_id = ?", (thread_id,))
        # The checkpointer uses a write-ahead log, which keeps copies of the erased pages (resume
        # text) until they are checkpointed into the database, where secure_delete has zeroed them.
        # Checkpoint now and truncate the log, so the text does not linger in checkpoints.db-wal.
        with self._registry().cursor(transaction=False) as cur:
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # Exact names, not a glob: thread "a" must not match the files of thread "a_b"
        for kind in UPLOAD_KINDS:
            self.upload_path(thread_id, kind, ".pdf").unlink(missing_ok=True)
        self._report_path(thread_id).unlink(missing_ok=True)

    def inactive_since(self, cutoff: datetime) -> list[str]:
        """Thread ids of screenings with no activity since `cutoff` (timezone-aware). One query on
        the registry: no screening's workflow state is loaded."""
        with self._registry().cursor(transaction=False) as cur:
            cur.execute("SELECT thread_id, last_active FROM screenings")
            rows = cur.fetchall()
        return [thread_id for thread_id, last_active in rows if datetime.fromisoformat(last_active) < cutoff]

    def purge_stray_files(self, cutoff: datetime) -> None:
        """Delete uploads and reports older than `cutoff` whose screening is gone or never started,
        e.g. an upload from a run rejected before its PDF was parsed."""
        live = self._thread_ids()
        keep = {
            self.uploads_dir: {f"{t}_{kind}.pdf" for t in live for kind in UPLOAD_KINDS},
            self.reports_dir: {self._report_path(t).name for t in live},
        }
        for directory, owned in keep.items():
            for path in directory.glob("*") if directory.is_dir() else []:
                if path.name in owned or not path.is_file():
                    continue
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                    path.unlink(missing_ok=True)

    def _report_path(self, thread_id: str) -> Path:
        return self.reports_dir / f"{safe_filename(thread_id)}.json"

    @staticmethod
    def _backfill_last_active(checkpointer: SqliteSaver) -> None:
        """Once, for screenings registered before last_active existed: their newest checkpoint
        (or, with none, their creation) is when they were last active."""
        with checkpointer.cursor(transaction=False) as cur:
            cur.execute("SELECT thread_id, created_at FROM screenings WHERE last_active IS NULL")
            rows = cur.fetchall()
        if not rows:
            return
        updates = []
        for thread_id, created_at in rows:
            latest = checkpointer.get_tuple(config_for(thread_id))
            updates.append((latest.checkpoint["ts"] if latest and latest.checkpoint else created_at, thread_id))
        with checkpointer.cursor() as cur:
            cur.executemany("UPDATE screenings SET last_active = ? WHERE thread_id = ?", updates)

    def _thread_ids(self) -> set[str]:
        """Every registered screening."""
        with self._registry().cursor(transaction=False) as cur:
            cur.execute("SELECT thread_id FROM screenings")
            return {row[0] for row in cur.fetchall()}

    def _checkpointer(self) -> SqliteSaver:
        return cast(SqliteSaver, self._graph().checkpointer)

    def _registry(self) -> SqliteSaver:
        """The checkpointer, once the screenings table exists in its DB.

        When the table is new, screenings saved before it existed are found once through the
        checkpointer's public list() and registered as shared. A table from before summaries
        gains the column (NULL: those screenings are read from their state), and one from before
        last_active gains that column, filled in once from each screening's newest checkpoint.
        """
        checkpointer = self._checkpointer()
        with self._registry_lock:
            if self._registry_ready is not checkpointer:
                with checkpointer.cursor() as cur:
                    cur.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'screenings'")
                    is_new = cur.fetchone() is None
                    cur.execute(REGISTRY_TABLE)
                    cur.execute("PRAGMA table_info(screenings)")
                    columns = {row[1] for row in cur.fetchall()}
                    if "summary" not in columns:
                        cur.execute("ALTER TABLE screenings ADD COLUMN summary TEXT")
                    if "last_active" not in columns:
                        cur.execute("ALTER TABLE screenings ADD COLUMN last_active TEXT")
                if is_new:
                    # Outside the cursor: list() takes the checkpointer's lock itself
                    legacy = {c.config["configurable"]["thread_id"] for c in checkpointer.list(None)}
                    now = datetime.now(timezone.utc).isoformat()
                    with checkpointer.cursor() as cur:
                        cur.executemany(
                            "INSERT OR IGNORE INTO screenings (thread_id, owner, created_at) VALUES (?, NULL, ?)",
                            [(thread_id, now) for thread_id in legacy],
                        )
                self._backfill_last_active(checkpointer)
                self._registry_ready = checkpointer
        return checkpointer
