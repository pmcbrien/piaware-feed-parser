#!/usr/bin/env python3
"""
Build the local ownership database.

Primary source is the FAA Releasable Aircraft Database, which contains a
MODE S CODE HEX column -- so US aircraft join directly on the ICAO address
seen on the air, with no N-number arithmetic involved.

    python3 build_registry.py                     # download and build
    python3 build_registry.py --zip ./faa.zip     # use a file you already have
    python3 build_registry.py --opensky ./ac.csv  # add non-US coverage

Old Raspbian releases sometimes fail the TLS handshake with registry.faa.gov.
If the download errors out, pull the zip on another machine and pass --zip.

Stdlib only. Compatible with Python 3.5+.
"""

import argparse
import csv
import io
import os
import sqlite3
import sys
import time
import urllib.request
import zipfile

FAA_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
OPENSKY_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry.db")

# Registrant type -> plain label. Drives the "who is this" framing in the UI.
REGISTRANT_TYPE = {
    "1": "Individual",
    "2": "Partnership",
    "3": "Corporation",
    "4": "Co-owned",
    "5": "Government",
    "7": "LLC",
    "8": "Non-citizen corporation",
    "9": "Non-citizen co-owned",
}

AIRCRAFT_TYPE = {
    "1": "Glider",
    "2": "Balloon",
    "3": "Blimp / dirigible",
    "4": "Fixed wing, single engine",
    "5": "Fixed wing, multi engine",
    "6": "Rotorcraft",
    "7": "Weight-shift-control",
    "8": "Powered parachute",
    "9": "Gyroplane",
    "H": "Hybrid lift",
    "O": "Other",
}

ENGINE_TYPE = {
    "0": "None",
    "1": "Reciprocating",
    "2": "Turbo-prop",
    "3": "Turbo-shaft",
    "4": "Turbo-jet",
    "5": "Turbo-fan",
    "6": "Ramjet",
    "7": "2-cycle",
    "8": "4-cycle",
    "10": "Electric",
    "11": "Rotary",
}

STATUS_CODE = {
    "A": "Triennial form undeliverable",
    "D": "Expired Dealer certificate",
    "E": "Certificate revoked",
    "M": "Valid, aircraft registered",
    "N": "Non-citizen corporation expired",
    "R": "Registration pending",
    "S": "Second triennial attempt",
    "T": "Valid, registration pending cancel",
    "V": "Valid registration",
    "W": "Certificate deregistered",
    "X": "Enforcement letter",
    "Z": "Permanent reserved",
}

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

DROP TABLE IF EXISTS registration;
CREATE TABLE registration (
    icao            TEXT PRIMARY KEY,
    tail            TEXT,
    owner           TEXT,
    owner_type      TEXT,
    other_names     TEXT,
    street          TEXT,
    city            TEXT,
    region          TEXT,
    postal          TEXT,
    country         TEXT,
    manufacturer    TEXT,
    model           TEXT,
    year_built      TEXT,
    serial          TEXT,
    aircraft_type   TEXT,
    engine_type     TEXT,
    engines         TEXT,
    seats           TEXT,
    status          TEXT,
    cert_issued     TEXT,
    expires         TEXT,
    deregistered    INTEGER DEFAULT 0,
    source          TEXT
);
CREATE INDEX idx_tail ON registration (tail);
CREATE INDEX idx_owner ON registration (owner);
"""


def log(message):
    sys.stderr.write("[build] {}\n".format(message))
    sys.stderr.flush()


def download(url, destination, progress=None):
    log("downloading {}".format(url))
    start = time.time()
    request = urllib.request.Request(url, headers={"User-Agent": "piaware-owner/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with open(destination, "wb") as handle:
            while True:
                chunk = response.read(262144)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress("Downloading registry", 5 + int(45.0 * done / total))
    size = os.path.getsize(destination) / 1048576.0
    log("got {:.1f} MB in {:.0f}s".format(size, time.time() - start))
    return destination


def normalize_key(key):
    return " ".join((key or "").strip().upper().split())


def read_table(archive, member):
    """Yield dicts with normalized keys and stripped values."""
    try:
        raw = archive.read(member)
    except KeyError:
        log("{} not present in archive, skipping".format(member))
        return
    text = io.StringIO(raw.decode("latin-1"))
    reader = csv.DictReader(text)
    reader.fieldnames = [normalize_key(name) for name in (reader.fieldnames or [])]
    for row in reader:
        yield {k: (v or "").strip() for k, v in row.items() if k}


def load_aircraft_reference(archive):
    """MFR MDL CODE -> make/model details."""
    reference = {}
    for row in read_table(archive, "ACFTREF.txt"):
        code = row.get("CODE", "")
        if not code:
            continue
        reference[code] = {
            "manufacturer": row.get("MFR", ""),
            "model": row.get("MODEL", ""),
            "aircraft_type": AIRCRAFT_TYPE.get(row.get("TYPE-ACFT", ""), ""),
            "engine_type": ENGINE_TYPE.get(row.get("TYPE-ENG", ""), ""),
            "engines": row.get("NO-ENG", "").lstrip("0"),
            "seats": row.get("NO-SEATS", "").lstrip("0"),
        }
    log("aircraft reference: {} models".format(len(reference)))
    return reference


def format_date(value):
    """FAA dates are YYYYMMDD. Return YYYY-MM-DD, or blank."""
    value = (value or "").strip()
    if len(value) == 8 and value.isdigit():
        return "{}-{}-{}".format(value[0:4], value[4:6], value[6:8])
    return ""


def collect_other_names(row):
    names = []
    for index in range(1, 6):
        name = row.get("OTHER NAMES({})".format(index), "").strip()
        if name:
            names.append(name)
    return " / ".join(names)


def build_row(row, reference, deregistered):
    icao = row.get("MODE S CODE HEX", "").strip().lower()
    if not icao or icao in ("000000",):
        return None
    icao = icao.zfill(6)

    details = reference.get(row.get("MFR MDL CODE", ""), {})
    tail = row.get("N-NUMBER", "").strip()
    if tail and not tail.upper().startswith("N"):
        tail = "N" + tail

    year = row.get("YEAR MFR", "").strip()
    if year in ("0000", "0"):
        year = ""

    manufacturer = details.get("manufacturer", "")
    model = details.get("model", "")
    if row.get("KIT MFR"):
        manufacturer = manufacturer or row.get("KIT MFR", "")
        model = model or row.get("KIT MODEL", "")

    return (
        icao,
        tail.upper(),
        row.get("NAME", ""),
        REGISTRANT_TYPE.get(row.get("TYPE REGISTRANT", ""), ""),
        collect_other_names(row),
        " ".join(part for part in (row.get("STREET", ""), row.get("STREET2", "")) if part),
        row.get("CITY", ""),
        row.get("STATE", ""),
        row.get("ZIP CODE", "")[:10],
        row.get("COUNTRY", "") or "US",
        manufacturer,
        model,
        year,
        row.get("SERIAL NUMBER", ""),
        details.get("aircraft_type", ""),
        details.get("engine_type", ""),
        details.get("engines", ""),
        details.get("seats", ""),
        STATUS_CODE.get(row.get("STATUS CODE", ""), row.get("STATUS CODE", "")),
        format_date(row.get("CERT ISSUE DATE", "")),
        format_date(row.get("EXPIRATION DATE", "")),
        1 if deregistered else 0,
        "FAA",
    )


INSERT = """
INSERT OR REPLACE INTO registration VALUES
(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def load_faa(connection, zip_path, progress=None):
    with zipfile.ZipFile(zip_path) as archive:
        if progress:
            progress("Reading aircraft models", 55)
        reference = load_aircraft_reference(archive)

        # Deregistered first, so an active MASTER row overwrites it.
        for member, is_dereg in (("DEREG.txt", True), ("MASTER.txt", False)):
            if progress:
                progress("Indexing {}".format(member), 62 if is_dereg else 72)
            batch = []
            count = 0
            for row in read_table(archive, member):
                record = build_row(row, reference, is_dereg)
                if not record:
                    continue
                batch.append(record)
                count += 1
                if len(batch) >= 5000:
                    connection.executemany(INSERT, batch)
                    batch = []
            if batch:
                connection.executemany(INSERT, batch)
            connection.commit()
            log("{}: {} rows with a Mode S address".format(member, count))


def load_opensky(connection, csv_path):
    """
    Optional non-US coverage. The OpenSky metadata CSV is crowd-maintained:
    good breadth, patchy accuracy. Only fills gaps -- never overwrites FAA.
    """
    existing = set(
        row[0] for row in connection.execute("SELECT icao FROM registration")
    )
    added = 0
    batch = []
    with open(csv_path, "r", encoding="utf-8", errors="replace") as handle:
        for row in csv.DictReader(handle):
            icao = (row.get("icao24") or "").strip().lower()
            if not icao or icao in existing:
                continue
            owner = (row.get("owner") or "").strip()
            tail = (row.get("registration") or "").strip().upper()
            if not owner and not tail:
                continue
            existing.add(icao)
            batch.append((
                icao.zfill(6), tail, owner, "", (row.get("operator") or "").strip(),
                "", "", "", "", "",
                (row.get("manufacturername") or "").strip(),
                (row.get("model") or "").strip(),
                (row.get("built") or "")[:4], (row.get("serialnumber") or "").strip(),
                "", (row.get("engines") or "").strip(), "", "",
                "", "", "", 0, "OpenSky",
            ))
            added += 1
            if len(batch) >= 5000:
                connection.executemany(INSERT, batch)
                batch = []
    if batch:
        connection.executemany(INSERT, batch)
    connection.commit()
    log("OpenSky: added {} aircraft not covered by the FAA file".format(added))


def build(db_path, zip_path=None, opensky=None, keep=False, progress=None):
    """
    Build the registry database. Returns the number of aircraft written.

    progress is an optional callable(message, percent) so a UI can follow along.
    Safe to call from a thread; nothing here touches global state.
    """
    def report(message, percent):
        if progress:
            progress(message, percent)

    workdir = os.path.dirname(os.path.abspath(db_path)) or "."
    if not os.path.isdir(workdir):
        os.makedirs(workdir)
    temporary = []

    report("Starting", 2)
    if not zip_path:
        zip_path = os.path.join(workdir, "ReleasableAircraft.zip")
        download(FAA_URL, zip_path, progress)
        temporary.append(zip_path)

    build_path = db_path + ".tmp"
    if os.path.exists(build_path):
        os.remove(build_path)

    connection = sqlite3.connect(build_path)
    connection.executescript(SCHEMA)
    load_faa(connection, zip_path, progress)

    if opensky:
        if opensky == "__download__":
            opensky = os.path.join(workdir, "aircraftDatabase.csv")
            report("Downloading non-US data", 88)
            download(OPENSKY_URL, opensky)
            temporary.append(opensky)
        report("Merging non-US data", 92)
        load_opensky(connection, opensky)

    report("Compacting", 96)
    connection.executescript("VACUUM;")
    total = connection.execute("SELECT COUNT(*) FROM registration").fetchone()[0]
    connection.close()

    os.replace(build_path, db_path)

    if not keep:
        for path in temporary:
            try:
                os.remove(path)
            except OSError:
                pass

    size = os.path.getsize(db_path) / 1048576.0
    log("wrote {} -- {} aircraft, {:.1f} MB".format(db_path, total, size))
    report("Done", 100)
    return total


def main():
    parser = argparse.ArgumentParser(description="Build the ownership database.")
    parser.add_argument("--db", default=DEFAULT_DB, help="output SQLite path")
    parser.add_argument("--zip", help="use this FAA zip instead of downloading")
    parser.add_argument("--opensky", nargs="?", const="__download__",
                        help="merge OpenSky metadata CSV (path, or omit to download)")
    parser.add_argument("--keep", action="store_true", help="keep downloaded files")
    args = parser.parse_args()

    build(args.db, zip_path=args.zip, opensky=args.opensky, keep=args.keep,
          progress=lambda message, percent: log("{:>3}%  {}".format(percent, message)))


if __name__ == "__main__":
    main()
