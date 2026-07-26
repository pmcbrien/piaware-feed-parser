"""
Ownership lookup for an ICAO address.

Order of resolution:

    1. Exact hit in the local registry database.
    2. No hit, but the address is inside the US block -> derive the N-number
       arithmetically and return a record marked "derived". The tail is
       correct; the owner is simply not in the file yet.
    3. Neither -> return a record marked "unknown", carrying whatever the
       aircraft broadcast about itself.

Stdlib only. Compatible with Python 3.5+.
"""

import os
import sqlite3
import threading

from nnumber import icao_to_n_number, is_us_address

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry.db")

COLUMNS = (
    "icao tail owner owner_type other_names street city region postal country "
    "manufacturer model year_built serial aircraft_type engine_type engines "
    "seats status cert_issued expires deregistered source"
).split()

# Blocks worth calling out on sight. Not ownership, but it explains why a
# contact has no registry entry.
SPECIAL_BLOCKS = (
    (0xADF7C8, 0xAFFFFF, "US military"),
    (0xAE0000, 0xAFFFFF, "US military"),
    (0x43C000, 0x43CFFF, "UK military"),
    (0x3B7000, 0x3BFFFF, "French military"),
    (0x3EA000, 0x3EBFFF, "German military"),
    (0x7CF800, 0x7CFAFF, "Australian military"),
    (0xC00000, 0xC3FFFF, "Canada"),
    (0x4B0000, 0x4B7FFF, "Switzerland"),
    (0x7C0000, 0x7FFFFF, "Australia"),
    (0x400000, 0x43FFFF, "United Kingdom"),
    (0x3C0000, 0x3FFFFF, "Germany"),
    (0x380000, 0x3BFFFF, "France"),
    (0x300000, 0x33FFFF, "Italy"),
    (0x340000, 0x37FFFF, "Spain"),
    (0x0A0000, 0x0AFFFF, "Mexico"),
    (0xE00000, 0xE3FFFF, "Argentina / Brazil region"),
    (0x800000, 0x83FFFF, "India"),
    (0x840000, 0x87FFFF, "Japan"),
    (0x71C000, 0x71FFFF, "South Korea"),
    (0x780000, 0x7BFFFF, "China"),
    (0x150000, 0x1FFFFF, "Russia"),
)


def describe_block(icao_int):
    for low, high, label in SPECIAL_BLOCKS:
        if low <= icao_int <= high:
            return label
    return ""


class Registry(object):
    def __init__(self, path=DEFAULT_DB, cache_limit=20000):
        self.path = path
        self.available = os.path.exists(path)
        self._local = threading.local()
        self._cache = {}
        self._cache_limit = cache_limit
        self._lock = threading.Lock()
        self._generation = 0

    def _connection(self):
        connection = getattr(self._local, "connection", None)
        generation = getattr(self._local, "generation", -1)
        if connection is not None and generation != self._generation:
            # The file was replaced underneath us. Drop the stale handle.
            try:
                connection.close()
            except sqlite3.Error:
                pass
            connection = None
        if connection is None:
            connection = sqlite3.connect(
                "file:{}?mode=ro".format(self.path), uri=True, timeout=5
            )
            connection.row_factory = sqlite3.Row
            self._local.connection = connection
            self._local.generation = self._generation
        return connection

    def reload(self):
        """Call after the database file has been replaced."""
        with self._lock:
            self._cache.clear()
            self._generation += 1
        self.available = os.path.exists(self.path)

    def count(self):
        if not self.available:
            return 0
        try:
            return self._connection().execute(
                "SELECT COUNT(*) FROM registration"
            ).fetchone()[0]
        except sqlite3.Error:
            return 0

    def lookup(self, icao):
        """Return an ownership dict for a lowercase hex address."""
        icao = (icao or "").strip().lower().lstrip("~")
        if not icao:
            return None

        with self._lock:
            cached = self._cache.get(icao)
        if cached is not None:
            return cached

        record = self._resolve(icao)

        with self._lock:
            if len(self._cache) >= self._cache_limit:
                self._cache.clear()
            self._cache[icao] = record
        return record

    def _resolve(self, icao):
        try:
            icao_int = int(icao, 16)
        except ValueError:
            icao_int = -1

        if self.available:
            try:
                row = self._connection().execute(
                    "SELECT * FROM registration WHERE icao = ?", (icao,)
                ).fetchone()
            except sqlite3.Error:
                row = None
            if row is not None:
                record = {column: (row[column] or "") for column in COLUMNS}
                record["deregistered"] = bool(row["deregistered"])
                record["resolution"] = "deregistered" if record["deregistered"] else "registry"
                record["block"] = describe_block(icao_int)
                return record

        blank = {column: "" for column in COLUMNS}
        blank["icao"] = icao
        blank["deregistered"] = False
        blank["block"] = describe_block(icao_int)

        derived_tail = icao_to_n_number(icao_int) if is_us_address(icao_int) else None
        if derived_tail:
            blank["tail"] = derived_tail
            blank["country"] = "US"
            blank["resolution"] = "derived"
            blank["source"] = "N-number arithmetic"
        else:
            blank["resolution"] = "unknown"

        return blank

    def search(self, term, limit=50):
        """Free-text search across owner, tail and model."""
        if not self.available or not term:
            return []
        pattern = "%{}%".format(term.strip().upper())
        try:
            rows = self._connection().execute(
                "SELECT * FROM registration "
                "WHERE tail LIKE ? OR UPPER(owner) LIKE ? OR UPPER(model) LIKE ? "
                "ORDER BY deregistered, tail LIMIT ?",
                (pattern, pattern, pattern, limit),
            ).fetchall()
        except sqlite3.Error:
            return []
        return [{column: (row[column] or "") for column in COLUMNS} for row in rows]

