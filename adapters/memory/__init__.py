"""Vendor glue for the memory block (contracts/memory.py MemoryProvider v1).

The PostgreSQL backend — the 04_TECH_STACK default persistence target — lives
in adapters/memory/postgres.py (PostgresMemoryStore). Import it explicitly:

    from adapters.memory.postgres import PostgresMemoryStore

This package deliberately imports nothing: importing adapters.memory must not
pull psycopg (or any vendor SDK) at package import time — the vendor import
happens inside PostgresMemoryStore.__init__ so environments without the
postgres dependency group stay import-clean.
"""

from __future__ import annotations
