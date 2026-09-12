from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INVENTORY = (REPO / "docs" / "FEATURE_MAP.md").read_text()
RECIPES = (
    REPO / ".cursor" / "skills" / "verify-call-agent" / "features" / "README.md"
).read_text()


def _heading_index(text: str, heading: str) -> int:
    marker = f"\n## {heading}\n"
    index = text.find(marker)
    assert index != -1, f"missing heading {heading!r}"
    return index


def test_feature_map_lists_shipped_book_rows() -> None:
    required_rows = (
        "| `book` | shipped |",
        "| `verify-book` | shipped |",
        "| `BookRequest` | shipped |",
        "| `BookResult` | shipped |",
        "| `assess_book` | shipped |",
        "| `multi_turn_booking` | shipped |",
        "| `build_book_instructions` | shipped |",
    )

    missing = [row for row in required_rows if row not in INVENTORY]

    assert missing == []


def test_feature_map_lists_live_prompt_rows() -> None:
    required_rows = (
        "| live prompt | shipped |",
        "| speak_first | shipped |",
        "| Realtime reject | shipped |",
        "| no tool-delegation | shipped |",
    )

    missing = [row for row in required_rows if row not in INVENTORY]

    assert missing == []
    assert "(./gpt-live-prompt.md)" in RECIPES


def test_feature_map_keeps_book_with_the_other_levers() -> None:
    book_at = _heading_index(INVENTORY, "book")
    verify_book_at = _heading_index(INVENTORY, "verify-book")
    design_at = _heading_index(INVENTORY, "Design")

    assert book_at < verify_book_at < design_at


def test_verify_feature_index_lists_book_recipes() -> None:
    assert "(./book.md)" in RECIPES
    assert "(./verify-book.md)" in RECIPES


def test_fly_toml_keeps_always_on_machines_and_feature_map_names_the_host() -> None:
    fly = (REPO / "fly.toml").read_text()
    assert 'app = "hidden-beacon-080"' in fly
    assert 'auto_stop_machines = "off"' in fly
    assert "min_machines_running = 1" in fly
    assert "always-on Fly host" in INVENTORY
    assert "APP_BASE_URL" in INVENTORY
    assert "https://hidden-beacon-080.fly.dev" in INVENTORY
    assert "https://<app>.fly.dev" not in INVENTORY


def test_feature_map_documents_cli_live_ingress() -> None:
    assert "| live CLI ingress | shipped |" in INVENTORY
    assert "| delivery-miss timeout | shipped |" in INVENTORY
    assert "serve_token" in INVENTORY
    assert "Fly `PUBLIC_BASE_URL`" in INVENTORY


def test_book_feature_recipes_use_the_entry_contract() -> None:
    features = (
        REPO / ".cursor" / "skills" / "verify-call-agent" / "features"
    )
    required = (
        "## Sub-features",
        "## How to get to it (user POV)",
        "## Driving it with call-agent",
        "## Gotchas",
    )

    for name in ("book.md", "verify-book.md", "gpt-live-prompt.md"):
        text = (features / name).read_text()
        positions = [text.find(section) for section in required]
        assert all(index != -1 for index in positions), name
        assert positions == sorted(positions), name
