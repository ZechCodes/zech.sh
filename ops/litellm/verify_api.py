"""Host-only smoke test; credentials are read locally and never printed."""
import json
import pathlib
import subprocess
import time
import http.client
import ipaddress

container = json.loads(subprocess.check_output(['docker', 'inspect', 'litellm-litellm-1']))[0]
ip = next(iter(container['NetworkSettings']['Networks'].values()))['IPAddress']
ipaddress.ip_address(ip)
env = dict(item.split('=', 1) for item in container['Config']['Env'])
key = env['LITELLM_MASTER_KEY']

def request(path, payload=None, authenticated=True):
    headers = {'Content-Type': 'application/json'}
    if authenticated:
        headers['Authorization'] = 'Bearer ' + key
    body = json.dumps(payload).encode() if payload is not None else None
    assert path in ('/health/liveliness', '/health/readiness', '/v1/models', '/v1/chat/completions')
    connection = http.client.HTTPConnection(ip, 4000, timeout=60)
    connection.request('POST' if body is not None else 'GET', path, body=body, headers=headers)
    return connection.getresponse()

for attempt in range(30):
    try:
        with request('/health/liveliness'):
            break
    except (OSError, http.client.HTTPException):
        if attempt == 29:
            raise
        time.sleep(2)

for path in ('/health/liveliness', '/health/readiness'):
    with request(path) as response:
        assert response.status == 200
        print('PASS', path)
with request('/v1/models', authenticated=False) as response:
    assert response.status in (401, 403), 'Unauthenticated model listing allowed'
    print('PASS unauthenticated access rejected')
with request('/v1/models') as response:
    models = json.load(response)['data']
    assert models
    print('PASS authenticated model listing:', len(models), 'models')
if '--stream' in __import__('sys').argv:
    candidates = [model['id'] for model in models]
    model = 'openai/gpt-4o-mini' if 'openai/gpt-4o-mini' in candidates else candidates[0]
    payload = {'model': model, 'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'stream': True, 'max_tokens': 8}
    with request('/v1/chat/completions', payload) as response:
        lines = response.read(65536).decode()
        assert 'data:' in lines and '[DONE]' in lines, 'Incomplete stream'
        assert '"error"' not in lines, 'Stream returned an error'
    print('PASS authenticated streaming completion')
