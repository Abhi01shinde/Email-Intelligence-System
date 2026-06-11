from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "email_intelligence.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")

# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)

# SQLite connect_args
connect_args = {"check_same_thread": False, "timeout": 30} if "sqlite" in DATABASE_URL else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from models import Email, Commitment, Task, DecisionLog, SenderProfile, CognitiveLoadSnapshot  # noqa
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()
    print("Database initialized")


def _ensure_sqlite_columns():
    if "sqlite" not in DATABASE_URL:
        return

    table_columns = {
        "emails": {"account_email": "TEXT"},
        "commitments": {"account_email": "TEXT"},
        "tasks": {"account_email": "TEXT"},
        "decision_logs": {"account_email": "TEXT"},
        "sender_profiles": {"account_email": "TEXT", "sender_email": "TEXT"},
        "cognitive_load_snapshots": {"account_email": "TEXT"},
    }

    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        for table_name, columns in table_columns.items():
            existing = {
                row[1]
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
            }
            for column_name, column_type in columns.items():
                if column_name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                    )
        if _emails_table_uses_legacy_unique_gmail_id(conn):
            _migrate_emails_table(conn)


def _emails_table_uses_legacy_unique_gmail_id(conn) -> bool:
    indexes = conn.exec_driver_sql("PRAGMA index_list(emails)").fetchall()
    for row in indexes:
        index_name = row[1]
        is_unique = bool(row[2])
        if not is_unique:
            continue
        columns = [info[2] for info in conn.exec_driver_sql(f"PRAGMA index_info({index_name})").fetchall()]
        if columns == ["gmail_id"]:
            return True
    return False


def _migrate_emails_table(conn):
    conn.exec_driver_sql("""
        CREATE TABLE emails_new (
            id INTEGER NOT NULL PRIMARY KEY,
            account_email TEXT,
            gmail_id VARCHAR,
            sender VARCHAR,
            sender_email VARCHAR,
            recipient VARCHAR,
            subject VARCHAR,
            body TEXT,
            body_snippet VARCHAR,
            classification VARCHAR,
            priority_score INTEGER,
            deadline VARCHAR,
            commitment_phrases JSON,
            action_items JSON,
            mood VARCHAR,
            stress_score INTEGER,
            suggested_action VARCHAR,
            ai_summary TEXT,
            ai_reply_draft TEXT,
            ai_reasoning TEXT,
            language VARCHAR,
            is_read BOOLEAN,
            is_replied BOOLEAN,
            is_deferred BOOLEAN,
            label_applied VARCHAR,
            processed_at DATETIME,
            received_at DATETIME,
            CONSTRAINT uq_emails_account_gmail_id UNIQUE (account_email, gmail_id)
        )
    """)
    conn.exec_driver_sql("""
        INSERT INTO emails_new (
            id, account_email, gmail_id, sender, sender_email, recipient, subject, body,
            body_snippet, classification, priority_score, deadline, commitment_phrases,
            action_items, mood, stress_score, suggested_action, ai_summary, ai_reply_draft,
            ai_reasoning, language, is_read, is_replied, is_deferred, label_applied,
            processed_at, received_at
        )
        SELECT
            id, account_email, gmail_id, sender, sender_email, recipient, subject, body,
            body_snippet, classification, priority_score, deadline, commitment_phrases,
            action_items, mood, stress_score, suggested_action, ai_summary, ai_reply_draft,
            ai_reasoning, language, is_read, is_replied, is_deferred, label_applied,
            processed_at, received_at
        FROM emails
    """)
    conn.exec_driver_sql("DROP TABLE emails")
    conn.exec_driver_sql("ALTER TABLE emails_new RENAME TO emails")
    conn.exec_driver_sql("CREATE INDEX ix_emails_id ON emails (id)")
    conn.exec_driver_sql("CREATE INDEX ix_emails_account_email ON emails (account_email)")
    conn.exec_driver_sql("CREATE INDEX ix_emails_gmail_id ON emails (gmail_id)")
