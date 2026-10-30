"""Independent compatibility exports; APFS clones avoid copying trace data twice.

Apple contract: xnu/bsd/man/man2/clonefile.2. Unlike hard links, a write to
an export cannot change the authoritative checkpoint. Other filesystems copy.
"""

from __future__ import annotations

import ctypes
import errno
import shutil
import sys
from collections.abc import Callable
from functools import lru_cache
from os import fsencode
from pathlib import Path
from typing import cast


@lru_cache(maxsize=1)
def _clonefile() -> Callable[[bytes, bytes, int], int]:
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = library.clonefile
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    function.restype = ctypes.c_int
    return cast(Callable[[bytes, bytes, int], int], function)


def copy_export(source: Path, target: Path) -> None:
    if sys.platform == "darwin":
        # An interrupted prior export may have left a temporary file.
        target.unlink(missing_ok=True)
        if _clonefile()(fsencode(source), fsencode(target), 0) == 0:
            return
        error = ctypes.get_errno()
        if error not in {errno.ENOTSUP, errno.EXDEV, errno.ENOSYS}:
            raise OSError(error, "checkpoint export clone failed", str(target))
    shutil.copyfile(source, target)
