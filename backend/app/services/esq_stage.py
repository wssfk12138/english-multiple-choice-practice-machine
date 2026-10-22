"""Complete uploads survive parsing failures and restarts; drafts stay explicit."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException
from ..database import get_active_profile_id
from .esq import MAX_PACKAGE_BYTES, EsqValidationError, load_esq_package


def migrate(db):
    db.execute("CREATE TABLE IF NOT EXISTS esq_import_stages(id TEXT PRIMARY KEY,filename TEXT NOT NULL,profile_id INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'receiving',archive_bytes INTEGER NOT NULL DEFAULT 0,archive_hash TEXT NOT NULL DEFAULT '',job_id INTEGER UNIQUE,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    # Only parsing is restarted. Fully uploaded archives are never auto-parsed.
    db.execute("UPDATE esq_import_stages SET state='uploaded' WHERE state='parsing' AND job_id IS NULL")
    db.execute("INSERT OR IGNORE INTO app_migrations(migration_key) VALUES('esq-staging-v1')")


def stage_path(upload_root: Path, stage_id: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', stage_id):
        raise HTTPException(400, '无效的暂存编号')
    root = (upload_root / 'esq-stages').resolve()
    original = root / stage_id
    target = original.resolve()
    if target.parent != root or target != original or original.is_symlink():
        raise HTTPException(400, '无效的暂存路径')
    return target


def record(db, stage_id):
    row = db.execute("SELECT * FROM esq_import_stages WHERE id=? AND state<>'discarded'", (stage_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '暂存任务不存在')
    return dict(row)


async def upload(db, upload_root, file, profile_id=None):
    filename = str(file.filename or '').replace('\\', '/').rsplit('/', 1)[-1][:255]
    if Path(filename).suffix.lower() not in {'.esq', '.zip'}:
        raise HTTPException(422, '请选择 .esq 或 .zip 题库包')
    profile = profile_id or get_active_profile_id(db)
    if not db.execute('SELECT 1 FROM question_bank_profiles WHERE id=? AND deleted_at IS NULL', (profile,)).fetchone():
        raise HTTPException(422, '目标题库配置不存在')
    stage_id = uuid.uuid4().hex
    directory = stage_path(upload_root, stage_id)
    directory.mkdir(parents=True)
    db.execute('INSERT INTO esq_import_stages(id,filename,profile_id) VALUES(?,?,?)', (stage_id, filename, profile))
    db.commit()
    size, digest = 0, hashlib.sha256()
    try:
        with (directory / 'upload.part').open('xb') as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_PACKAGE_BYTES:
                    raise HTTPException(413, 'ESQ 文件不能超过 2 GiB，请重新选择')
                digest.update(chunk)
                handle.write(chunk)
            if not size:
                raise HTTPException(422, '文件为空，请重新选择')
            handle.flush()
            os.fsync(handle.fileno())
        # The final filename is itself the durable upload-complete marker.
        os.replace(directory / 'upload.part', directory / 'archive.esq')
        db.execute("UPDATE esq_import_stages SET state='uploaded',archive_bytes=?,archive_hash=? WHERE id=?", (size, digest.hexdigest(), stage_id))
        db.commit()
        return stage_id
    except BaseException:
        db.rollback()
        # An incomplete part never masquerades as a resumable complete archive.
        raise


def pending(db, upload_root):
    result = []
    for row in db.execute("SELECT * FROM esq_import_stages WHERE job_id IS NULL AND state<>'discarded' ORDER BY created_at DESC,id"):
        directory = stage_path(upload_root, row['id'])
        archive = directory / 'archive.esq'
        complete = archive.is_file()
        result.append({'stage_id':row['id'], 'filename':row['filename'], 'profile_id':row['profile_id'], 'phase':row['state'] if complete else 'incomplete', 'ready':False, 'archive_bytes':archive.stat().st_size if complete else row['archive_bytes'], 'staged_at':row['created_at'], 'complete':complete})
    return result


def resume(db, upload_root, stage_id):
    from ..routers.question_banks import create_question_bank_import, question_bank_import_detail
    directory = stage_path(upload_root, stage_id)
    row = record(db, stage_id)
    if row['job_id']:
        return question_bank_import_detail(row['job_id'], db)
    archive = directory / 'archive.esq'
    if not archive.is_file():
        raise HTTPException(409, '上传未完整保存，请重新选择原文件')
    db.execute('BEGIN IMMEDIATE')
    changed = db.execute("UPDATE esq_import_stages SET state='parsing' WHERE id=? AND job_id IS NULL AND state IN ('receiving','uploaded','failed')", (stage_id,)).rowcount
    db.commit()
    if not changed:
        row = record(db, stage_id)
        if row['job_id']:
            return question_bank_import_detail(row['job_id'], db)
        raise HTTPException(409, '该题库包正在解析，请等待当前任务结束')
    try:
        digest = hashlib.sha256()
        with archive.open('rb') as handle:
            for chunk in iter(lambda:handle.read(1024 * 1024), b''):
                digest.update(chunk)
        if row['archive_hash'] and digest.hexdigest() != row['archive_hash']:
            raise HTTPException(409, '暂存文件校验不一致，请重新选择原文件')
        package = load_esq_package(archive)
        db.execute('BEGIN IMMEDIATE')
        current = record(db, stage_id)
        if current['state'] != 'parsing' or current['job_id']:
            raise HTTPException(409, '暂存状态已变化，请刷新后继续')
        result = create_question_bank_import(db, archive, row['filename'], row['profile_id'], package=package, commit=False)
        db.execute("UPDATE esq_import_stages SET state='ready',job_id=?,archive_hash=?,archive_bytes=? WHERE id=?", (result['id'], digest.hexdigest(), archive.stat().st_size, stage_id))
        db.commit()
        return result
    except BaseException as error:
        db.rollback()
        db.execute("UPDATE esq_import_stages SET state='failed' WHERE id=? AND job_id IS NULL AND state='parsing'", (stage_id,))
        db.commit()
        if isinstance(error, HTTPException):
            raise
        if isinstance(error, EsqValidationError):
            raise HTTPException(422, {'code':'VALIDATION_ERROR','message':'题库包校验失败，已保留暂存文件','stage_id':stage_id,'details':error.details}) from error
        if isinstance(error, Exception):
            raise HTTPException(422, {'code':'IMPORT_FAILED','message':'解析未完成，暂存文件已保留，可继续或删除','stage_id':stage_id}) from error
        raise


def discard(db, upload_root, stage_id):
    directory = stage_path(upload_root, stage_id)
    db.execute('BEGIN IMMEDIATE')
    try:
        row = record(db, stage_id)
        if row['job_id'] or row['state'] == 'parsing':
            raise HTTPException(409, '已有草稿或正在解析的任务不能作为未完成暂存删除')
        db.execute("UPDATE esq_import_stages SET state='discarded' WHERE id=? AND job_id IS NULL", (stage_id,))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    # The client explicitly selected this one unpublished stage. Resolve again
    # before recursive deletion and reject paths outside its generated directory.
    if directory.parent != (upload_root / 'esq-stages').resolve():
        raise HTTPException(400, '无效的暂存路径')
    if directory.exists():
        shutil.rmtree(directory)
    return {'discarded': True}
