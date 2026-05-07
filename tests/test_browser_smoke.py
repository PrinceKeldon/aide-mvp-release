from fastapi.testclient import TestClient


def test_mvp_pages_return_200(monkeypatch):
    from interface import web

    monkeypatch.setattr(web, "onboarding_complete", lambda: True)

    client = TestClient(web.app)

    for path in ["/onboarding", "/settings", "/", "/your-day", "/finance"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.text.strip()
