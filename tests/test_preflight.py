from harness.preflight import scan_text


def test_secret_scan_detects_without_needing_a_real_key():
    assert scan_text("sk-or-v1-" + "a" * 40)
    assert scan_text("AIza" + "b" * 35)
    assert not scan_text("LLM_API_KEY=\nLLM_FALLBACK_API_KEY=")
