import os, sys
scratch, mod = sys.argv[2], sys.argv[1]
if mod == "pyqt5":
    from PyQt5.QtCore import QSettings
else:
    from PySide6.QtCore import QSettings
print("unscoped NativeFormat:", end=" ")
try:
    print(QSettings.NativeFormat, QSettings.UserScope)
except Exception as e:
    print("FAILED", e)
p = os.path.join(scratch, "probe", "probe.conf")
os.makedirs(os.path.dirname(p), exist_ok=True)
open(p, "w").write("[General]\nb=false\nz=0\nempty=\nweird=notanumber\n")
for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(fmt, scope, os.fspath(scratch))
s = QSettings("probe", "probe")
for k in ("b", "z", "empty", "weird", "absent"):
    for t in (bool, int):
        try:
            v = s.value(k, None, type=t)
            print(f"{k} as {t.__name__}: {v!r} ({type(v).__name__})")
        except Exception as e:
            print(f"{k} as {t.__name__}: FAILED {type(e).__name__}: {e}")
