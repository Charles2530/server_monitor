import struct
import zipfile

import pytest

from scripts.check_android_native import check_apk


def native_apk(tmp_path, alignment, offset=0):
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, 3, 183, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    segment = struct.pack("<IIQQQQQQ", 1, 5, offset, 0, 0, 120, 120, alignment)
    path = tmp_path / "test.apk"
    with zipfile.ZipFile(path, "w") as apk:
        apk.writestr("lib/arm64-v8a/libgojni.so", header + segment)
    return path


def test_rejects_4kb_native_library_even_in_valid_apk(tmp_path):
    with pytest.raises(ValueError, match="not 16 KB aligned"):
        check_apk(native_apk(tmp_path, 4096))


def test_accepts_16kb_native_library(tmp_path):
    assert check_apk(native_apk(tmp_path, 16384)) == 1


def test_rejects_mismatched_offset_and_virtual_address(tmp_path):
    with pytest.raises(ValueError, match="not 16 KB aligned"):
        check_apk(native_apk(tmp_path, 16384, offset=4096))


def test_missing_native_library_cannot_pass(tmp_path):
    path = tmp_path / "empty.apk"
    with zipfile.ZipFile(path, "w"):
        pass
    with pytest.raises(ValueError, match="no native libraries"):
        check_apk(path)
