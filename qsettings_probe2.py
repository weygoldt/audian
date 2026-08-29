import os, sys
scratch = sys.argv[2]
mod = sys.argv[1]
mode = sys.argv[3]
if mod == "pyqt5":
    from PyQt5.QtCore import QSettings
else:
    from PySide6.QtCore import QSettings
for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(fmt, scope, os.fspath(scratch))
s = QSettings("probe", "probe")
if mode == "write":
    s.setValue("i", 3)
    s.setValue("b", True)
    s.setValue("f", 1.5)
    s.setValue("s", "hello")
    s.sync()
    print("wrote:", s.fileName())
    print(open(s.fileName()).read())
else:
    print("read from:", s.fileName())
    for k, d in (("i", 0), ("b", False), ("f", 0.0), ("s", "")):
        v = s.value(k)
        vd = s.value(k, d)
        print(f"{k}: no-default={v!r} ({type(v).__name__})  with-default={vd!r} ({type(vd).__name__})")
    print("int() of b:", end=" ")
    try:
        print(int(s.value("b", False)))
    except Exception as e:
        print("FAILED", type(e).__name__, e)
