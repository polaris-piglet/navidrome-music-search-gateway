"""Linux atomic publication: refuse existing targets; no overwrite fallback."""
import ctypes
import os

def publish_noreplace(source, target):
    lib = ctypes.CDLL(None, use_errno=True)
    fn = lib.renameat2
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), os.fspath(target))
