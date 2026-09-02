from datetime import UTC, datetime
from uuid import UUID

from app.jobs.run_analysis import _cli_idempotency_key


def test_cli_idempotency_key_is_scoped_to_user_and_analysis_clock():
    user_a = UUID("11111111-1111-4111-8111-111111111111")
    user_b = UUID("22222222-2222-4222-8222-222222222222")
    first_clock = datetime(2026, 8, 30, tzinfo=UTC)
    second_clock = datetime(2026, 8, 31, tzinfo=UTC)

    assert _cli_idempotency_key(user_a, first_clock) != _cli_idempotency_key(user_a, second_clock)
    assert _cli_idempotency_key(user_a, second_clock) != _cli_idempotency_key(user_b, second_clock)
    assert _cli_idempotency_key(user_a, second_clock) == _cli_idempotency_key(user_a, second_clock)
