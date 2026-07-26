#!/bin/bash
#
# Strip Bay launcher.
#
# The app is plain Python with no third-party packages, so the only thing this
# has to do is find an interpreter that will actually run and hand over to it.
#
# The awkward part is /usr/bin/python3. On a Mac without the Command Line Tools
# it is a stub that pops a "install developer tools?" dialog when invoked, so it
# is only ever tried after confirming the tools are present.

set -u

BUNDLE="$(cd "$(dirname "$0")/.." && pwd)"
APP="$BUNDLE/Resources/app"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/Strip Bay.log"

mkdir -p "$LOG_DIR"

usable() {
    [ -x "$1" ] || return 1
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 6) else 1)' \
        >/dev/null 2>&1
}

find_python() {
    local candidates=()

    # Homebrew, then the python.org installer, then anything on PATH.
    candidates+=("/opt/homebrew/bin/python3" "/usr/local/bin/python3")
    for framework in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3; do
        [ -x "$framework" ] && candidates+=("$framework")
    done
    if command -v python3 >/dev/null 2>&1; then
        candidates+=("$(command -v python3)")
    fi

    for candidate in "${candidates[@]}"; do
        # Skip the stub unless the Command Line Tools are installed. Older
        # macOS has no 'readlink -f', so check the literal path as well.
        resolved="$candidate"
        if real="$(readlink -f "$candidate" 2>/dev/null)"; then
            resolved="$real"
        fi
        case "$candidate|$resolved" in
            */usr/bin/python3*) continue ;;
        esac
        if usable "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    if /usr/bin/xcode-select -p >/dev/null 2>&1 && usable /usr/bin/python3; then
        echo "/usr/bin/python3"
        return 0
    fi
    return 1
}

PYTHON="$(find_python)" || {
    /usr/bin/osascript <<'APPLESCRIPT' >/dev/null 2>&1
set message to "Strip Bay needs Python 3, and there isn't one installed.

The quickest fix is to install it from python.org, then open Strip Bay again."
display dialog message with title "Strip Bay" buttons {"Quit", "Get Python"} default button "Get Python" with icon caution
if button returned of result is "Get Python" then
    open location "https://www.python.org/downloads/macos/"
end if
APPLESCRIPT
    exit 1
}

{
    echo "--- $(date '+%Y-%m-%d %H:%M:%S') starting with $PYTHON"
} >> "$LOG"

# exec so the Dock's Quit sends its signal straight to the server.
exec "$PYTHON" "$APP/server.py" --app >> "$LOG" 2>&1
