"""Validate or install an inactive NetworkManager profile on server 100."""

import argparse
import hashlib
import os
import subprocess
from pathlib import Path

import gi

gi.require_version("NM", "1.0")
from gi.repository import NM, GLib  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install", action="store_true", help="Save and load the inactive profile; requires root"
    )
    args = parser.parse_args()
    source = Path(__file__).with_name("marvell-static-192.168.0.100-staged.nmconnection")
    keyfile = GLib.KeyFile.new()
    keyfile.load_from_file(str(source), GLib.KeyFileFlags.NONE)
    connection = NM.keyfile_read(keyfile, str(source.parent), NM.KeyfileHandlerFlags.NONE, None, None)
    connection.verify()
    settings = connection.get_setting_connection()
    assert settings.get_interface_name() == "enp12s0"
    assert not settings.get_autoconnect()
    print(f"Validated: {settings.get_id()}, autoconnect=false", flush=True)
    if not args.install:
        return
    if os.geteuid() != 0:
        raise SystemExit("Installation requires sudo; profile has not been installed.")
    destination = Path("/etc/NetworkManager/system-connections") / source.name
    if destination.exists():
        raise SystemExit(f"Profile already exists; inspect it before installing: {destination}")
    data = source.read_bytes()
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    subprocess.run(["/usr/bin/nmcli", "--wait", "10", "connection", "load", str(destination)], check=True)
    print(f"Installed inactive profile: {destination}")
    print(f"SHA256: {hashlib.sha256(destination.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
