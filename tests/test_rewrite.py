"""Layer B: prompt construction, URL guards, scoring, and candidate selection."""

from __future__ import annotations

import pytest

from wmtk.rewrite import (
    API_KEY_ENV,
    STRENGTHS,
    BackendConfig,
    RewriteError,
    _check_url,
    build_prompt,
    chunk_text,
    divergence,
    rewrite,
    score_candidate,
    split_sentences,
)

SAMPLE = (
    "The committee released its findings on 14 March 2023. "
    "Revenue grew 12% to $4.2 million. Dr. Alice Chen led the review."
)


class TestPrompts:
    @pytest.mark.parametrize("strength", STRENGTHS)
    def test_every_strength_builds(self, strength):
        prompt = build_prompt(SAMPLE, strength)
        assert SAMPLE in prompt
        assert len(prompt) > len(SAMPLE)

    def test_unknown_strength_rejected(self):
        with pytest.raises(RewriteError, match="unknown strength"):
            build_prompt(SAMPLE, "nonsense")

    @pytest.mark.parametrize("strength", ["paraphrase", "humanize"])
    def test_prose_prompts_demand_fact_preservation(self, strength):
        prompt = build_prompt(SAMPLE, strength).lower()
        assert "preserve" in prompt
        assert "number" in prompt

    def test_code_prompt_protects_public_api(self):
        prompt = build_prompt("def f(): pass", "code").lower()
        assert "public api" in prompt
        assert "behavior" in prompt

    def test_backtranslate_uses_pivot(self):
        assert "German" in build_prompt(SAMPLE, "backtranslate", pivot="German")


class TestUrlGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:11434/api/chat",
            "http://localhost:8080/v1/chat/completions",
            "http://[::1]:11434/api/chat",
        ],
    )
    def test_loopback_allowed(self, url):
        _check_url(url, allow_remote=False)

    @pytest.mark.parametrize(
        "url",
        ["http://api.example.com/v1", "https://evil.test/v1/chat/completions"],
    )
    def test_remote_refused_by_default(self, url):
        with pytest.raises(RewriteError, match="not loopback"):
            _check_url(url, allow_remote=False)

    def test_remote_allowed_when_opted_in(self):
        _check_url("https://api.example.com/v1", allow_remote=True)

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "gopher://host"])
    def test_non_http_schemes_refused(self, url):
        with pytest.raises(RewriteError, match="non-http"):
            _check_url(url, allow_remote=True)


class TestApiKeyHandling:
    def test_reads_from_environment(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV, "sk-from-env")
        assert BackendConfig().api_key() == "sk-from-env"

    def test_override_wins(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV, "sk-from-env")
        cfg = BackendConfig(api_key_override="sk-from-request")
        assert cfg.api_key() == "sk-from-request"

    def test_absent_is_none(self, monkeypatch):
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        assert BackendConfig().api_key() is None

    def test_repr_never_leaks_the_key(self, monkeypatch):
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        cfg = BackendConfig(api_key_override="sk-supersecret-value")
        assert "supersecret" not in repr(cfg)
        assert "api_key=set" in repr(cfg)


class TestScoring:
    def test_identical_text_has_zero_divergence(self):
        assert divergence(SAMPLE, SAMPLE) == 0.0

    def test_unrelated_text_diverges_fully(self):
        assert divergence("alpha beta gamma", "delta epsilon zeta") == 1.0

    def test_partial_overlap_is_between(self):
        d = divergence("the cat sat on the mat", "the cat stood on the mat")
        assert 0.0 < d < 1.0

    def test_empty_inputs_do_not_crash(self):
        assert divergence("", "") == 0.0
        assert divergence("", "words here") == 1.0

    def test_length_drift_is_penalized(self):
        original = "word " * 40
        bloated = "different phrasing entirely " * 40
        score, notes = score_candidate(original, bloated)
        assert any("length drift" in n for n in notes)
        assert score < divergence(original, bloated)

    def test_normal_length_has_no_penalty(self):
        _, notes = score_candidate(SAMPLE, SAMPLE.replace("grew", "rose"))
        assert notes == []


class TestChunking:
    def test_short_text_is_one_chunk(self):
        assert chunk_text(SAMPLE, 6000) == [SAMPLE]

    def test_long_text_splits_on_paragraphs(self):
        text = "\n\n".join(["paragraph " * 50] * 10)
        chunks = chunk_text(text, 1000)
        assert len(chunks) > 1
        assert all(len(c) < 2000 for c in chunks)

    def test_no_content_is_lost(self):
        text = "\n\n".join(f"Paragraph number {i}." for i in range(40))
        rejoined = "\n\n".join(chunk_text(text, 200))
        assert rejoined == text

    def test_sentence_splitter(self):
        assert len(split_sentences(SAMPLE)) == 3


class TestPipeline:
    def test_print_prompt_calls_no_model(self):
        result = rewrite(SAMPLE, strength="paraphrase", cfg=BackendConfig())
        assert result.backend == "print-prompt"
        assert SAMPLE in result.text
        assert any("no model was called" in n for n in result.notes)

    def test_always_warns_about_cross_vendor(self):
        result = rewrite(SAMPLE, cfg=BackendConfig())
        assert any("different vendor" in n for n in result.notes)

    def test_candidate_count_validated(self):
        for bad in (0, 6, -1):
            with pytest.raises(RewriteError, match="between 1 and 5"):
                rewrite(SAMPLE, cfg=BackendConfig(), candidates=bad)

    def test_multiple_candidates_are_scored(self):
        result = rewrite(SAMPLE, cfg=BackendConfig(), candidates=3)
        assert len(result.candidates) == 3
        scores = [c.score for c in result.candidates]
        assert scores == sorted(scores, reverse=True), "not sorted best-first"

    def test_picks_most_diverged_candidate(self, monkeypatch):
        outputs = iter(
            [
                "The committee released its findings on 14 March 2023.",  # near-identical
                "On 14 March 2023 a panel published what it had found.",  # diverged
            ]
        )
        monkeypatch.setattr("wmtk.rewrite.call_model", lambda p, c: next(outputs))
        cfg = BackendConfig(backend="ollama")
        result = rewrite(SAMPLE, cfg=cfg, candidates=2, layer_a_after=False)
        assert result.text.startswith("On 14 March 2023")

    def test_layer_a_runs_over_model_output(self, monkeypatch):
        dirty = "The panel​ published⁠ its findings."
        monkeypatch.setattr("wmtk.rewrite.call_model", lambda p, c: dirty)
        result = rewrite(SAMPLE, cfg=BackendConfig(backend="ollama"))
        assert "​" not in result.text
        assert "⁠" not in result.text
        assert result.layer_a_after["total_removed"] == 2
        assert any("invisible characters" in n for n in result.notes)

    def test_layer_a_can_be_disabled(self, monkeypatch):
        dirty = "The panel​ published."
        monkeypatch.setattr("wmtk.rewrite.call_model", lambda p, c: dirty)
        result = rewrite(SAMPLE, cfg=BackendConfig(backend="ollama"), layer_a_after=False)
        assert "​" in result.text

    def test_unreachable_backend_raises_rewrite_error(self):
        cfg = BackendConfig(backend="ollama", base_url="http://127.0.0.1:1", timeout=2)
        with pytest.raises(RewriteError, match="cannot reach"):
            rewrite(SAMPLE, cfg=cfg)

    def test_unknown_backend_rejected(self):
        with pytest.raises(RewriteError, match="unknown backend"):
            rewrite(SAMPLE, cfg=BackendConfig(backend="telepathy"))

    def test_report_is_json_serializable(self):
        import json

        json.dumps(rewrite(SAMPLE, cfg=BackendConfig()).to_dict())


class TestRedirectRefusal:
    def test_redirect_handler_raises(self):
        from wmtk.rewrite import _NoRedirect

        handler = _NoRedirect()
        with pytest.raises(RewriteError, match="refusing to follow"):
            handler.redirect_request(None, None, 302, "Found", {}, "http://evil.test/steal")
