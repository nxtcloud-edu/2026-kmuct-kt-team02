from fastapi.testclient import TestClient

from server.config import ConfigurationError, Settings
from server.main import create_app


class FakePolicyCatalog:
    def __init__(self, verified_count: int) -> None:
        self.verified_count = verified_count

    def count_verified(self) -> int:
        return self.verified_count


def settings(*, llm_adapter_name: str | None = None) -> Settings:
    return Settings(
        localhost_cors_origins=("http://localhost:5173",),
        s3_cors_origins=("https://demo-bucket.s3.amazonaws.com",),
        llm_adapter_name=llm_adapter_name,
    )


def test_health_reports_unconnected_policy_dependency_without_secrets() -> None:
    client = TestClient(create_app(settings=settings()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "verified_policy_count": None,
        "policy_dependency": "unconnected",
        "llm_adapter_configured": False,
    }


def test_health_reports_only_connected_dependency_state() -> None:
    client = TestClient(
        create_app(
            settings=settings(llm_adapter_name="claude-adapter"),
            policy_catalog=FakePolicyCatalog(24),
        )
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "verified_policy_count": 24,
        "policy_dependency": "connected",
        "llm_adapter_configured": True,
    }
    assert "claude-adapter" not in response.text


def test_cors_allows_only_configured_exact_origin() -> None:
    client = TestClient(create_app(settings=settings()))

    allowed = client.options(
        "/health",
        headers={
            "Origin": "https://demo-bucket.s3.amazonaws.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = client.options(
        "/health",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == (
        "https://demo-bucket.s3.amazonaws.com"
    )
    assert "access-control-allow-origin" not in denied.headers


def test_settings_reject_wildcard_cors_origin() -> None:
    try:
        Settings.from_env(
            {
                "CORS_LOCALHOST_ORIGINS": "http://localhost:5173",
                "CORS_S3_ORIGINS": "*",
            }
        )
    except ConfigurationError as exc:
        assert "wildcard" in str(exc)
    else:
        raise AssertionError("wildcard CORS origin must be rejected")
