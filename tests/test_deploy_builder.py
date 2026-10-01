from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("ask_lizheng_deployment", Path(__file__).resolve().parents[1] / "scripts" / "deploy_builder.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)
COMMIT = "a" * 40


def prohibit_network(*args, **kwargs):
    pytest.fail("A dry run or failed approval must not make a network call")


def test_default_is_dry_run_without_credentials_or_network(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "request_json", prohibit_network)
    class NoEnvironment:
        def get(self, *args):
            pytest.fail("Dry run must not read any environment credential")
    monkeypatch.setattr(deploy, "os", SimpleNamespace(environ=NoEnvironment()))
    assert deploy.main([]) == 0
    output = capsys.readouterr().out
    assert '"mode": "dry-run"' in output
    assert deploy.DEPLOYMENTS_URL in output and deploy.PUBLIC_URL in output
    assert '"AI_MODEL": "deepseek-v4-flash"' in output
    assert '"ASK_QUERY_LOG_ENABLED": "false"' in output
    assert '"ASK_QUOTA_ENABLED": "false"' in output
    assert "AI_BUILDER_TOKEN" not in output


def test_enable_quota_is_reviewable_without_credentials_or_network(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "request_json", prohibit_network)
    class NoEnvironment:
        def get(self, *args):
            pytest.fail("Quota dry run must not read any environment credential")
    monkeypatch.setattr(deploy, "os", SimpleNamespace(environ=NoEnvironment()))
    assert deploy.main(["--enable-quota", "--expected-commit", COMMIT]) == 0
    output = capsys.readouterr().out
    assert '"ASK_QUOTA_ENABLED": "true"' in output
    assert deploy.QUOTA_STORE_URL in output
    assert "ASK_ADMISSION_SECRET" not in output and "ASK_QUOTA_STORE_SECRET" not in output
    review = deploy.build_review(expected_commit=COMMIT, enable_quota=True)
    assert review["payload"]["env_vars"] == {
        "AI_MODEL": "deepseek-v4-flash", "ASK_QUOTA_ENABLED": "true", "ASK_QUOTA_STORE_ORIGIN": deploy.QUOTA_STORE_URL,
        "ASK_QUERY_LOG_ENABLED": "false",
        "ASK_OPS_ENABLED": "false",
    }
    assert deploy.approval_digest(review) != deploy.approval_digest(deploy.build_review(expected_commit=COMMIT))


def test_disabled_review_cannot_authorize_enabled_quota(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "request_json", prohibit_network)
    class NoEnvironment:
        def get(self, *args):
            pytest.fail("Changed quota configuration must not read credentials")
    monkeypatch.setattr(deploy, "os", SimpleNamespace(environ=NoEnvironment()))
    approved = deploy.approval_digest(deploy.build_review(expected_commit=COMMIT))
    assert deploy.main(["--enable-quota", "--expected-commit", COMMIT, "--approved-sha", approved]) == 2
    assert "digest mismatch" in capsys.readouterr().err


def test_logging_requires_quota_and_separate_approval(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "request_json", prohibit_network)
    with pytest.raises(deploy.DeploymentError):
        deploy.build_review(enable_query_log=True)
    approved = deploy.approval_digest(deploy.build_review(expected_commit=COMMIT, enable_quota=True))
    assert deploy.main(["--enable-quota", "--enable-query-log", "--expected-commit", COMMIT,
                        "--approved-sha", approved]) == 2
    assert "digest mismatch" in capsys.readouterr().err
    review = deploy.build_review(expected_commit=COMMIT, enable_quota=True, enable_query_log=True)
    assert review["payload"]["env_vars"]["ASK_QUERY_LOG_ENABLED"] == "true"


def test_ops_requires_quota_logging_and_separate_exact_approval(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "request_json", prohibit_network)
    for options in [{}, {"enable_quota": True}, {"enable_query_log": True}]:
        with pytest.raises(deploy.DeploymentError): deploy.build_review(enable_ops=True, **options)
    approved = deploy.approval_digest(deploy.build_review(expected_commit=COMMIT, enable_quota=True, enable_query_log=True))
    class NoEnvironment:
        def get(self, *args): pytest.fail("Changed ops configuration read credentials")
    monkeypatch.setattr(deploy, "os", SimpleNamespace(environ=NoEnvironment()))
    assert deploy.main(["--enable-quota", "--enable-query-log", "--enable-ops", "--expected-commit", COMMIT,
                        "--approved-sha", approved]) == 2
    assert "digest mismatch" in capsys.readouterr().err
    assert deploy.main(["--enable-quota", "--enable-query-log", "--enable-ops", "--expected-commit", COMMIT]) == 0
    review = deploy.build_review(expected_commit=COMMIT, enable_quota=True, enable_query_log=True, enable_ops=True)
    assert review["payload"]["env_vars"]["ASK_OPS_ENABLED"] == "true"
    assert deploy.approval_digest(review) != approved


@pytest.mark.parametrize("config", [
    {"AI_MODEL": "deepseek-v4-flash", "ASK_OPS_ENABLED": "true"},
    {"AI_MODEL": "deepseek-v4-flash", "ASK_OPS_ENABLED": "true", "ASK_QUOTA_ENABLED": "true",
     "ASK_QUOTA_STORE_ORIGIN": deploy.QUOTA_STORE_URL, "ASK_QUERY_LOG_ENABLED": "false"},
    {"AI_MODEL": "deepseek-v4-flash", "ASK_OPS_ENABLED": "yes"},
])
def test_private_or_uncoordinated_ops_configuration_is_rejected(config):
    review = deploy.build_review()
    review["payload"]["env_vars"] = config
    with pytest.raises(deploy.DeploymentError): deploy.validate_payload(review["payload"])


def test_model_change_requires_its_own_review(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "request_json", prohibit_network)
    approved = deploy.approval_digest(deploy.build_review(expected_commit=COMMIT, model="grok-4.5"))
    assert deploy.main(["--expected-commit", COMMIT, "--approved-sha", approved]) == 2
    assert "digest mismatch" in capsys.readouterr().err


@pytest.mark.parametrize("configuration", [
    {"AI_MODEL": "grok-4.5", "ASK_QUOTA_ENABLED": "true"},
    {"AI_MODEL": "grok-4.5", "ASK_QUOTA_ENABLED": "true", "ASK_QUOTA_STORE_ORIGIN": deploy.QUOTA_STORE_URL + "/"},
    {"AI_MODEL": "grok-4.5", "ASK_QUOTA_ENABLED": "true", "ASK_QUOTA_STORE_ORIGIN": "https://elsewhere.example"},
    {"AI_MODEL": "grok-4.5", "ASK_QUOTA_ENABLED": "true", "ASK_QUOTA_STORE_ORIGIN": deploy.QUOTA_STORE_URL, "ASK_ADMISSION_SECRET": "SYNTHETIC_PRIVATE_SENTINEL"},
    {"AI_MODEL": "grok-4.5", "ASK_QUOTA_ENABLED": "true", "ASK_QUOTA_STORE_ORIGIN": deploy.QUOTA_STORE_URL, "ASK_QUOTA_STORE_SECRET": "SYNTHETIC_PRIVATE_SENTINEL"},
    {"AI_MODEL": "grok-4.5", "ASK_QUOTA_REDIS_REST_TOKEN": "SYNTHETIC_PRIVATE_SENTINEL"},
])
def test_deploy_rejects_private_or_unreviewed_quota_configuration(configuration):
    payload = deploy.build_review()["payload"]
    payload["env_vars"] = configuration
    with pytest.raises(deploy.DeploymentError) as error:
        deploy.validate_payload(payload)
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(error.value)


def test_approval_mismatch_blocks_all_network_and_token_reads(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "request_json", prohibit_network)
    class NoEnvironment:
        def get(self, *args):
            pytest.fail("Mismatched approval must not read a credential")
    monkeypatch.setattr(deploy, "os", SimpleNamespace(environ=NoEnvironment()))
    assert deploy.main(["--approved-sha", "f" * 64]) == 2
    assert "digest mismatch" in capsys.readouterr().err


def test_approval_digest_covers_destination_repo_and_commit():
    review = deploy.build_review()
    digest = deploy.approval_digest(review)
    assert len(digest) == 64
    assert digest != deploy.approval_digest(deploy.build_review(expected_commit=COMMIT))
    assert digest != deploy.approval_digest(deploy.build_review("https://github.com/sunyuzheng/ask-lizheng-preview"))
    review["audience"] = "another destination"
    assert digest != deploy.approval_digest(review)


def test_matching_digest_without_commit_is_not_executable(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "request_json", prohibit_network)
    class NoEnvironment:
        def get(self, *args): pytest.fail("Missing commit must not read credentials")
    monkeypatch.setattr(deploy, "os", SimpleNamespace(environ=NoEnvironment()))
    assert deploy.main(["--approved-sha", deploy.approval_digest(deploy.build_review())]) == 2
    output = capsys.readouterr()
    assert '"execution_ready": false' in output.out
    assert "--expected-commit" in output.err


def test_changed_commit_prevents_even_credential_read(monkeypatch, capsys):
    class NoEnvironment:
        def get(self, *args): pytest.fail("Changed commit must not read credentials")
    calls = []
    def remote(method, url, *args, **kwargs):
        calls.append(method)
        return {"name": "main", "commit": {"sha": "b" * 40}} if "/branches/" in url else {"private": False, "html_url": deploy.DEFAULT_REPO}
    monkeypatch.setattr(deploy, "os", SimpleNamespace(environ=NoEnvironment()))
    monkeypatch.setattr(deploy, "request_json", remote)
    review = deploy.build_review(expected_commit=COMMIT)
    assert deploy.main(["--expected-commit", COMMIT, "--approved-sha", deploy.approval_digest(review)]) == 2
    assert calls == ["GET", "GET"]
    assert "approved commit" in capsys.readouterr().err


@pytest.mark.parametrize("repo_url", ["http://github.com/sunyuzheng/ask-lizheng", "https://token@github.com/sunyuzheng/ask-lizheng", "https://github.com/sunyuzheng/ask-lizheng?token=forbidden", "https://github.com/sunyuzheng/ask-lizheng/tree/main", "https://example.org/sunyuzheng/ask-lizheng"])
def test_invalid_repo_targets_are_not_reviewable(repo_url):
    with pytest.raises(deploy.DeploymentError):
        deploy.build_review(repo_url)


@pytest.mark.parametrize("field,value", [("env_vars", {"AI_BUILDER_TOKEN": "SYNTHETIC_PRIVATE_SENTINEL"}), ("env_vars", {"AI_MODEL": "gpt-5", "SECRET": "SYNTHETIC_PRIVATE_SENTINEL"}), ("service_name", "Other-Service"), ("service_name", "other-service"), ("branch", "other-branch")])
def test_secret_config_and_other_service_rejected_without_echo(field, value):
    payload = deploy.build_review()["payload"]
    payload[field] = value
    with pytest.raises(deploy.DeploymentError) as error:
        deploy.validate_payload(payload)
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(error.value)


def test_branch_lock_prevents_post_on_changed_commit(monkeypatch):
    calls = []
    def remote(method, url, *args, **kwargs):
        calls.append(method)
        return {"private": False, "html_url": deploy.DEFAULT_REPO} if "/branches/" not in url else {"name": "main", "commit": {"sha": "b" * 40}}
    monkeypatch.setattr(deploy, "request_json", remote)
    with pytest.raises(deploy.DeploymentError, match="approved commit"):
        deploy.deploy(deploy.build_review(expected_commit=COMMIT), "SYNTHETIC_PRIVATE_SENTINEL")
    assert calls == ["GET", "GET"]


def test_private_repository_prevents_post(monkeypatch):
    monkeypatch.setattr(deploy, "request_json", lambda *args, **kwargs: {"private": True, "html_url": deploy.DEFAULT_REPO})
    with pytest.raises(deploy.DeploymentError, match="public repository"):
        deploy.deploy(deploy.build_review(expected_commit=COMMIT), "SYNTHETIC_PRIVATE_SENTINEL")


def test_existing_binding_prevents_wrong_repository_post(monkeypatch):
    calls = []
    def remote(method, url, *args, **kwargs):
        calls.append(method)
        if url.startswith("https://api.github.com"):
            return {"name": "main", "commit": {"sha": COMMIT}} if "/branches/" in url else {"private": False, "html_url": deploy.DEFAULT_REPO}
        return {"service_name": deploy.SERVICE, "repo_url": "https://github.com/sunyuzheng/something-else"}
    monkeypatch.setattr(deploy, "request_json", remote)
    with pytest.raises(deploy.DeploymentError, match="different repository"):
        deploy.deploy(deploy.build_review(expected_commit=COMMIT), "SYNTHETIC_PRIVATE_SENTINEL")
    assert calls == ["GET", "GET", "GET"]


def test_approved_deploy_uses_exact_payload_no_token_output(monkeypatch, capsys):
    calls = []
    token = "SYNTHETIC_PRIVATE_SENTINEL"
    review = deploy.build_review(expected_commit=COMMIT)
    def remote(method, url, payload=None, token="", timeout=20):
        calls.append((method, url, payload))
        if url.startswith("https://api.github.com"):
            assert not token
            return {"name": "main", "commit": {"sha": COMMIT}} if "/branches/" in url else {"private": False, "html_url": deploy.DEFAULT_REPO}
        if method == "GET" and len(calls) == 3:
            raise deploy.RemoteError(404)
        return {"service_name": deploy.SERVICE, "status": "HEALTHY", "git_commit_id": COMMIT, "public_url": deploy.PUBLIC_URL}
    monkeypatch.setattr(deploy, "request_json", remote)
    monkeypatch.setenv("AI_BUILDER_TOKEN", token)
    assert deploy.main(["--expected-commit", COMMIT, "--approved-sha", deploy.approval_digest(review)]) == 0
    output = capsys.readouterr()
    assert token not in output.out + output.err
    assert output.out.strip().endswith(deploy.PUBLIC_URL)
    assert [call[0] for call in calls] == ["GET", "GET", "GET", "POST"]
    assert calls[-1][2] == review["payload"]


def test_optional_logs_are_build_only_and_redacted(monkeypatch, capsys):
    token = "SYNTHETIC_PRIVATE_SENTINEL"
    def remote(method, url, *args, **kwargs):
        assert "log_type=build" in url and "runtime" not in url
        return {"log_type": "build", "logs": "Build started\nAuthorization: Bearer " + token + "\nAI_BUILDER_TOKEN=another-sensitive-value\nBuild finished"}
    monkeypatch.setattr(deploy, "request_json", remote)
    deploy.show_build_logs(token)
    output = capsys.readouterr().err
    assert token not in output and "another-sensitive-value" not in output
    assert "Build started" in output and "Build finished" in output
