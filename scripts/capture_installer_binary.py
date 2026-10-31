"""Capture Tauri's actual NSIS input without modifying executable bytes."""
import argparse
import hashlib
from pathlib import Path

PORTABLE_MARKER=b'__TAURI_BUNDLE_TYPE_VAR_UNK'
NSIS_MARKER=b'__TAURI_BUNDLE_TYPE_VAR_NSS'


def capture(source,destination,portable_sha256):
    data=Path(source).read_bytes()
    # Tauri CLI 2.11.5 bundle.rs patches this token then restores the original.
    # Normalize in memory only to verify the same compiled input.
    if data.count(NSIS_MARKER)!=1 or PORTABLE_MARKER in data:
        raise ValueError('NSIS输入的官方包类型标记不唯一。')
    if hashlib.sha256(data.replace(NSIS_MARKER,PORTABLE_MARKER)).hexdigest()!=portable_sha256:
        raise ValueError('NSIS输入除官方包类型标记外发生变化。')
    with Path(destination).open('xb')as stream:stream.write(data)
    return hashlib.sha256(data).hexdigest()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path);parser.add_argument('portable_sha256')
    args=parser.parse_args();print(capture(args.source,args.destination,args.portable_sha256))
