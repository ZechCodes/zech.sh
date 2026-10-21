"""Offline installation of a hash-verified dependency-free wheel using python -S."""
import hashlib
import pathlib
import shutil
import zipfile

wheel = pathlib.Path('/audit/certifi-2026.10.19-py3-none-any.whl')
assert hashlib.sha256(wheel.read_bytes()).hexdigest() == '42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b'
site = pathlib.Path('/app/.venv/lib/python3.13/site-packages')
for old in site.glob('certifi-*.dist-info'):
    shutil.rmtree(old)
shutil.rmtree(site / 'certifi')
with zipfile.ZipFile(wheel) as archive:
    assert all(name.startswith(('certifi/', 'certifi-2026.10.19.dist-info/')) and '..' not in pathlib.PurePosixPath(name).parts for name in archive.namelist())
    archive.extractall(site)
