"""Run using python -S; inspect metadata and validate both independent trust stores."""
import json
import pathlib
import subprocess
import tempfile
import urllib.request
import urllib.error
import sys
import ssl

site = pathlib.Path('/app/.venv/lib/python3.13/site-packages')
assert not any((site / name).exists() for name in ('cook.py', 'cook.pth', 'sitecustomize.py'))
assert 'Version: 1.98.0\n' in (site / 'litellm-1.98.0.dist-info/METADATA').read_text()
assert (site / 'certifi-2026.10.19.dist-info/METADATA').exists(), 'certifi must be patched'
fixtures = json.loads(pathlib.Path('/audit/impacted-intermediates.json').read_text())
for bundle in (site / 'certifi/cacert.pem', pathlib.Path('/etc/ssl/certs/ca-certificates.crt')):
    with tempfile.TemporaryDirectory() as directory:
        for index, item in enumerate(fixtures):
            pem = pathlib.Path(directory) / f'{index}.pem'
            pem.write_text(item['pem'])
            subprocess.run(['openssl', 'x509', '-in', str(pem), '-noout'], check=True, capture_output=True)
            result = subprocess.run(['openssl', 'verify', '-no-CApath', '-no-CAstore', '-CAfile', str(bundle), str(pem)], capture_output=True)
            assert b'verification failed' in result.stderr, result.stderr
            assert result.returncode != 0, f'Compromised CA accepted by {bundle}: {item["name"]} / {item["issued_by"]}'
    context = ssl.create_default_context(cafile=str(bundle))
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    urls = ('https://api.openai.com/',) if '--runtime' in sys.argv else ('https://pypi.org/', 'https://api.openai.com/')
    for url in urls:
        assert url in ('https://pypi.org/', 'https://api.openai.com/')
        try:
            # Fixed HTTPS allowlist plus an explicitly validating TLS context;
            # urllib intentionally preserves production's HTTPS_PROXY/Squid routing.
            with urllib.request.urlopen(url, context=context, timeout=20) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
                assert response.status < 500
        except urllib.error.HTTPError as error:
            assert error.code < 500
    print(f'PASS: {bundle}: rejects all {len(fixtures)} compromised certificates; normal HTTPS succeeds')
print('PASS: LiteLLM 1.98.0 preserved; certifi patched; no incident startup files')
