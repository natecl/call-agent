from pathlib import Path

from call_agent.domain import UserId
from call_agent.ledger import Ledger
from call_agent.telnyx import StubTelnyxHttp


def test_provision_is_idempotent_and_users_get_distinct_dids(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "call-agent.sqlite3")
    telnyx = StubTelnyxHttp()

    first = ledger.provision(
        UserId("first-user"), telnyx, "connection-1"
    )
    repeated = ledger.provision(
        UserId("first-user"), telnyx, "connection-1"
    )
    second = ledger.provision(
        UserId("second-user"), telnyx, "connection-1"
    )
    records = ledger.list()

    assert first.did == "+15555550100"
    assert repeated.model_dump(mode="json") == first.model_dump(mode="json")
    assert second.did == "+15555550101"
    assert [
        {
            "user_id": str(record.user_id),
            "did": str(record.did),
            "status": record.status,
        }
        for record in records
    ] == [
        {
            "user_id": "first-user",
            "did": "+15555550100",
            "status": "active",
        },
        {
            "user_id": "second-user",
            "did": "+15555550101",
            "status": "active",
        },
    ]
