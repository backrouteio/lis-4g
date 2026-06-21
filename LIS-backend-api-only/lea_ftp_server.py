#!/usr/bin/env python3
"""
LEA Agent - FTP Server and HI2/HI3 API (No Portals)

Implements:
- HI2: IRI event reception (HTTP POST)
- HI3: CC file reception (FTP)
- API: CC file management and status
"""

import asyncio
import sqlite3
import json
import logging
from datetime import datetime
from typing import List, Dict
from pathlib import Path
import argparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import ThreadedFTPServer
import uvicorn
import threading

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# Data Models
# ============================================================================

class IRIEvent(BaseModel):
    liid: str
    event_id: str
    event_type: str
    timestamp: str
    calling_party: str = None
    called_party: str = None
    call_direction: str = None
    imsi: str = None
    imei: str = None
    additional_info: dict = {}

# ============================================================================
# Database
# ============================================================================

class LEADatabase:
    def __init__(self, db_path: str = "./lea_agent.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Initialize database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ftp_files (
                id INTEGER PRIMARY KEY,
                liid TEXT NOT NULL,
                filename TEXT UNIQUE NOT NULL,
                file_size INTEGER,
                received_timestamp TEXT NOT NULL,
                status TEXT DEFAULT 'Received',
                created_at TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS iri_events (
                id INTEGER PRIMARY KEY,
                liid TEXT NOT NULL,
                event_id TEXT UNIQUE NOT NULL,
                event_type TEXT,
                timestamp TEXT NOT NULL,
                calling_party TEXT,
                called_party TEXT,
                imsi TEXT,
                imei TEXT,
                created_at TEXT NOT NULL
            )
        ''')

        conn.commit()
        conn.close()

    def log_iri(self, event: IRIEvent) -> None:
        """Log IRI event"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO iri_events (
                liid, event_id, event_type, timestamp,
                calling_party, called_party, imsi, imei, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            event.liid, event.event_id, event.event_type,
            event.timestamp, event.calling_party, event.called_party,
            event.imsi, event.imei, datetime.utcnow().isoformat()
        ))

        conn.commit()
        conn.close()
        logger.info(f"IRI logged: LIID={event.liid} EventID={event.event_id}")

    def log_ftp_file(self, liid: str, filename: str, file_size: int) -> None:
        """Log FTP file receipt"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO ftp_files (
                liid, filename, file_size, received_timestamp, created_at
            ) VALUES (?, ?, ?, ?, ?)
        ''', (
            liid, filename, file_size,
            datetime.utcnow().isoformat(),
            datetime.utcnow().isoformat()
        ))

        conn.commit()
        conn.close()
        logger.info(f"FTP file logged: LIID={liid} File={filename}")

    def get_cc_files(self, liid: str = None, limit: int = 100) -> List[Dict]:
        """Get CC files"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if liid:
            cursor.execute(
                'SELECT * FROM ftp_files WHERE liid = ? ORDER BY created_at DESC LIMIT ?',
                (liid, limit)
            )
        else:
            cursor.execute('SELECT * FROM ftp_files ORDER BY created_at DESC LIMIT ?', (limit,))

        rows = cursor.fetchall()
        conn.close()

        files = []
        for row in rows:
            files.append({
                'file_id': f"cc-{row[0]}",
                'liid': row[1],
                'filename': row[2],
                'file_size': row[3],
                'received_timestamp': row[4],
                'status': row[5]
            })

        return files

# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="LEA-4G Agent API",
    description="Law Enforcement Agency - HI2/HI3 Reception",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

db = LEADatabase()

# ============================================================================
# HI2 Endpoints
# ============================================================================

@app.post("/hi2/iri", tags=["HI2 - IRI Reception"])
async def receive_iri(event: IRIEvent):
    """Receive IRI event from LIS"""
    db.log_iri(event)
    logger.info(f"HI2: IRI received - LIID={event.liid} EventID={event.event_id}")
    return {
        "status": "RECEIVED",
        "event_id": event.event_id,
        "timestamp": datetime.utcnow().isoformat()
    }

# ============================================================================
# HI3 Endpoints
# ============================================================================

@app.get("/hi3/cc/status", tags=["HI3 - CC Reception"])
async def get_cc_status(liid: str = None):
    """Get CC delivery status"""
    files = db.get_cc_files(liid)
    return {
        "pending_files": [],
        "delivered_files": files
    }

# ============================================================================
# FTP Server Management
# ============================================================================

@app.get("/ftp/server/status", tags=["FTP Server"])
async def get_ftp_status():
    """Get FTP server status"""
    return {
        "status": "RUNNING",
        "host": "0.0.0.0",
        "port": 21,
        "username": "lea",
        "connections": 0
    }

# ============================================================================
# CC File Management
# ============================================================================

@app.get("/cc/files/list", tags=["CC Files"])
async def list_cc_files(liid: str = None, limit: int = 100):
    """List received CC files"""
    files = db.get_cc_files(liid, limit)
    total_size = sum(f['file_size'] for f in files if f['file_size'])

    return {
        "files": files,
        "total_count": len(files),
        "total_size": total_size
    }

# ============================================================================
# Health Check
# ============================================================================

@app.get("/health", tags=["Health"])
async def health_check():
    """LEA agent health status"""
    return {
        "status": "OK",
        "service": "LEA-4G Agent",
        "ftp_server": "RUNNING",
        "api_port": 8443,
        "ftp_port": 21,
        "timestamp": datetime.utcnow().isoformat()
    }

# ============================================================================
# FTP Server Thread
# ============================================================================

def start_ftp_server(host: str, port: int):
    """Start FTP server in background thread"""
    authorizer = DummyAuthorizer()
    authorizer.add_user("lea", "lea", "./cc_received", perm="elradfmw")

    handler = FTPHandler
    handler.authorizer = authorizer
    handler.permit_foreign_addresses = True
    handler.passive_ports = range(6000, 6100)

    server = ThreadedFTPServer((host, port), handler)
    logger.info(f"FTP server started: {host}:{port}")

    server.serve_forever()

# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="LEA-4G Agent")
    parser.add_argument("--host", default="0.0.0.0", help="API host")
    parser.add_argument("--api-port", type=int, default=8443, help="API port")
    parser.add_argument("--ftp-port", type=int, default=21, help="FTP port")

    args = parser.parse_args()

    # Create cc_received directory
    Path("cc_received").mkdir(exist_ok=True)

    # Start FTP server in background
    ftp_thread = threading.Thread(
        target=start_ftp_server,
        args=(args.host, args.ftp_port),
        daemon=True
    )
    ftp_thread.start()

    logger.info("="*70)
    logger.info("LEA-4G Agent - Starting")
    logger.info(f"API: {args.host}:{args.api_port}")
    logger.info(f"FTP: {args.host}:{args.ftp_port} (User: lea/lea)")
    logger.info("="*70)

    # Start FastAPI
    uvicorn.run(
        app,
        host=args.host,
        port=args.api_port,
        log_level="info"
    )

if __name__ == "__main__":
    main()
