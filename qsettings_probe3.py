import os, sys
scratch, mod = sys.argv[2], sys.argv[1]
if mod == "pyqt5":
    from PyQt5.QtCore import QSettings, QByteArray
else:
    from PySide6.QtCore import QSettings, QByteArray
for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(fmt, scope, os.fspath(scratch))
s = QSettings("probe", "probe")
for how, fn in (
    ("kwarg type=bool", lambda: s.value("b", False, type=bool)),
    ("pos type bool", lambda: s.value("b", False, bool)),
    ("kwarg type=int", lambda: s.value("i", 0, type=int)),
    ("pos type int", lambda: s.value("i", 0, int)),
    ("bytearray missing", lambda: s.value("geom", QByteArray())),
):
    try:
        v = fn()
        print(f"{how}: {v!r} ({type(v).__name__})")
    except Exception as e:
        print(f"{how}: FAILED {type(e).__name__}: {e}")
