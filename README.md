Author Patrick McBrien

![alt text](https://github.com/pmcbrien/piaware-feed-parser/blob/main/3.png)
![alt text](https://github.com/pmcbrien/piaware-feed-parser/blob/main/4.png)

# Strip Bay

A macOS app that shows who owns the aircraft your PiAware receiver is hearing.

SkyAware and tar1090 answer *where* an aircraft is. Strip Bay answers *whose it
is* — registered owner, registrant type, airframe, and certificate status arrive
with the track instead of after a separate lookup. It reads both of your bands,
1090 MHz and 978 UAT, and merges them into one board.

The Mac connects to the Pi over your LAN. Nothing new gets installed on the Pi,
and the 60 MB registry lives on the Mac rather than the SD card.

## Install

Open `StripBay-1.1.dmg` and drag **Strip Bay** to Applications.

**The first launch will be blocked.** The app is not signed with an Apple
Developer ID, so Gatekeeper stops it. Open it once this way:

1. Double-click Strip Bay. macOS refuses and offers only Done.
2. Go to **System Settings → Privacy & Security**, scroll down, and click
   **Open Anyway** next to the message about Strip Bay.
3. Confirm. It opens normally from then on.

Or clear the quarantine flag from a terminal:

```bash
xattr -dr com.apple.quarantine "/Applications/Strip Bay.app"
```

There is a `StripBay-1.1.zip` alongside the DMG with the same app inside. The
DMG was built on Linux, so it is an ISO9660 image rather than the HFS+ one
`hdiutil` makes; macOS mounts it, but if yours objects, use the zip. Rebuilding
on your Mac (below) produces a normal DMG.

### Python

Strip Bay is plain Python with no third-party packages, and it uses whichever
Python 3 you already have — Homebrew, python.org, or the Command Line Tools. It
deliberately avoids triggering the developer-tools installer prompt. If it
cannot find one, it says so and points you at python.org.

Bundling a runtime would have removed this step, but that needs a build on
macOS, and it would take the app from about 400 KB to roughly 40 MB.

## First run

The Setup panel opens by itself. Two things to fill in:

**Your receiver.** The Pi's hostname or IP — `piaware.local` usually works.
Press Test and Strip Bay probes both bands across all the paths PiAware has used
(`/skyaware/`, `/dump1090-fa/`, port 8080, and the 978 equivalents), so you do
not have to know which layout your version uses.

**The registry.** Press Build registry. It pulls the FAA Releasable Aircraft
Database, about 60 MB, and builds a local index — a couple of minutes. Tick
*Include non-US aircraft* to also merge the OpenSky metadata set, which is
crowd-maintained: good breadth, patchier accuracy, and it only ever fills gaps
rather than overwriting an FAA record.

Rebuild every week or so. Settings and the database live in
`~/Library/Application Support/Strip Bay/`; the log is at
`~/Library/Logs/Strip Bay.log`.

## Why it looks like this

The layout is an air traffic control flight progress strip board. Each contact
is a paper strip in a holder: printed data on the top line, owner pencilled into
the remarks line below. Buff stock is 1090 MHz, pale blue is 978 UAT — the way a
real bay uses paper colour to separate classes of traffic at a glance. Strips
slide in from the right as contacts appear, and the paper yellows as a contact
goes stale, so a strip that has stopped updating looks old before you read the
timestamp.

There is deliberately **no map**. tar1090 already does that better than a second
implementation would, and dropping it keeps the whole surface pointed at the
question this tool exists to answer. Range and bearing from your receiver appear
on every strip instead.

## When there is no registry match

Every US ICAO address encodes its own N-number. The FAA allocates the block
`A00001`–`ADF7C7` algorithmically, so the tail can be recovered from the hex
alone. When the registry has no row, Strip Bay derives the tail and marks the
strip **Not in registry** — you still get a tail number, just no owner name.
This also means the app is useful before you have built the registry at all.

The conversion in `nnumber.py` round-trips all 915,399 addresses in the block;
`python3 nnumber.py` re-runs that check.

Two caveats worth knowing:

- Aircraft enrolled in the FAA **Privacy ICAO Address** program broadcast an
  alternate address not tied to their registration. A derived tail for one of
  those is arithmetically correct and factually meaningless.
- **LADD** participants are filtered from FlightAware and similar services, but
  not from what your own antenna receives. Your bay will show them.

## Running it

Strip Bay opens your browser onto a local server and binds to localhost only.
Requests that change anything carry a per-launch key and check the Host header,
so another page in your browser cannot quietly reconfigure it.

Quit from the button in the bottom-right, or from the Dock. If you close the
window and forget about it, it exits on its own after thirty minutes idle —
adjustable in settings, `0` to disable.

## Rebuilding

```bash
python3 make_icon.py                    # regenerate the icon (needs Pillow)
./packaging/build.sh                    # app + DMG
./packaging/build.sh --app-only         # bundle only
./packaging/build.sh --sign "Developer ID Application: Your Name (TEAMID)"
```

On macOS this uses `hdiutil` and produces a compressed UDZO image. On Linux it
falls back to `genisoimage`. Change `IDENTIFIER` at the top of `build.sh` from
the `com.example` placeholder before you sign anything.

Signing removes the Gatekeeper prompt for you. Removing it for anyone else also
needs notarisation (`xcrun notarytool submit`), which requires a paid Developer
account.

## Running on the Pi instead

The same code still runs headless, which is what you want if you would rather
leave it on all the time and reach it from any device:

```bash
python3 server.py --headless          # env vars, binds 0.0.0.0:8890
```

`config.example.env` lists the variables and `strip-bay.service` is a systemd
unit for it. In this mode there is no auth key and no idle exit, so keep it on
the LAN. Note that building the registry on Buster can fail the TLS handshake
with `registry.faa.gov`; build it elsewhere and pass
`build_registry.py --zip ReleasableAircraft.zip`.

## A note on the address field

FAA registration data is public record, and for individually owned aircraft the
registrant address is frequently a home address. Strip Bay keeps street-level
detail off the strips and shows only city and state; the full address appears
only when you open a registration card.

## Files

```
server.py            HTTP server, feed polling, band merge, settings
registry.py          lookup with caching, hot reload, derived-tail fallback
nnumber.py           ICAO <-> N-number, both directions
build_registry.py    FAA (and optional OpenSky) to SQLite
static/              the bay
make_icon.py         draws StripBay.icns
packaging/           launcher, build script
```

## Keyboard

`/` focuses the filter. `Enter` on a strip opens its registration card.
`Esc` closes whatever is open.
