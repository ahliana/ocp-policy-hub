"""Tests for the source-intake validation harness (WP-41, src/sources/intake.py).

No network anywhere - every fetch is an injected fixture callable.
"""

import pytest

from src.sources.intake import (
    Check,
    FetchError,
    FetchResponse,
    RedirectLoopError,
    ValidationResult,
    main,
    validate_candidate,
)

GOOD_HTML = """<html><head><title>Energy Policy</title></head>
<body><h1>Energy Efficiency Directive</h1>
<p>""" + ("This page describes the national energy efficiency regulation. " * 20) + """</p>
</body></html>"""

JS_SHELL_HTML = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'

EMPTY_HTML = "<html><head><title>Empty</title></head><body></body></html>"


def _good_domain(**overrides) -> dict:
    domain = {
        "name": "Test Energy Ministry",
        "id": "test_energy",
        "base_url": "https://energy.example.gov",
        "start_paths": ["/policies"],
        "region": ["germany"],
        "language": "en",
    }
    domain.update(overrides)
    return domain


def _fetcher(routes: dict):
    """routes: url -> FetchResponse | Exception."""
    def fetch(url: str) -> FetchResponse:
        for key, outcome in routes.items():
            if key in url:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        raise AssertionError(f"unrouted URL in test fixture: {url}")
    return fetch


def _ok_html(text: str, content_type: str = "text/html; charset=utf-8") -> FetchResponse:
    return FetchResponse(status_code=200, text=text, final_url="https://energy.example.gov/policies",
                          content_type=content_type)


class ConnectionRefusedFetchError(FetchError):
    """Test-only alias making fixture intent readable at call sites."""


class TestSchemaShape:
    @pytest.mark.small
    def test_missing_required_field_fails(self):
        domain = _good_domain()
        del domain["base_url"]
        result = validate_candidate(domain, fetcher=_fetcher({}))
        schema_check = next(c for c in result.checks if c.name == "schema_shape")
        assert schema_check.passed is False
        assert "base_url" in schema_check.detail
        assert result.passed is False

    @pytest.mark.small
    def test_empty_start_paths_fails(self):
        domain = _good_domain(start_paths=[])
        result = validate_candidate(domain, fetcher=_fetcher({
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        schema_check = next(c for c in result.checks if c.name == "schema_shape")
        assert schema_check.passed is False
        assert "start_paths" in schema_check.detail

    @pytest.mark.small
    def test_wrong_type_fails_pydantic_validation(self):
        domain = _good_domain(max_depth="not-a-number")
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(GOOD_HTML),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        schema_check = next(c for c in result.checks if c.name == "schema_shape")
        assert schema_check.passed is False


class TestJurisdiction:
    @pytest.mark.small
    def test_unknown_region_slug_fails(self):
        domain = _good_domain(region=["not_a_real_place"])
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(GOOD_HTML),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        j_check = next(c for c in result.checks if c.name == "jurisdiction")
        assert j_check.passed is False
        assert "not_a_real_place" in j_check.detail
        assert result.passed is False

    @pytest.mark.small
    def test_missing_region_fails(self):
        domain = _good_domain(region=[])
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(GOOD_HTML),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        j_check = next(c for c in result.checks if c.name == "jurisdiction")
        assert j_check.passed is False

    @pytest.mark.small
    def test_known_region_slug_passes(self):
        domain = _good_domain(region=["germany"])
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(GOOD_HTML),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        j_check = next(c for c in result.checks if c.name == "jurisdiction")
        assert j_check.passed is True


class TestLiveness:
    @pytest.mark.small
    def test_dead_site_fails_fetch_and_skips_dependents(self):
        domain = _good_domain()
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": ConnectionRefusedFetchError("connection refused"),
            "robots.txt": ConnectionRefusedFetchError("connection refused"),
        }))
        fetch_check = next(c for c in result.checks if c.name == "fetch_start_path")
        assert fetch_check.passed is False
        content_type_check = next(c for c in result.checks if c.name == "content_type")
        assert content_type_check.passed is False
        assert "skipped" in content_type_check.detail
        robots_check = next(c for c in result.checks if c.name == "robots_txt")
        assert robots_check.passed is False
        assert result.passed is False

    @pytest.mark.small
    def test_redirect_loop_fails_fetch(self):
        domain = _good_domain()
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": RedirectLoopError("too many redirects"),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        fetch_check = next(c for c in result.checks if c.name == "fetch_start_path")
        assert fetch_check.passed is False
        assert "redirect" in fetch_check.detail.lower()
        assert result.passed is False

    @pytest.mark.small
    def test_http_error_status_fails_fetch(self):
        domain = _good_domain()
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": FetchResponse(status_code=404, text="Not Found",
                                       final_url="https://energy.example.gov/policies"),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        fetch_check = next(c for c in result.checks if c.name == "fetch_start_path")
        assert fetch_check.passed is False
        assert "404" in fetch_check.detail

    @pytest.mark.small
    def test_empty_page_fails_non_trivial_text(self):
        domain = _good_domain()
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(EMPTY_HTML),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        text_check = next(c for c in result.checks if c.name == "non_trivial_text")
        assert text_check.passed is False
        shell_check = next(c for c in result.checks if c.name == "not_js_shell")
        assert shell_check.passed is True  # empty, but not a recognized JS-shell marker
        assert result.passed is False

    @pytest.mark.small
    def test_js_shell_fails_not_js_shell_check(self):
        domain = _good_domain()
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(JS_SHELL_HTML),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        shell_check = next(c for c in result.checks if c.name == "not_js_shell")
        assert shell_check.passed is False
        assert "playwright" in shell_check.detail.lower()
        assert result.passed is False

    @pytest.mark.small
    def test_wrong_content_type_fails(self):
        domain = _good_domain()
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(GOOD_HTML, content_type="application/pdf"),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        ct_check = next(c for c in result.checks if c.name == "content_type")
        assert ct_check.passed is False
        assert "application/pdf" in ct_check.detail
        assert result.passed is False

    @pytest.mark.small
    def test_robots_unreachable_fails_its_own_check_only(self):
        domain = _good_domain()
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(GOOD_HTML),
            "robots.txt": FetchError("connection refused"),
        }))
        robots_check = next(c for c in result.checks if c.name == "robots_txt")
        assert robots_check.passed is False
        fetch_check = next(c for c in result.checks if c.name == "fetch_start_path")
        assert fetch_check.passed is True
        assert result.passed is False


class TestLanguageDetect:
    @pytest.mark.small
    def test_plausible_language_detected_for_good_candidate(self):
        domain = _good_domain()
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(GOOD_HTML),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        lang_check = next(c for c in result.checks if c.name == "language_detect")
        assert lang_check.passed is True
        assert "en" in lang_check.detail


class TestGoodCandidate:
    @pytest.mark.small
    def test_good_candidate_passes_every_check(self):
        domain = _good_domain()
        result = validate_candidate(domain, fetcher=_fetcher({
            "policies": _ok_html(GOOD_HTML),
            "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
        }))
        assert result.passed is True
        assert all(c.passed for c in result.checks)
        names = {c.name for c in result.checks}
        assert names == {
            "schema_shape", "jurisdiction", "fetch_start_path", "content_type",
            "non_trivial_text", "not_js_shell", "robots_txt", "language_detect",
        }


class TestValidationResultShape:
    @pytest.mark.small
    def test_result_carries_per_check_detail(self):
        result = ValidationResult(passed=True, checks=[Check("x", True, "ok")])
        assert result.passed is True
        assert [(c.name, c.passed, c.detail) for c in result.checks] == [("x", True, "ok")]


class TestCli:
    @pytest.mark.medium
    def test_main_exits_zero_for_passing_candidate(self, tmp_path, monkeypatch, capsys):
        import yaml as pyyaml

        candidate_path = tmp_path / "candidate.yaml"
        candidate_path.write_text(pyyaml.safe_dump({"domains": [_good_domain()]}))

        def fake_validate(domain, fetcher=None):
            return validate_candidate(domain, fetcher=_fetcher({
                "policies": _ok_html(GOOD_HTML),
                "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
            }))

        monkeypatch.setattr("src.sources.intake.validate_candidate", fake_validate)
        exit_code = main([str(candidate_path)])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "Result: PASS" in out

    @pytest.mark.medium
    def test_main_exits_nonzero_for_failing_candidate(self, tmp_path, monkeypatch, capsys):
        import yaml as pyyaml

        candidate_path = tmp_path / "candidate.yaml"
        candidate_path.write_text(pyyaml.safe_dump({"domains": [_good_domain(region=["nope"])]}))

        def fake_validate(domain, fetcher=None):
            return validate_candidate(domain, fetcher=_fetcher({
                "policies": _ok_html(GOOD_HTML),
                "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
            }))

        monkeypatch.setattr("src.sources.intake.validate_candidate", fake_validate)
        exit_code = main([str(candidate_path)])
        assert exit_code == 1
        out = capsys.readouterr().out
        assert "Result: FAIL" in out

    @pytest.mark.medium
    def test_main_missing_file_returns_two(self, tmp_path, capsys):
        exit_code = main([str(tmp_path / "nope.yaml")])
        assert exit_code == 2

    @pytest.mark.medium
    def test_main_no_args_returns_two(self, capsys):
        exit_code = main([])
        assert exit_code == 2

    @pytest.mark.medium
    def test_main_accepts_single_domain_mapping(self, tmp_path, monkeypatch, capsys):
        import yaml as pyyaml

        candidate_path = tmp_path / "candidate.yaml"
        candidate_path.write_text(pyyaml.safe_dump(_good_domain()))

        def fake_validate(domain, fetcher=None):
            return validate_candidate(domain, fetcher=_fetcher({
                "policies": _ok_html(GOOD_HTML),
                "robots.txt": _ok_html("User-agent: *", content_type="text/plain"),
            }))

        monkeypatch.setattr("src.sources.intake.validate_candidate", fake_validate)
        exit_code = main([str(candidate_path)])
        assert exit_code == 0
