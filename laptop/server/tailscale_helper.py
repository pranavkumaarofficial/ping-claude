#!/usr/bin/env python3
"""
Ping Claude — Tailscale Helper
Detects the local Tailscale IP and generates a QR code for phone pairing.

Dependencies: qrcode[pil]  (pip install "qrcode[pil]")
              Falls back to ASCII QR if Pillow is missing.
"""
from __future__ import annotations

import subprocess
import sys


def get_tailscale_ip() -> str | None:
    """Return the Tailscale IPv4 address or None."""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5,
        )
        ip = result.stdout.strip().split("\n")[0].strip()
        if ip.startswith("100."):
            return ip
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def build_pairing_uri(ip: str, port: int = 8765) -> str:
    return f"pingclaude://{ip}:{port}"


def print_qr_ascii(data: str) -> None:
    """Print a QR code to the terminal using Unicode block characters."""
    try:
        import qrcode
    except ImportError:
        print(f"\n  Pairing URI:  {data}")
        print("  (install 'qrcode' for a scannable QR:  pip install qrcode)\n")
        return

    qr = qrcode.QRCode(box_size=1, border=2,
                        error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print(f"\n  Scan this QR code with the Ping Claude app.")
    print(f"  URI: {data}\n")


def save_qr_image(data: str, path: str = "pairing_qr.png") -> str | None:
    """Save a QR code as a PNG.  Returns the path or None on failure."""
    try:
        import qrcode
        img = qrcode.make(data)
        img.save(path)
        return path
    except Exception:
        return None


# ---------------------------------------------------------------------------

def main() -> None:
    ip = get_tailscale_ip()
    if ip is None:
        print("ERROR: Tailscale not detected.")
        print("Install it:  https://tailscale.com/download")
        print("Then run:    tailscale up")
        sys.exit(1)

    uri = build_pairing_uri(ip)
    print(f"\nTailscale IP: {ip}")
    print_qr_ascii(uri)


if __name__ == "__main__":
    main()
