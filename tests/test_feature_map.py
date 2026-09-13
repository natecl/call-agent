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
        "| `hangup-on-goodbye` | shipped |",
        "| `multi_turn_booking` | shipped |",
        "| `keeps_talking` | shipped |",
        "| `build_book_instructions` | shipped |",
        "| goodbye needles | shipped |",
        "| hangup confirm timeout | shipped |",
        "| done-state hangup | shipped |",
    )

    missing = [row for row in required_rows if row not in INVENTORY]

    assert missing == []


def test_feature_map_lists_live_prompt_rows() -> None:
    required_rows = (
        "| live prompt | shipped |",
        "| speak_first | shipped |",
        "| Realtime reject | shipped |",
        "| Responses delegation | shipped |",
        "| live event journal | shipped |",
        "| live delegation reply | shipped |",
        "| silence failsafe | shipped |",
        "| tool loop | shipped |",
        "| app-owned hangup | shipped |",
        "| media disconnect → MediaStop | shipped |",
        "| play into closed media is non-fatal | shipped |",
        "| runtime crash → failed result | shipped |",
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
    assert "(./linq-to-call.md)" in RECIPES
    assert "(./verify-linq-call.md)" in RECIPES


def test_feature_map_lists_account_rows() -> None:
    required_rows = (
        "| `users` | shipped |",
        "| `users.from_did` | shipped |",
        "| `open_accounts` | shipped |",
        "| `account upsert` | shipped |",
        "| `account get` | shipped |",
        "| `account list` | shipped |",
        "| `POST /accounts/sync` | shipped |",
        "| `GET /accounts/dashboard` | shipped |",
        "| `verify-accounts` | shipped |",
        "| `Stripe → users sync` | gated |",
        "| `minutes ledger` | not shipped |",
        "| `kickoff account lookup` | shipped |",
        "| `kickoff from-DID` | shipped |",
        "| `no-account reply` | shipped |",
        "| `no-name reply` | shipped |",
        "| `no-from-did reply` | shipped |",
        "| `LINQ_KICKOFF_USER_ID` | not shipped |",
    )

    missing = [row for row in required_rows if row not in INVENTORY]

    assert missing == []
    assert "stays `LINQ_KICKOFF_USER_ID`" not in INVENTORY
    did_at = _heading_index(INVENTORY, "did provision and did list")
    accounts_at = _heading_index(INVENTORY, "accounts")
    quick_ask_at = _heading_index(INVENTORY, "quick-ask")
    assert did_at < accounts_at < quick_ask_at
    assert "(./accounts.md)" in RECIPES


def test_feature_map_lists_linq_to_call_rows() -> None:
    required_rows = (
        "| `linq-to-call` | shipped |",
        "| `linq-to-call --live` | gated |",
        "| `parse_kickoff` | shipped |",
        "| `pre-call collect` | shipped |",
        "| `pre-call confirm` | shipped |",
        "| `kickoff result reply` | shipped |",
        "| `linq ↔ call cross-link` | shipped |",
        "| `kickoff dedupe` | shipped |",
        "| `verify-linq-call` | shipped |",
        "| mid-call chat | not shipped |",
        "| `kickoff account lookup` | shipped |",
        "| `kickoff from-DID` | shipped |",
        "| `no-account reply` | shipped |",
        "| `no-name reply` | shipped |",
        "| `no-from-did reply` | shipped |",
        "| `no number_orders at dial` | shipped |",
        "| friendly iMessage errors | shipped |",
    )

    missing = [row for row in required_rows if row not in INVENTORY]

    assert missing == []
    linq_to_call_at = _heading_index(INVENTORY, "linq-to-call")
    verify_book_at = _heading_index(INVENTORY, "verify-book")
    verify_call_agent_at = _heading_index(INVENTORY, "verify-call-agent")
    assert verify_book_at < linq_to_call_at < verify_call_agent_at


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
    assert "| live origin preflight | shipped |" in INVENTORY
    assert "| delivery-miss timeout | shipped |" in INVENTORY
    assert "| unknown-call 404 | shipped |" in INVENTORY
    assert "serve_token" in INVENTORY
    assert "Fly `PUBLIC_BASE_URL`" in INVENTORY
    assert "same tunnel" in INVENTORY
    assert "assert_live_dial_origins" in INVENTORY


def test_feature_map_lists_default_wipe_and_keeps_privacy_page_unshipped() -> None:
    assert "| default wipe | shipped |" in INVENTORY
    assert "| `retain_call_artifacts` | shipped |" in INVENTORY
    assert "| privacy page | not shipped |" in INVENTORY
    assert "(./privacy-wipe.md)" in RECIPES
    wipe_at = _heading_index(INVENTORY, "Run directory")
    not_shipped_at = _heading_index(INVENTORY, "Not shipped")
    assert wipe_at < not_shipped_at
    features = (
        REPO / ".cursor" / "skills" / "verify-call-agent" / "features"
    )
    text = (features / "privacy-wipe.md").read_text()
    required = (
        "## Sub-features",
        "## How to get to it (user POV)",
        "## Driving it with call-agent",
        "## Gotchas",
    )
    positions = [text.find(section) for section in required]
    assert all(index != -1 for index in positions)
    assert positions == sorted(positions)
    assert "Prove-retain override OFF, wipe-unless-opted-in is the live default." in INVENTORY
    assert "Prove-retain override OFF, wipe-unless-opted-in is the live default." in text
    assert "RETAIN_CALL_ARTIFACTS=1 still on Fly" not in INVENTORY
    assert "RETAIN_CALL_ARTIFACTS=1 still on Fly" not in text


def test_feature_map_recipe_counts_match_levers() -> None:
    readme = (REPO / "README.md").read_text()
    skill = (
        REPO / ".cursor" / "skills" / "verify-call-agent" / "SKILL.md"
    ).read_text()
    assert "five stub checks" not in INVENTORY
    assert "seven stub checks" not in INVENTORY
    assert "three stub checks" not in INVENTORY
    assert "prints three `[PASS]`" not in readme
    assert "prints four `[PASS]`" in readme
    assert "8/8 passed" in readme
    assert "10/10 passed" in readme
    assert "4/4 passed" in skill
    assert "| `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` | shipped | Covers the eight stub checks" in INVENTORY
    assert "| `.cursor/skills/verify-call-agent/features/verify-book.md` | shipped | Covers the ten stub checks" in INVENTORY
    assert "| `.cursor/skills/verify-call-agent/features/verify-persistence.md` | shipped | Covers the four stub checks" in INVENTORY
    assert "(./verify-accounts.md)" in RECIPES


def test_feature_map_records_cap_locks() -> None:
    agents = (REPO / "AGENTS.md").read_text()
    plan = (REPO / "docs" / "WAVE1_HOLD_FEATURE_MAP_PLAN.md").read_text()
    assert "| privacy wipe (hangup wipe) | shipped |" in INVENTORY
    assert "| L1 iMessage | shipped |" in INVENTORY
    assert "live-prove: PASS (#34)" in INVENTORY
    assert "qa owns live-prove sign-off (TRE-19)" in INVENTORY
    assert "| live voice | gated |" in INVENTORY
    assert "| hangup | shipped |" in INVENTORY
    assert "| landing unfreeze | pending web |" in INVENTORY
    assert "TRE-22 unfreeze ordered" in INVENTORY
    assert "Dashboard UI stays on Pages" in INVENTORY
    assert "Intended Fly route" in INVENTORY
    assert "missing_public_key" in INVENTORY
    assert "bad_signature" in INVENTORY
    assert "website `/start`" in INVENTORY
    assert "AGENTS.md" in INVENTORY
    assert "WAVE1_HOLD_FEATURE_MAP_PLAN.md" in INVENTORY
    assert "verify-accounts" in agents
    assert "Prove-retain override OFF" in plan
    assert "wipe-unless-opted-in is the live default" in plan


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

    for name in (
        "book.md",
        "verify-book.md",
        "gpt-live-prompt.md",
        "linq-to-call.md",
        "verify-linq-call.md",
        "accounts.md",
        "verify-accounts.md",
    ):
        text = (features / name).read_text()
        positions = [text.find(section) for section in required]
        assert all(index != -1 for index in positions), name
        assert positions == sorted(positions), name


def test_feature_map_lists_slug_and_publish_isolation_rows() -> None:
    required_rows = (
        "| `artifact_slug` | shipped |",
        "| publish failure isolation | shipped |",
    )
    missing = [row for row in required_rows if row not in INVENTORY]
    assert missing == []

