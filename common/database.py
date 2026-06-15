"""
Database layer: PostgreSQL (warrants, audit) + Redis (active LIIDs cache).
"""
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras
import redis

from common.models import Warrant, TargetIdentifier, TargetIDType, InterceptType

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  PostgreSQL                                                                   #
# --------------------------------------------------------------------------- #

class PostgresDB:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._conn: Optional[psycopg2.extensions.connection] = None

    def connect(self):
        self._conn = psycopg2.connect(self._dsn)
        self._conn.autocommit = False
        self._create_tables()
        logger.info("PostgreSQL connected")

    def disconnect(self):
        if self._conn:
            self._conn.close()

    @contextmanager
    def cursor(self):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    def _create_tables(self):
        with self.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS warrants (
                    liid            TEXT PRIMARY KEY,
                    lea_id          TEXT NOT NULL,
                    target_id_type  TEXT NOT NULL,
                    target_value    TEXT NOT NULL,
                    intercept_type  TEXT NOT NULL,
                    delivery_address TEXT NOT NULL,
                    valid_from      TIMESTAMPTZ NOT NULL,
                    valid_until     TIMESTAMPTZ NOT NULL,
                    active          BOOLEAN DEFAULT TRUE,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS iri_records (
                    id              SERIAL PRIMARY KEY,
                    liid            TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL,
                    event_type      TEXT NOT NULL,
                    timestamp       TIMESTAMPTZ NOT NULL,
                    payload_json    JSONB,
                    delivered_hi2   BOOLEAN DEFAULT FALSE,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS cc_records (
                    id              SERIAL PRIMARY KEY,
                    liid            TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL,
                    timestamp       TIMESTAMPTZ NOT NULL,
                    direction       TEXT NOT NULL,
                    src_ip          TEXT,
                    dst_ip          TEXT,
                    payload_size    INTEGER,
                    delivered_hi3   BOOLEAN DEFAULT FALSE,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_warrants_active ON warrants(active);
                CREATE INDEX IF NOT EXISTS idx_iri_liid ON iri_records(liid);
                CREATE INDEX IF NOT EXISTS idx_cc_liid ON cc_records(liid);
            """)

    # ---- Warrants ----

    def save_warrant(self, warrant: Warrant):
        with self.cursor() as cur:
            cur.execute("""
                INSERT INTO warrants
                    (liid, lea_id, target_id_type, target_value, intercept_type,
                     delivery_address, valid_from, valid_until, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (liid) DO UPDATE SET
                    active = EXCLUDED.active,
                    valid_until = EXCLUDED.valid_until
            """, (
                warrant.liid, warrant.lea_id,
                warrant.target.id_type.value, warrant.target.value,
                warrant.intercept_type.value, warrant.delivery_address,
                warrant.valid_from, warrant.valid_until, warrant.active
            ))

    def get_warrant(self, liid: str) -> Optional[Warrant]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM warrants WHERE liid = %s", (liid,))
            row = cur.fetchone()
        if not row:
            return None
        return Warrant(
            liid=row["liid"],
            lea_id=row["lea_id"],
            target=TargetIdentifier(TargetIDType(row["target_id_type"]), row["target_value"]),
            intercept_type=InterceptType(row["intercept_type"]),
            delivery_address=row["delivery_address"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            active=row["active"],
            created_at=row["created_at"],
        )

    def deactivate_warrant(self, liid: str):
        with self.cursor() as cur:
            cur.execute("UPDATE warrants SET active = FALSE WHERE liid = %s", (liid,))

    def get_active_warrants(self) -> list[Warrant]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM warrants WHERE active = TRUE AND valid_until > NOW()")
            rows = cur.fetchall()
        return [
            Warrant(
                liid=r["liid"], lea_id=r["lea_id"],
                target=TargetIdentifier(TargetIDType(r["target_id_type"]), r["target_value"]),
                intercept_type=InterceptType(r["intercept_type"]),
                delivery_address=r["delivery_address"],
                valid_from=r["valid_from"], valid_until=r["valid_until"],
                active=r["active"], created_at=r["created_at"],
            )
            for r in rows
        ]


# --------------------------------------------------------------------------- #
#  Redis                                                                        #
# --------------------------------------------------------------------------- #

class RedisCache:
    """
    Fast LIID lookup cache. Keys: target value → LIID list.
    Used by IRI-MF and CC-MF to check if a packet belongs to an active intercept.
    """
    LIID_PREFIX = "liid:"
    TARGET_PREFIX = "target:"

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        self._client = redis.Redis(host=host, port=port, db=db, decode_responses=True)

    def ping(self):
        return self._client.ping()

    def add_active_liid(self, liid: str, target_value: str, ttl_seconds: int = 86400):
        """Index target → LIID for fast lookup during packet processing."""
        key = f"{self.TARGET_PREFIX}{target_value}"
        self._client.sadd(key, liid)
        self._client.expire(key, ttl_seconds)
        # Also store LIID metadata
        self._client.hset(f"{self.LIID_PREFIX}{liid}", mapping={
            "target": target_value,
            "active": "1",
        })
        self._client.expire(f"{self.LIID_PREFIX}{liid}", ttl_seconds)

    def remove_liid(self, liid: str, target_value: str):
        self._client.srem(f"{self.TARGET_PREFIX}{target_value}", liid)
        self._client.delete(f"{self.LIID_PREFIX}{liid}")

    def get_liids_for_target(self, target_value: str) -> set[str]:
        return self._client.smembers(f"{self.TARGET_PREFIX}{target_value}")

    def is_liid_active(self, liid: str) -> bool:
        return self._client.hget(f"{self.LIID_PREFIX}{liid}", "active") == "1"
