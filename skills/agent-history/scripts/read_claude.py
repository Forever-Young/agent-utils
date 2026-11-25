#!/usr/bin/env python3
"""Read Claude Desktop's Chromium simple-cache JSON responses, offline."""
import argparse
import ctypes
import ctypes.util
import json
from pathlib import Path
import re
import struct
import sys
from urllib.parse import urlsplit

LIMIT = 64 * 1024 * 1024
MAGIC = 0xFCFB6D1BA7725C30


def zstd_decode(data):
    candidates = [ctypes.util.find_library('zstd'), '/opt/homebrew/lib/libzstd.dylib', '/usr/local/lib/libzstd.dylib']
    lib = None
    for candidate in candidates:
        if candidate:
            try:
                lib = ctypes.CDLL(candidate)
                break
            except OSError:
                pass
    if lib is None:
        raise ValueError('zstd library missing; install libzstd (brew install zstd)')
    lib.ZSTD_findFrameCompressedSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.ZSTD_findFrameCompressedSize.restype = ctypes.c_size_t
    lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    lib.ZSTD_decompress.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    size = lib.ZSTD_findFrameCompressedSize(data, len(data))
    if lib.ZSTD_isError(size):
        raise ValueError('invalid zstd frame')
    out = ctypes.create_string_buffer(LIMIT)
    size = lib.ZSTD_decompress(out, LIMIT, data, size)
    if lib.ZSTD_isError(size):
        raise ValueError('zstd decompression failed or response exceeds 64 MiB')
    return out.raw[:size]


def entries(root):
    for file in root.glob('*_0'):
        try:
            with file.open('rb') as stream:
                header = stream.read(24)
                if len(header) != 24 or struct.unpack_from('<Q', header)[0] != MAGIC:
                    continue
                keysize = struct.unpack_from('<I', header, 12)[0]
                if keysize > 16384:
                    continue
                key = stream.read(keysize).decode('utf-8')
                start = key.find('https://claude.ai/')
                if start < 0:
                    continue
                key = key[start:]
                url = urlsplit(key)
                if url.hostname != 'claude.ai':
                    continue
                match = re.fullmatch(r'/api/organizations/([^/]+)/(chat_conversations(?:_v2)?|projects(?:_v2)?)(?:/([^/]+))?', url.path)
                if not match:
                    continue
                org, kind, ident = match.groups()
                if ident and not re.fullmatch(r'[a-f0-9-]{36}', ident):
                    continue
                yield file, key, org, kind, ident, 24 + keysize
        except (OSError, UnicodeError, ValueError):
            continue


def decode(file, offset):
    with file.open('rb') as stream:
        stream.seek(offset)
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError('response exceeds 64 MiB')
    if data.startswith(b'\x28\xb5\x2f\xfd'):
        data = zstd_decode(data)
    elif data.startswith(b'\x1f\x8b'):
        import zlib
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = decoder.decompress(data, LIMIT + 1)
        if len(data) > LIMIT or not decoder.eof:
            raise ValueError('gzip response too large or incomplete')
    # The cache appends stream footers and HTTP metadata after the body.
    return json.JSONDecoder().raw_decode(data.decode('utf-8', errors='replace'))[0]


def collect(root, org_filter=None):
    records = {}
    errors = []
    for file, url, org, kind, ident, offset in entries(root):
        if org_filter and org != org_filter:
            continue
        try:
            value = decode(file, offset)
            rows = [value] if ident else value if isinstance(value, list) else value.get('data', []) if isinstance(value, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                uid = row.get('uuid') or row.get('id')
                if not uid:
                    continue
                category = 'conversation' if kind.startswith('chat') else 'project'
                key = (org, category, uid)
                stamp = file.stat().st_mtime
                item = dict(org=org, kind=category, id=uid, data=row, source=str(file), cached_at=stamp, detail=bool(ident))
                old = records.get(key)
                if old is None or (item['detail'], stamp) > (old['detail'], old['cached_at']):
                    records[key] = item
        except (OSError, ValueError, TypeError) as exc:
            errors.append({'file': file.name, 'error': str(exc)})
    return list(records.values()), errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path.home() / 'Library/Application Support/Claude/Cache/Cache_Data')
    parser.add_argument('--org', help='restrict organization UUID')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    for name in ('conversations', 'projects'):
        p = sub.add_parser(name)
        p.add_argument('--query', default='')
    for name in ('conversation', 'project'):
        p = sub.add_parser(name)
        p.add_argument('id')
    p = sub.add_parser('search')
    p.add_argument('query')
    args = parser.parse_args()
    if not args.cache.is_dir():
        parser.exit(1, 'Claude cache directory not found. Use --cache PATH.\n')
    records, errors = collect(args.cache, args.org)
    envelope = {'coverage': 'Local cached responses only; may be incomplete or stale.', 'decode_errors': errors}
    if args.command == 'status':
        envelope['counts'] = {kind: sum(r['kind'] == kind for r in records) for kind in ('conversation', 'project')}
        envelope['detail_counts'] = {kind: sum(r['kind'] == kind and r['detail'] for r in records) for kind in ('conversation', 'project')}
    elif args.command in ('conversation', 'project'):
        envelope['records'] = [r for r in records if r['kind'] == args.command and r['id'] == args.id]
        if not envelope['records']:
            envelope['missing'] = 'Not cached. Open this item in Claude Desktop, then retry.'
        elif args.command == 'conversation' and not any('chat_messages' in r['data'] for r in envelope['records']):
            envelope['missing'] = 'Only metadata cached. Open the conversation in Claude Desktop, then retry.'
    else:
        query = args.query.casefold()
        result = []
        for r in records:
            if args.command != 'search' and r['kind'] != args.command[:-1]:
                continue
            searchable = json.dumps(r['data'], ensure_ascii=False) if args.command == 'search' else str(r['data'].get('name', ''))
            if query not in searchable.casefold():
                continue
            item = {k: v for k, v in r.items() if k != 'data'}
            item['name'] = r['data'].get('name')
            item['updated_at'] = r['data'].get('updated_at')
            if args.command == 'search':
                pos = searchable.casefold().find(query)
                item['excerpt'] = searchable[max(0, pos-100):pos+300]
            result.append(item)
        envelope['records'] = sorted(result, key=lambda r: r['cached_at'], reverse=True)
    print(json.dumps(envelope, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
