"""
Centralized settings for LIS platform.
Override via environment variables or a .env file.
"""
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # PostgreSQL
    postgres_dsn: str = field(
        default_factory=lambda: os.getenv(
            "POSTGRES_DSN",
            "postgresql://lis:lis_secret@localhost:5432/lis_db"
        )
    )

    # Redis
    redis_host: str = field(default_factory=lambda: os.getenv("REDIS_HOST", "localhost"))
    redis_port: int = field(default_factory=lambda: int(os.getenv("REDIS_PORT", "6379")))

    # Kafka
    kafka_brokers: str = field(
        default_factory=lambda: os.getenv("KAFKA_BROKERS", "localhost:9092")
    )

    # Network Element X1 endpoints
    # In production, these come from a provisioning database
    ne_endpoints: dict = field(default_factory=lambda: {
        "MME": os.getenv("MME_ENDPOINT", "http://localhost:9001"),
        "SGW": os.getenv("SGW_ENDPOINT", "http://localhost:9002"),
        "PGW": os.getenv("PGW_ENDPOINT", "http://localhost:9003"),
    })

    # TLS / LEA delivery
    lea_ca_cert_path: str = field(
        default_factory=lambda: os.getenv("LEA_CA_CERT_PATH", "")
    )
    lea_client_cert_path: str = field(
        default_factory=lambda: os.getenv("LEA_CLIENT_CERT_PATH", "")
    )
    lea_client_key_path: str = field(
        default_factory=lambda: os.getenv("LEA_CLIENT_KEY_PATH", "")
    )

    # Service ports
    admf_port: int = field(default_factory=lambda: int(os.getenv("ADMF_PORT", "8001")))
    iri_mf_port: int = field(default_factory=lambda: int(os.getenv("IRI_MF_PORT", "8002")))
    cc_mf_port: int = field(default_factory=lambda: int(os.getenv("CC_MF_PORT", "8003")))
