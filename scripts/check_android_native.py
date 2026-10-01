#!/usr/bin/env python3
"""Reject APK native libraries that cannot be loaded on 16 KB page devices."""
import argparse
import struct
import zipfile


def check_apk(path):
    checked = 0
    with zipfile.ZipFile(path) as apk:
        for name in apk.namelist():
            if not name.startswith("lib/") or not name.endswith(".so"):
                continue
            data = apk.read(name)
            if data[:6] != b"\x7fELF\x02\x01":
                raise ValueError(f"{name}: expected little-endian ELF64")
            header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
            loads = 0
            for index in range(header[10]):
                segment = struct.unpack_from("<IIQQQQQQ", data, header[5] + index * header[9])
                if segment[0] != 1:  # PT_LOAD
                    continue
                loads += 1
                if segment[7] < 16384 or (segment[2] - segment[3]) % 16384:
                    raise ValueError(f"{name}: PT_LOAD {index} is not 16 KB aligned (alignment={segment[7]})")
            if not loads:
                raise ValueError(f"{name}: no loadable segments")
            checked += 1
    if not checked:
        raise ValueError("APK contains no native libraries")
    return checked


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk")
    args = parser.parse_args()
    try:
        count = check_apk(args.apk)
    except (ValueError, struct.error, zipfile.BadZipFile) as error:
        parser.exit(1, f"Native alignment check failed: {error}\n")
    print(f"16 KB ELF alignment verified: {count} native libraries")
