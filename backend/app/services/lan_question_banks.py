"""Explicit, atomic ESQ publication. Only generated snapshots are addressable."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException

from .. import database
from . import esq

LOCK = threading.RLock()
PUBLISH_LOCK = threading.Lock()
_generation = 0
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024


def root() -> Path:
    return database.DATABASE_PATH.parent / 'lan-question-banks'


def state() -> dict:
    path = root() / 'catalog.json'
    if not path.exists():
        return {'catalogVersion': 1, 'enabled': False, 'profileIds': [], 'packages': []}
    return json.loads(path.read_text(encoding='utf-8'))


def choices(connection) -> list[dict]:
    return [dict(row) for row in connection.execute(
        "SELECT b.id, b.name, count(p.id) AS papers FROM question_bank_profiles b "
        "JOIN papers p ON p.profile_id=b.id AND p.status='published' AND p.deleted_at IS NULL "
        "WHERE b.deleted_at IS NULL GROUP BY b.id ORDER BY b.name,b.id")]


def canonical_package(data: bytes, package_id: str, title: str) -> tuple[bytes, dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        if len(data) > MAX_PACKAGE_BYTES or sum(i.file_size for i in source.infolist()) > MAX_SNAPSHOT_BYTES:
            raise HTTPException(422, 'Shared package exceeds the size limit')
        entries = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(entries.pop('manifest.json'))
    manifest.update(packageId=package_id, contentVersion='1.0.0', title=title)
    def encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    for name in entries:
        if name.endswith('.json'):
            entries[name] = encode(json.loads(entries[name]))
    # Content identity survives republishing the same bank from another host.
    digest = hashlib.sha256(encode({k: v for k, v in manifest.items() if k not in ('packageId', 'title')}))
    for name, value in sorted(entries.items()):
        digest.update(name.encode('utf-8') + b'\0' + hashlib.sha256(value).digest())
    manifest['contentVersion'] = '1.0.0+' + digest.hexdigest()
    entries['manifest.json'] = encode(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    return output.getvalue(), manifest


def publish(connection, enabled: bool, profile_ids: list[int]) -> dict:
    global _generation
    with LOCK:
        _generation += 1
        generation = _generation
        previous = state()
        if not enabled:
            result = {**previous, 'enabled': False}
            root().mkdir(parents=True, exist_ok=True)
            _atomic_write(root() / 'catalog.json', json.dumps(result, ensure_ascii=False).encode('utf-8'))
            return result
    # Export never holds the authorization lock; disable/revocation remain responsive.
    with PUBLISH_LOCK:
        if enabled:
            available = {item['id']: item for item in choices(connection)}
            if not profile_ids or len(set(profile_ids)) != len(profile_ids) or any(i not in available for i in profile_ids):
                raise HTTPException(422, 'Select existing published question banks')
            root().mkdir(parents=True, exist_ok=True)
            packages = []
            total = 0
            for profile_id in sorted(profile_ids):
                key = 'lan_question_bank_id:' + str(profile_id)
                saved = connection.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
                package_id = saved['value'] if saved else 'lan.bank.' + uuid.uuid4().hex
                if not saved:
                    connection.execute('INSERT INTO app_settings(key,value) VALUES (?,?)', (key, package_id))
                    connection.commit()
                data, _ = esq.export_package(connection, profile_id=profile_id)
                data, manifest = canonical_package(data, package_id, available[profile_id]['name'])
                total += len(data)
                if len(data) > MAX_PACKAGE_BYTES or total > MAX_SNAPSHOT_BYTES:
                    raise HTTPException(422, 'Shared snapshot exceeds the size limit')
                if shutil.disk_usage(root()).free < len(data) + 64 * 1024 * 1024:
                    raise HTTPException(507, 'Insufficient sharing storage')
                sha = hashlib.sha256(data).hexdigest()
                target = root() / (sha + '.esq')
                if not target.exists():
                    _atomic_write(target, data)
                packages.append({'packageId': package_id, 'contentVersion': manifest['contentVersion'],
                                 'title': manifest['title'], 'fileName': sha + '.esq',
                                 'sha256': sha, 'size': len(data),
                                 'years': sorted({p['year'] for p in manifest['papers']}),
                                 'license': manifest['license']['notice']})
            result = {'catalogVersion': 1, 'enabled': True, 'profileIds': sorted(profile_ids), 'packages': packages}
        with LOCK:
            if generation != _generation:
                raise HTTPException(409, 'Sharing selection changed during publication; retry')
            root().mkdir(parents=True, exist_ok=True)
            _atomic_write(root() / 'catalog.json', json.dumps(result, ensure_ascii=False).encode('utf-8'))
        return result


def _atomic_write(target: Path, data: bytes) -> None:
    temporary = target.with_name(uuid.uuid4().hex + '.part')
    try:
        with temporary.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def catalog() -> dict:
    current = state()
    if not current['enabled']:
        raise HTTPException(403, 'Question bank sharing is disabled')
    return {'catalogVersion': 1, 'packages': current['packages']}


def package(sha: str) -> tuple[Path, dict]:
    if not re.fullmatch('[a-f0-9]{64}', sha):
        raise HTTPException(422, 'Invalid package digest')
    item = next((p for p in catalog()['packages'] if p['sha256'] == sha), None)
    if item is None:
        raise HTTPException(409, 'Shared snapshot changed; refresh the catalog')
    return root() / (sha + '.esq'), item
