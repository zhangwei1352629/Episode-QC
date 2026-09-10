"""Spawn-safe workers: never inherit the HTTP server or its SQLite connections."""
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os
import logging


def _lower_priority():
    try:
        if os.name == 'nt':
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00004000):
                raise OSError('SetPriorityClass failed')
        else:
            os.nice(5)
    except OSError:
        logging.getLogger(__name__).warning('Worker priority unchanged', exc_info=True)


def _execute(kind, args, kwargs):
    if kind == 'index':
        from episode_qc.workspace import scan_data_source
        return scan_data_source(*args, **kwargs)
    if kind == 'playback':
        from episode_qc.playback import prepare_episode_cache
        return prepare_episode_cache(*args, **kwargs)
    if kind == 'hash':
        from episode_qc.platform_workflow import sha256_file
        return sha256_file(*args, **kwargs)
    raise ValueError('Unknown worker operation')


class IsolatedWork:
    def __init__(self):
        self._pools = {kind: ProcessPoolExecutor(
            max_workers=1, mp_context=multiprocessing.get_context('spawn'),
            initializer=_lower_priority,
        ) for kind in ('index', 'playback', 'playback_background')}

    def call(self, kind, *args, **kwargs):
        # Caller owns queue admission and per-Episode serialization. Do not
        # silently retry writes in the HTTP process after a worker failure.
        pool = 'index' if kind == 'hash' else kind
        operation = 'playback' if kind == 'playback_background' else kind
        return self._pools[pool].submit(_execute, operation, args, kwargs).result()

    def close(self):
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
