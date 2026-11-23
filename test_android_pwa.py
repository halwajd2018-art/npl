from fastapi.testclient import TestClient
from app.main import app


def test_pwa_assets_and_security():
    with TestClient(app) as client:
        manifest = client.get('/manifest.webmanifest')
        assert manifest.status_code == 200
        doc = manifest.json()
        assert doc['display'] == 'standalone'
        assert doc['dir'] == 'rtl'
        worker = client.get('/sw.js')
        assert worker.status_code == 200
        assert 'reports/' not in worker.text
        assert "SAFE_ASSETS" in worker.text
        login = client.get('/login')
        assert login.status_code == 200
        assert '/manifest.webmanifest' in login.text
        assert login.headers['Cache-Control'] == 'no-store'
        assert 'frame-ancestors' in login.headers['content-security-policy']
