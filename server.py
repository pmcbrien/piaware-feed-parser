#!/usr/bin/env python3
"""
Strip Bay -- an ownership-first view of what your PiAware receiver hears.

Two ways to run:

  App mode (default when launched from Strip Bay.app, or with --app)
      Settings and registry live in ~/Library/Application Support/Strip Bay.
      Binds to localhost, opens a browser window, exits when left idle.

  Headless mode (--headless)
      Settings come from environment variables, binds to whatever BIND says.
      This is the shape that runs on the Pi under systemd.

Stdlib only -- nothing to pip install.
"""

import argparse
import json
import math
import os
import posixpath
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import build_registry
from registry import Registry

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
APP_NAME = "Strip Bay"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

DEFAULTS = {
    "piaware_host": "",
    "feed_1090": "",
    "feed_978": "",
    "receiver_lat": None,
    "receiver_lon": None,
    "poll_interval": 1.0,
    "contact_ttl": 60,
    "idle_exit_minutes": 30,
}


def log(message):
    sys.stderr.write("[strip-bay] {}\n".format(message))
    sys.stderr.flush()


# ---------------------------------------------------------------- settings


def default_data_dir():
    if sys.platform == "darwin":
        return os.path.expanduser(
            "~/Library/Application Support/{}".format(APP_NAME))
    return os.environ.get(
        "STRIP_BAY_HOME", os.path.expanduser("~/.local/share/strip-bay"))


class Settings(object):
    """Persisted in the data directory. The app bundle stays read-only."""

    def __init__(self, data_dir, use_environment=False):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "settings.json")
        self.db_path = os.path.join(data_dir, "registry.db")
        self.values = dict(DEFAULTS)
        self._lock = threading.Lock()
        if not os.path.isdir(data_dir):
            os.makedirs(data_dir)
        self.load()
        if use_environment:
            self._apply_environment()

    def _apply_environment(self):
        mapping = {
            "PIAWARE_HOST": ("piaware_host", str),
            "FEED_1090_URL": ("feed_1090", str),
            "FEED_978_URL": ("feed_978", str),
            "RECEIVER_LAT": ("receiver_lat", float),
            "RECEIVER_LON": ("receiver_lon", float),
            "POLL_INTERVAL": ("poll_interval", float),
            "CONTACT_TTL": ("contact_ttl", float),
        }
        for variable, (key, cast) in mapping.items():
            raw = os.environ.get(variable)
            if raw not in (None, ""):
                try:
                    self.values[key] = cast(raw)
                except ValueError:
                    log("ignoring {}={!r}".format(variable, raw))
        if os.environ.get("REGISTRY_DB"):
            self.db_path = os.environ["REGISTRY_DB"]
        self.values["idle_exit_minutes"] = 0  # never time out when headless

    def load(self):
        try:
            with open(self.path, "r") as handle:
                stored = json.load(handle)
            for key in DEFAULTS:
                if key in stored:
                    self.values[key] = stored[key]
        except (IOError, ValueError):
            pass

    def save(self):
        with self._lock:
            temporary = self.path + ".tmp"
            with open(temporary, "w") as handle:
                json.dump(self.values, handle, indent=2, sort_keys=True)
            os.replace(temporary, self.path)

    def update(self, changes):
        for key, value in changes.items():
            if key in DEFAULTS:
                self.values[key] = value
        self.save()

    def __getitem__(self, key):
        return self.values.get(key)


# ---------------------------------------------------------------- feeds


def fetch_json(url, timeout=4):
    request = urllib.request.Request(url, headers={"User-Agent": "strip-bay/1.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def feed_candidates(host, band):
    host = (host or "").strip().rstrip("/")
    if not host:
        return []
    if "://" in host:
        host = host.split("://", 1)[1]
    if band == "1090":
        # PiAware renamed the web path from dump1090-fa to skyaware in 5.0.
        paths = ["/skyaware/data/aircraft.json",
                 "/dump1090-fa/data/aircraft.json",
                 ":8080/data/aircraft.json"]
    else:
        paths = ["/skyaware978/data/aircraft.json",
                 "/dump978-fa/data/aircraft.json"]
    return ["http://" + host + path for path in paths]


def probe_feed(url, timeout=3):
    try:
        payload = fetch_json(url, timeout=timeout)
    except Exception as error:
        return False, str(error)
    if isinstance(payload, dict) and "aircraft" in payload:
        return True, "{} aircraft".format(len(payload["aircraft"]))
    return False, "responded, but not an aircraft feed"


def resolve_feed(host, band, override):
    override = (override or "").strip()
    if override.lower() in ("off", "none", "disabled"):
        return None
    candidates = [override] if override else feed_candidates(host, band)
    for url in candidates:
        ok, _ = probe_feed(url)
        if ok:
            log("{} MHz: using {}".format(band, url))
            return url
    return None


def haversine_nm(lat1, lon1, lat2, lon2):
    radius_nm = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return radius_nm * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def initial_bearing(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def first_number(source, *keys):
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float)):
            return value
    return None


class Collector(object):
    """Polls both bands on a background thread and keeps a merged snapshot."""

    def __init__(self, registry, settings):
        self.registry = registry
        self.settings = settings
        self.lock = threading.Lock()
        self.contacts = {}
        self.receiver = {"lat": None, "lon": None}
        self.feeds = {"1090": None, "978": None}
        self.health = {}
        self.reconfigure()

    def reconfigure(self):
        """Re-resolve both feeds. Safe to call while running."""
        host = self.settings["piaware_host"]
        self.feeds["1090"] = resolve_feed(host, "1090", self.settings["feed_1090"])
        self.feeds["978"] = resolve_feed(host, "978", self.settings["feed_978"])
        self.health = {}
        self._load_receiver_position()
        return dict(self.feeds)

    def _load_receiver_position(self):
        latitude = self.settings["receiver_lat"]
        longitude = self.settings["receiver_lon"]
        if latitude is not None and longitude is not None:
            self.receiver = {"lat": float(latitude), "lon": float(longitude)}
            return
        for url in (self.feeds["1090"], self.feeds["978"]):
            if not url:
                continue
            try:
                payload = fetch_json(url.replace("aircraft.json", "receiver.json"))
                if payload.get("lat") is not None:
                    self.receiver = {"lat": payload["lat"], "lon": payload["lon"]}
                    return
            except Exception:
                continue
        self.receiver = {"lat": None, "lon": None}

    def configured(self):
        return bool(self.feeds["1090"] or self.feeds["978"])

    def poll_once(self):
        now = time.time()
        seen_this_pass = {}

        for band in ("1090", "978"):
            url = self.feeds[band]
            if not url:
                continue
            try:
                payload = fetch_json(url)
                self.health[band] = "ok"
            except Exception as error:
                self.health[band] = str(error)
                continue

            for aircraft in payload.get("aircraft", []):
                icao = (aircraft.get("hex") or "").strip().lower()
                if not icao:
                    continue
                contact = self._shape(aircraft, band, now)
                previous = seen_this_pass.get(icao)
                # Same airframe on both bands: keep whichever heard it last.
                if previous is None or contact["seen"] < previous["seen"]:
                    if previous is not None:
                        contact["bands"] = sorted(set(previous["bands"] + contact["bands"]))
                    seen_this_pass[icao] = contact
                else:
                    previous["bands"] = sorted(set(previous["bands"] + contact["bands"]))

        ttl = float(self.settings["contact_ttl"] or 60)
        with self.lock:
            for icao, contact in seen_this_pass.items():
                existing = self.contacts.get(icao)
                contact["first_seen"] = existing["first_seen"] if existing else now
                self.contacts[icao] = contact
            for icao in [k for k, v in self.contacts.items()
                         if now - v["last_update"] > ttl]:
                del self.contacts[icao]

    def _shape(self, aircraft, band, now):
        icao = aircraft["hex"].strip().lower().lstrip("~")
        # A leading ~ marks a non-ADS-B target resolved by multilateration.
        anonymous = aircraft["hex"].startswith("~")

        latitude = aircraft.get("lat")
        longitude = aircraft.get("lon")
        distance = bearing = None
        if (latitude is not None and longitude is not None
                and self.receiver["lat"] is not None):
            distance = round(haversine_nm(
                self.receiver["lat"], self.receiver["lon"], latitude, longitude), 1)
            bearing = round(initial_bearing(
                self.receiver["lat"], self.receiver["lon"], latitude, longitude))

        # alt_baro is the string "ground" for surface targets, so first_number
        # skips it and on_ground below carries that fact instead.
        altitude = first_number(aircraft, "alt_baro", "altitude", "alt_geom")

        return {
            "icao": icao,
            "bands": [band],
            "callsign": (aircraft.get("flight") or "").strip(),
            "squawk": aircraft.get("squawk") or "",
            "altitude": altitude,
            "vertical_rate": first_number(aircraft, "baro_rate", "geom_rate", "vert_rate"),
            "ground_speed": first_number(aircraft, "gs", "speed"),
            "track": first_number(aircraft, "track", "heading"),
            "latitude": latitude,
            "longitude": longitude,
            "on_ground": aircraft.get("alt_baro") == "ground",
            "category": aircraft.get("category") or "",
            "rssi": aircraft.get("rssi"),
            "messages": aircraft.get("messages"),
            "seen": round(aircraft.get("seen", 0) or 0, 1),
            "distance_nm": distance,
            "bearing": bearing,
            "emergency": (aircraft.get("emergency") or "none") not in ("none", ""),
            "anonymous": anonymous,
            "last_update": now,
            "owner": self.registry.lookup(icao),
        }

    def snapshot(self):
        with self.lock:
            contacts = list(self.contacts.values())
        contacts.sort(key=lambda c: (
            c["distance_nm"] if c["distance_nm"] is not None else 9999, c["seen"]))
        return {
            "now": time.time(),
            "receiver": self.receiver,
            "contacts": contacts,
            "configured": self.configured(),
            "feeds": dict(self.health),
            "registry_size": self.registry.count(),
        }

    def run_forever(self):
        while True:
            try:
                if self.configured():
                    self.poll_once()
            except Exception as error:
                log("poll failed: {}".format(error))
            time.sleep(float(self.settings["poll_interval"] or 1.0))


# ---------------------------------------------------------------- registry build


class RegistryBuilder(object):
    """Runs build_registry in a thread and exposes progress to the UI."""

    def __init__(self, registry, settings):
        self.registry = registry
        self.settings = settings
        self.lock = threading.Lock()
        self.state = {"running": False, "percent": 0, "message": "",
                      "error": "", "finished": 0, "count": 0}

    def status(self):
        with self.lock:
            state = dict(self.state)
        state["available"] = self.registry.available
        state["size"] = self.registry.count()
        return state

    def start(self, include_opensky=False):
        with self.lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "percent": 0, "message": "Starting",
                          "error": "", "finished": 0, "count": 0}
        thread = threading.Thread(target=self._run, args=(include_opensky,))
        thread.daemon = True
        thread.start()
        return True

    def _progress(self, message, percent):
        with self.lock:
            self.state["message"] = message
            self.state["percent"] = percent

    def _run(self, include_opensky):
        try:
            count = build_registry.build(
                self.settings.db_path,
                opensky="__download__" if include_opensky else None,
                progress=self._progress)
            self.registry.reload()
            with self.lock:
                self.state.update({"running": False, "percent": 100,
                                   "message": "Done", "count": count,
                                   "finished": time.time()})
        except Exception as error:
            log("registry build failed: {}".format(error))
            with self.lock:
                self.state.update({"running": False, "error": str(error),
                                   "message": "Build failed"})


# ---------------------------------------------------------------- http


class Handler(BaseHTTPRequestHandler):
    server_version = "StripBay/1.1"
    protocol_version = "HTTP/1.1"

    context = None  # set in main()

    def log_message(self, fmt, *args):
        pass

    # -- helpers

    def _send(self, body, content_type="application/json", status=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(json.dumps(payload), "application/json", status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def _authorised(self):
        """
        Guards the endpoints that change things. The server listens on
        localhost, but any page in any browser can also reach localhost, so
        writes carry a per-launch key and the Host header is checked to keep
        a rebound DNS name from talking to us.
        """
        context = self.context
        if not context["key"]:
            return True
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
            return False
        return self.headers.get("X-Strip-Key") == context["key"]

    # -- routes

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        context = self.context
        context["last_seen"] = time.time()

        if path == "/api/contacts":
            return self._json(context["collector"].snapshot())

        if path.startswith("/api/registration/"):
            record = context["registry"].lookup(path.rsplit("/", 1)[-1])
            if not record:
                return self._json({"error": "Enter a 6-digit hex address."}, 400)
            return self._json(record)

        if path == "/api/search":
            term = (query.get("q") or [""])[0]
            return self._json({"results": context["registry"].search(term)})

        if path == "/api/settings":
            settings = context["settings"]
            return self._json({
                "values": settings.values,
                "data_dir": settings.data_dir,
                "feeds": context["collector"].feeds,
                "health": context["collector"].health,
                "receiver": context["collector"].receiver,
                "configured": context["collector"].configured(),
                "registry": context["builder"].status(),
                "app_mode": context["app_mode"],
            })

        if path == "/api/registry/state":
            return self._json(context["builder"].status())

        return self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        context = self.context
        context["last_seen"] = time.time()

        if not self._authorised():
            return self._json({"error": "This request is not authorised."}, 403)

        body = self._read_body()

        if path == "/api/settings":
            settings = context["settings"]
            changes = {}
            for key in ("piaware_host", "feed_1090", "feed_978"):
                if key in body:
                    changes[key] = str(body[key] or "").strip()
            for key in ("receiver_lat", "receiver_lon"):
                if key in body:
                    raw = body[key]
                    changes[key] = None if raw in ("", None) else float(raw)
            for key in ("contact_ttl", "idle_exit_minutes"):
                if key in body:
                    changes[key] = float(body[key])
            settings.update(changes)
            feeds = context["collector"].reconfigure()
            return self._json({
                "values": settings.values,
                "feeds": feeds,
                "configured": context["collector"].configured(),
                "receiver": context["collector"].receiver,
            })

        if path == "/api/probe":
            host = str(body.get("piaware_host") or "").strip()
            result = {}
            for band in ("1090", "978"):
                found = None
                for url in feed_candidates(host, band):
                    ok, detail = probe_feed(url)
                    if ok:
                        found = {"url": url, "detail": detail}
                        break
                result[band] = found
            return self._json({"result": result})

        if path == "/api/registry/build":
            started = context["builder"].start(bool(body.get("opensky")))
            return self._json({"started": started,
                               "state": context["builder"].status()})

        if path == "/api/quit":
            log("quit requested from the interface")
            threading.Timer(0.4, context["shutdown"]).start()
            return self._json({"ok": True})

        return self._json({"error": "Unknown request."}, 404)

    # -- static

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        safe = posixpath.normpath(path).lstrip("/")
        if safe.startswith("..") or os.path.isabs(safe):
            return self._send("Not found", "text/plain", 404)
        full = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(full):
            return self._send("Not found", "text/plain", 404)
        with open(full, "rb") as handle:
            body = handle.read()
        extension = os.path.splitext(full)[1].lower()
        return self._send(body, MIME.get(extension, "application/octet-stream"))


# ---------------------------------------------------------------- lifecycle


def choose_port(bind, preferred):
    for candidate in (preferred, 0):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((bind, candidate))
            port = probe.getsockname()[1]
            probe.close()
            return port
        except OSError:
            probe.close()
    raise SystemExit("Could not bind a port.")


def open_browser(url):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", url])
        else:
            import webbrowser
            webbrowser.open(url)
    except Exception as error:
        log("could not open a browser: {}".format(error))
        log("open this instead: {}".format(url))


def watch_for_idle(context, minutes):
    """Exit when nothing has talked to us for a while, so a forgotten
    window does not leave the app running until the Mac reboots."""
    limit = minutes * 60.0
    while True:
        time.sleep(30)
        if time.time() - context["last_seen"] > limit:
            log("idle for {:.0f} minutes, exiting".format(minutes))
            context["shutdown"]()
            return


def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--app", action="store_true",
                        help="run as the desktop app (default on macOS)")
    parser.add_argument("--headless", action="store_true",
                        help="server only: no browser, no idle exit")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "8890")))
    parser.add_argument("--bind", default=os.environ.get("BIND", ""))
    parser.add_argument("--data-dir", default=os.environ.get("STRIP_BAY_HOME"))
    arguments = parser.parse_args()

    app_mode = arguments.app or (not arguments.headless and sys.platform == "darwin")
    bind = arguments.bind or ("127.0.0.1" if app_mode else "0.0.0.0")

    settings = Settings(arguments.data_dir or default_data_dir(),
                        use_environment=not app_mode)
    registry = Registry(settings.db_path)
    collector = Collector(registry, settings)
    builder = RegistryBuilder(registry, settings)

    port = choose_port(bind, arguments.port)
    key = secrets.token_urlsafe(18) if app_mode else ""

    context = {
        "settings": settings, "registry": registry, "collector": collector,
        "builder": builder, "app_mode": app_mode, "key": key,
        "last_seen": time.time(), "shutdown": lambda: None,
    }
    Handler.context = context

    poller = threading.Thread(target=collector.run_forever)
    poller.daemon = True
    poller.start()

    httpd = ThreadingHTTPServer((bind, port), Handler)
    context["shutdown"] = lambda: threading.Thread(
        target=httpd.shutdown).start()

    url = "http://{}:{}/".format("127.0.0.1" if bind == "127.0.0.1" else bind, port)
    log("registry: {}".format(
        "{} aircraft".format(registry.count()) if registry.available
        else "not built yet"))
    log("listening on {}".format(url))

    if app_mode:
        launch_url = url + ("?k=" + key if key else "")
        log("open this if no window appears: {}".format(launch_url))
        open_browser(launch_url)
        idle = float(settings["idle_exit_minutes"] or 0)
        if idle > 0:
            watcher = threading.Thread(target=watch_for_idle, args=(context, idle))
            watcher.daemon = True
            watcher.start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    log("stopped")


if __name__ == "__main__":
    main()
