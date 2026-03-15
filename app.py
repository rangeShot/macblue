#!/usr/bin/env python3
"""
macblue — switch Bluetooth devices between Macs from the menu bar.
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import rumps

if sys.version_info < (3, 9):
    sys.exit("macblue requires Python 3.9 or later.")

# ── Paths ─────────────────────────────────────────────────────────────────────

def _find_source_dir() -> Path:
    if env := os.environ.get("MACBLUE_DIR"):
        return Path(env).resolve()
    candidate = Path(__file__).resolve().parents[4]
    if (candidate / "app.py").exists():
        return candidate
    return Path(__file__).parent.resolve()


BASE_DIR          = _find_source_dir()
DISCONNECT_SCRIPT = BASE_DIR / "scripts" / "disconnect.sh"
CONNECT_SCRIPT    = BASE_DIR / "scripts" / "connect.sh"
LOG_PATH          = BASE_DIR / "macblue.log"
BUNDLE_ID         = "com.macblue.app"

def _resolve_icon() -> str:
    """Find the best menu-bar icon (prefer @2x for retina) and return its path."""
    candidates = [
        BASE_DIR / "assets" / "icon_menubar@2x.png",
        BASE_DIR / "assets" / "icon_menubar.png",
        Path(__file__).parent / "icon_menubar@2x.png",
        Path(__file__).parent / "icon_menubar.png",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return ""

ICON_PATH = _resolve_icon()

VERSION    = "1.0.1"
GITHUB_REPO = "rangeshot/macblue"
MAC_RE      = re.compile(r"^([0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}$")

logging.basicConfig(
    filename=str(LOG_PATH), level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("macblue")

# ── Preferences (NSUserDefaults) ──────────────────────────────────────────────

_device_cache: list[dict] = []   # in-memory cache, always in sync


def _get_defaults():
    from Foundation import NSUserDefaults
    return NSUserDefaults.standardUserDefaults()


def load_devices() -> list[dict]:
    """Return registered devices from cache (or NSUserDefaults on first call)."""
    global _device_cache
    if _device_cache:
        return list(_device_cache)
    try:
        raw = _get_defaults().arrayForKey_("devices")
        if not raw:
            return []
        _device_cache = [{"name": str(d.get("name", "Unknown")),
                           "address": str(d.get("address", ""))} for d in raw]
        return list(_device_cache)
    except Exception as e:
        log.error("Failed to load devices from defaults: %s", e)
    return []


def save_devices(devices: list[dict]):
    """Persist registered devices to NSUserDefaults and update cache."""
    global _device_cache
    _device_cache = list(devices)
    _get_defaults().setObject_forKey_(devices, "devices")
    _get_defaults().synchronize()
    log.info("Devices saved to preferences: %s", [d["name"] for d in devices])


def has_devices() -> bool:
    return bool(load_devices())


def _migrate_from_config_json():
    """One-time migration: import devices from old config.json into NSUserDefaults."""
    config_path = BASE_DIR / "config.json"
    if not config_path.exists():
        return
    if load_devices():
        return  # already have devices in defaults, skip
    try:
        import json as _json
        with open(config_path, encoding="utf-8") as f:
            cfg = _json.load(f)
        devices = cfg.get("devices", [])
        if devices:
            save_devices(devices)
            log.info("Migrated %d device(s) from config.json to preferences.", len(devices))
    except Exception as e:
        log.warning("Could not migrate config.json: %s", e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_blueutil() -> str:
    for p in ["/opt/homebrew/bin/blueutil", "/usr/local/bin/blueutil"]:
        if os.path.isfile(p):
            return str(Path(p).resolve())
    raise RuntimeError("blueutil not found.\nInstall with: brew install blueutil")


def get_paired_devices() -> list[dict]:
    blueutil = get_blueutil()
    try:
        r = subprocess.run(
            [blueutil, "--paired", "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
    except FileNotFoundError:
        raise RuntimeError(f"blueutil not found at {blueutil}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("blueutil timed out scanning devices.")
    if r.returncode != 0:
        raise RuntimeError(f"blueutil error:\n{(r.stderr or r.stdout).strip()}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("blueutil returned invalid output.\nTry: brew upgrade blueutil")


# ── Update Checker ────────────────────────────────────────────────────────────

def _parse_version(v: str) -> tuple[int, ...]:
    """Parse '1.2.3' or 'v1.2.3' into (1, 2, 3)."""
    return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())


def check_for_update():
    """Check GitHub for a newer release. Returns {"version": ..., "url": ...} or None."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        tag = data.get("tag_name", "")
        remote = _parse_version(tag)
        local  = _parse_version(VERSION)
        if remote > local:
            return {
                "version": tag.lstrip("v"),
                "url": data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases"),
                "zipball": data.get("zipball_url", ""),
            }
    except Exception as e:
        log.warning("Update check failed: %s", e)
    return None


# ── App ───────────────────────────────────────────────────────────────────────

class MacBlueApp(rumps.App):

    LABEL_CONNECT    = "Connect devices"
    LABEL_DISCONNECT = "Disconnect devices"

    def __init__(self):
        super().__init__(name="macblue", title="", icon=ICON_PATH, quit_button=None)
        self.template = False
        self._fix_icon_size()
        _migrate_from_config_json()

        # State
        self._busy           = False
        self._pending_result = None   # (success: bool, msg: str) or None

        # Menu items
        self.header_item     = rumps.MenuItem(f"macblue  v{VERSION}")
        self.header_item.set_callback(None)
        self.connect_item    = rumps.MenuItem(self.LABEL_CONNECT,    callback=self.on_connect)
        self.disconnect_item = rumps.MenuItem(self.LABEL_DISCONNECT, callback=self.on_disconnect)
        self.register_item   = rumps.MenuItem("Register devices…",   callback=self.on_register_devices, key="r")

        # Devices section header (disabled label)
        self.devices_header = rumps.MenuItem("Registered devices")
        self.devices_header.set_callback(None)

        self.menu = [
            self.header_item,
            rumps.separator,
            self.connect_item,
            self.disconnect_item,
            rumps.separator,
            self.register_item,
            rumps.MenuItem("Check for Updates…", callback=self.on_check_update, key="u"),
            rumps.MenuItem("Logs…",              callback=self.on_logs, key="l"),
            rumps.MenuItem("Help Center",        callback=self.on_help),
            rumps.separator,
            self.devices_header,
            # device items inserted dynamically below
            rumps.separator,
            rumps.MenuItem("Quit",               callback=self.on_quit, key="q"),
        ]

        self._refresh_device_menu()

        log.info("macblue v%s started — base=%s", VERSION, BASE_DIR)

        if not has_devices():
            self._fire_once(self._notify_setup, delay=1)

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _fix_icon_size(self):
        """Force NSImage to 22x22pt so macOS renders it correctly in the menu bar."""
        try:
            from AppKit import NSSize
            ns_image = self._icon
            if ns_image is not None:
                ns_image.setSize_(NSSize(22, 22))
        except Exception:
            pass

    @staticmethod
    def _fire_once(fn, delay=0.1):
        """Schedule fn on the main thread, exactly once."""
        def _wrapper(timer):
            timer.stop()
            fn()
        rumps.Timer(_wrapper, delay).start()

    def _notify(self, msg):
        rumps.notification(title="macblue", subtitle=msg, message="", sound=False)

    def _notify_setup(self):
        rumps.notification(
            title="macblue", subtitle="No devices registered yet",
            message="Click 'Register devices…' in the menu bar.", sound=True,
        )

    def _refresh_device_menu(self):
        """Update the registered devices section in the menu."""
        # Remove old device items (they have keys starting with "_dev_")
        keys_to_remove = [k for k in self.menu.keys() if str(k).startswith("  ")]
        for k in keys_to_remove:
            del self.menu[k]

        devices = load_devices()
        if not devices:
            item = rumps.MenuItem("  No devices registered")
            item.set_callback(None)
            self.menu.insert_after(self.devices_header.title, item)
        else:
            for i, dev in enumerate(reversed(devices)):
                item = rumps.MenuItem(f"  {dev['name']}")
                item.set_callback(None)
                self.menu.insert_after(self.devices_header.title, item)

    # ── Register Devices ──────────────────────────────────────────────────────

    def on_register_devices(self, _):
        try:
            devices = get_paired_devices()
        except RuntimeError as e:
            log.error("Scan failed: %s", e)
            rumps.alert(title="Cannot scan devices", message=str(e))
            return

        if not devices:
            rumps.alert(
                title="No paired devices",
                message="No Bluetooth devices are paired with this Mac.\n\n"
                        "Pair via System Settings → Bluetooth first.",
            )
            return

        devices = devices[:20]
        current = load_devices()
        current_addrs = {d["address"] for d in current}

        selected = self._device_picker(devices, current_addrs)
        if selected is None:
            return
        if not selected:
            rumps.alert(title="Nothing selected", message="Select at least one device.")
            return

        chosen = []
        for i in selected:
            addr = devices[i].get("address", "")
            if not MAC_RE.match(addr):
                log.warning("Skipped invalid address: %s", addr)
                continue
            chosen.append({"name": devices[i].get("name") or "Unknown", "address": addr})

        if not chosen:
            rumps.alert(title="No valid devices", message="Selected devices have invalid addresses.")
            return

        save_devices(chosen)
        self._refresh_device_menu()

        log.info("Devices registered: %s", [d["name"] for d in chosen])
        names = "\n".join(f"  • {d['name']}" for d in chosen)
        rumps.alert(title="Devices registered!", message=f"{names}")

    @staticmethod
    def _device_picker(devices, pre_selected_addrs):
        from AppKit import NSAlert, NSAlertFirstButtonReturn, NSButton, NSMakeRect, NSView

        alert = NSAlert.alloc().init()
        alert.setMessageText_("Register Devices")
        alert.setInformativeText_("Select the devices you want macblue to manage:")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")

        row_h, gap, w = 24, 6, 340
        h = len(devices) * (row_h + gap) + gap
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        cbs = []
        for i, dev in enumerate(devices):
            y    = h - (i + 1) * (row_h + gap)
            name = dev.get("name") or "Unknown"
            addr = dev.get("address", "")
            tag  = "  ✓ connected" if dev.get("connected") else ""
            btn  = NSButton.alloc().initWithFrame_(NSMakeRect(0, y, w, row_h))
            btn.setButtonType_(3)
            btn.setTitle_(f"{name}{tag}")
            # Pre-check devices that are already registered
            btn.setState_(1 if addr in pre_selected_addrs else 0)
            view.addSubview_(btn)
            cbs.append(btn)

        alert.setAccessoryView_(view)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return None
        return [i for i, cb in enumerate(cbs) if cb.state() == 1]

    # ── Connect / Disconnect ──────────────────────────────────────────────────

    def on_connect(self, _):
        self._start_action(CONNECT_SCRIPT, "Devices connected", "connect")

    def on_disconnect(self, _):
        self._start_action(DISCONNECT_SCRIPT, "Devices disconnected", "disconnect")

    def _start_action(self, script, success_msg, action):
        if self._busy:
            return

        # Pre-flight checks
        try:
            get_blueutil()
        except RuntimeError as e:
            rumps.alert(title="blueutil missing", message=str(e))
            return
        if not has_devices():
            rumps.alert(title="No devices", message="Click 'Register devices…' first.")
            return
        if not script.exists():
            rumps.alert(title="Script missing", message=f"{script.name} not found.\nReinstall macblue.")
            return

        # Enter busy state
        self._busy           = True
        self._pending_result = None

        if action == "connect":
            self.connect_item.title    = "Connecting…"
        else:
            self.disconnect_item.title = "Disconnecting…"

        # Run script in background
        threading.Thread(target=self._run_script, args=(script, success_msg), daemon=True).start()

        # Poll for result on main thread (every 0.3s)
        self._poll = rumps.Timer(self._check_done, 0.3)
        self._poll.start()

    def _run_script(self, script, success_msg):
        """Background thread — do NOT touch UI here. Only set _pending_result."""
        try:
            blueutil = get_blueutil()
            devices = load_devices()

            # Build args: script blueutil addr1 name1 addr2 name2 ...
            cmd = ["bash", str(script), blueutil]
            for dev in devices:
                cmd.extend([dev["address"], dev["name"]])

            log.info("Running %s", script.name)
            r = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", timeout=180,
            )
            if r.returncode == 0:
                log.info("%s succeeded", script.name)
                self._pending_result = (True, success_msg)
            else:
                lines = (r.stderr or r.stdout).strip().splitlines()
                raw   = (lines[-1] if lines else "Unknown error").replace("[macblue] ", "").strip()
                log.warning("%s failed: %s", script.name, raw)
                self._pending_result = (False, raw)
        except subprocess.TimeoutExpired:
            log.error("%s timed out", script.name)
            self._pending_result = (False, "Timed out — check that devices are in range.")
        except Exception as e:
            log.exception("Error in %s: %s", script.name, e)
            self._pending_result = (False, "Unexpected error. See macblue.log.")

    def _check_done(self, timer):
        """Main-thread poll. Fires every 0.3s until _pending_result is set."""
        if self._pending_result is None:
            return

        timer.stop()
        success, msg = self._pending_result
        self._pending_result = None
        self._busy = False

        self.connect_item.title    = self.LABEL_CONNECT
        self.disconnect_item.title = self.LABEL_DISCONNECT

        self._notify(msg)
        log.info("Action complete: success=%s msg=%s", success, msg)

    # ── Updates ─────────────────────────────────────────────────────────────

    def on_check_update(self, _):
        """Check GitHub for a new release, offer to open the download page."""
        self._pending_update = None
        threading.Thread(target=self._fetch_update, daemon=True).start()
        self._update_poll = rumps.Timer(self._show_update_result, 0.3)
        self._update_poll.start()

    def _fetch_update(self):
        self._pending_update = ("done", check_for_update())

    def _show_update_result(self, timer):
        if self._pending_update is None:
            return
        timer.stop()
        _, update = self._pending_update
        self._pending_update = None

        if update is None:
            rumps.alert(
                title="You're up to date!",
                message=f"macblue v{VERSION} is the latest version.",
            )
            return

        # Check if this is a git repo — if so, offer auto-update
        is_git = (BASE_DIR / ".git").is_dir()

        if is_git:
            resp = rumps.alert(
                title=f"Update available: v{update['version']}",
                message=f"You have v{VERSION}.\n\n"
                        f"This will pull the latest changes and reinstall.",
                ok="Update Now",
                cancel="Later",
            )
            if resp == 1:
                self._notify("Updating macblue…")
                threading.Thread(target=self._run_auto_update, daemon=True).start()
        else:
            resp = rumps.alert(
                title=f"Update available: v{update['version']}",
                message=f"You have v{VERSION}.\n\n"
                        f"Download v{update['version']} and run install.sh to update.",
                ok="Download",
                cancel="Later",
            )
            if resp == 1:
                subprocess.Popen(["open", update["url"]])

    def _run_auto_update(self):
        """Pull latest code and run install.sh in background."""
        try:
            log.info("Auto-update: pulling latest changes...")
            r = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                log.error("git pull failed: %s", r.stderr.strip())
                self._pending_update_result = (False, f"git pull failed:\n{r.stderr.strip()}")
                return

            log.info("Auto-update: running install.sh...")
            r = subprocess.run(
                ["bash", str(BASE_DIR / "install.sh")],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=300,
            )
            if r.returncode == 0:
                log.info("Auto-update: success! App will restart.")
                self._pending_update_result = (True, "Update complete! macblue will restart.")
            else:
                log.error("install.sh failed: %s", r.stderr.strip()[-200:])
                self._pending_update_result = (False, "Install failed. Check logs.")
        except Exception as e:
            log.exception("Auto-update error: %s", e)
            self._pending_update_result = (False, str(e))

    # ── Misc ──────────────────────────────────────────────────────────────────

    def on_logs(self, _):
        if not LOG_PATH.exists():
            rumps.alert(title="No logs yet", message="Logs will appear after the first action.")
            return
        subprocess.Popen(["open", "-a", "Console", str(LOG_PATH)])

    def on_help(self, _):
        subprocess.Popen(["open", "https://github.com/rangeshot/macblue"])

    def on_quit(self, _):
        if self._busy:
            rumps.alert(title="Please wait", message="An operation is in progress.")
            return
        log.info("Quitting.")
        plist = Path.home() / "Library/LaunchAgents/com.macblue.app.plist"
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        rumps.quit_application()


if __name__ == "__main__":
    MacBlueApp().run()
