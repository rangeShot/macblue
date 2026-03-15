"""
py2app build script for macblue.
Usage: python3 setup.py py2app
"""
from setuptools import setup

VERSION = "1.0.0"

APP = ["app.py"]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "assets/macblue.icns",
    "plist": {
        "CFBundleName": "macblue",
        "CFBundleDisplayName": "macblue",
        "CFBundleIdentifier": "com.macblue.app",
        "CFBundleVersion": VERSION,
        "CFBundleShortVersionString": VERSION,
        "LSUIElement": True,
        "NSBluetoothAlwaysUsageDescription":
            "macblue needs Bluetooth access to connect and disconnect your devices.",
    },
    "packages": ["rumps"],
    "resources": ["assets/icon_menubar.png", "assets/icon_menubar@2x.png"],
}

setup(
    name="macblue",
    version=VERSION,
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
