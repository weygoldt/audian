import os, sys, tempfile
scratch = tempfile.mkdtemp()
mod = sys.argv[1]
if mod == "pyqt5":
    from PyQt5.QtCore import QSettings
else:
    from PySide6.QtCore import QSettings
for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(fmt, scope, os.fspath(scratch))
s = QSettings("probe", "probe")
s.setValue("i", 3)
s.setValue("b", True)
s.setValue("f", 1.5)
s.setValue("none", None)
s.setValue("list", [1, 2])
s.sync()
print("file:", s.fileName())
print(open(s.fileName()).read())
s2 = QSettings("probe", "probe")
for k in ("i", "b", "f", "none", "list"):
    v = s2.value(k, "DEFAULT")
    print(k, repr(v), type(v).__name__)
print("missing int default:", repr(s2.value("nope", 7)), type(s2.value("nope", 7)).__name__)
print("i with int default:", repr(s2.value("i", 7)), type(s2.value("i", 7)).__name__)
print("b with bool default:", repr(s2.value("b", False)), type(s2.value("b", False)).__name__)
try:
    print("b with type=bool:", repr(s2.value("b", False, type=bool)))
except Exception as e:
    print("type= kwarg failed:", type(e).__name__, e)
