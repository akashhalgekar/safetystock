"""Single place that knows how to reach the database."""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def _params():
    return dict(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "safetystock"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
    )


def get_conn():
    """psycopg2 connection, for writes and DDL."""
    return psycopg2.connect(**_params())


def get_engine():
    """SQLAlchemy engine, for pandas reads."""
    from sqlalchemy import create_engine
    p = _params()
    return create_engine(
        f"postgresql+psycopg2://{p['user']}:{p['password']}"
        f"@{p['host']}:{p['port']}/{p['dbname']}"
    )
