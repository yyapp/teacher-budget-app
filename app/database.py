# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/database.py

로컬 SQLite 장부 초기화 및 공통 질의 헬퍼.
장부는 budget, transaction, item, budget_allocation, refund, receipt_file,
request_log로 구성된다.
"""

import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "app.db")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_connection():
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    _ensure_dir()
    conn = get_connection()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS budget (
    budget_id TEXT NOT NULL PRIMARY KEY,
    budget_name TEXT NOT NULL,
    year INTEGER NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('확정','계획')),
    allocated_amount REAL NOT NULL,
    applied_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT NOT NULL PRIMARY KEY,
    budget_id TEXT NOT NULL,
    merchant_name TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    total_amount REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('확정','임시','취소')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (budget_id) REFERENCES budget(budget_id)
);

CREATE TABLE IF NOT EXISTS item (
    item_id TEXT NOT NULL PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    specification TEXT,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    unit_price REAL NOT NULL,
    item_amount REAL NOT NULL,
    selection_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id)
);

CREATE TABLE IF NOT EXISTS budget_allocation (
    allocation_id TEXT NOT NULL PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    budget_id TEXT NOT NULL,
    allocated_amount REAL NOT NULL,
    unallocated_amount REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id),
    FOREIGN KEY (budget_id) REFERENCES budget(budget_id)
);

CREATE TABLE IF NOT EXISTS refund (
    refund_id TEXT NOT NULL PRIMARY KEY,
    original_transaction_id TEXT NOT NULL,
    refund_date TEXT NOT NULL,
    refund_amount REAL NOT NULL,
    budget_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (original_transaction_id) REFERENCES transactions(transaction_id),
    FOREIGN KEY (budget_id) REFERENCES budget(budget_id)
);

CREATE TABLE IF NOT EXISTS receipt_file (
    receipt_id TEXT NOT NULL PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    file_type TEXT NOT NULL CHECK(file_type IN ('원본','정리본','json')),
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    original_total REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id)
);

CREATE TABLE IF NOT EXISTS cart_proposal (
    cart_id TEXT NOT NULL PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS request_log (
    request_id TEXT NOT NULL PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    related_transaction_id TEXT,
    related_receipt_id TEXT,
    error_note TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (related_transaction_id) REFERENCES transactions(transaction_id),
    FOREIGN KEY (related_receipt_id) REFERENCES receipt_file(receipt_id)
);
"""
