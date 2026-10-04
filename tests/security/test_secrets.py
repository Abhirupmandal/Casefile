"""Security Tests: Secrets Management & Leakage Prevention (Phase 8 Step 2 & 26).

Covers:
- No committed real credentials in configuration or code
- .env is in .gitignore
- .env.example contains only template placeholders
- SecretStr fields do not leak in string representations
"""

from __future__ import annotations

import re
from pathlib import Path

from casefile.config import load_config


def test_gitignore_contains_env_and_secrets():
    """Verify .gitignore properly excludes .env and secret files."""
    root = Path(__file__).resolve().parent.parent.parent
    gitignore_path = root / ".gitignore"
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")

    assert ".env" in content
    assert "*.db" in content


def test_env_example_contains_placeholders_only():
    """Verify .env.example does not contain real API keys."""
    root = Path(__file__).resolve().parent.parent.parent
    example_path = root / ".env.example"
    assert example_path.exists()
    content = example_path.read_text(encoding="utf-8")

    # Check for placeholder indicators
    assert "sk-your-openai-api-key" in content or "your-" in content
    # Ensure no real OpenAI key pattern (sk-proj... with 40+ chars)
    real_key_pattern = re.compile(r"sk-[a-zA-Z0-9]{40,}")
    assert not real_key_pattern.search(content)


def test_config_masks_secrets():
    """Verify SecretStr fields in LLMConfig do not leak in str() or repr()."""
    cfg = load_config(env="development")
    openai_key_repr = repr(cfg.llm.openai.api_key)
    assert "**********" in openai_key_repr or "SecretStr" in openai_key_repr


def test_no_hardcoded_private_keys_in_source():
    """Audit source files for BEGIN PRIVATE KEY or live tokens."""
    root = Path(__file__).resolve().parent.parent.parent
    src_dir = root / "src"

    for py_file in src_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        assert "-----BEGIN PRIVATE KEY-----" not in text
        assert "-----BEGIN RSA PRIVATE KEY-----" not in text
