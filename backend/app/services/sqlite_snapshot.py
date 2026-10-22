"""Consistent read snapshots for projections assembled from multiple SELECTs."""
from contextlib import contextmanager


@contextmanager
def read_snapshot(connection):
    connection.execute('SAVEPOINT vocabulary_read_snapshot')
    try:
        yield
    except BaseException:
        connection.execute('ROLLBACK TO vocabulary_read_snapshot')
        raise
    finally:
        connection.execute('RELEASE vocabulary_read_snapshot')
