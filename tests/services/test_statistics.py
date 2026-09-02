from __future__ import annotations

from decimal import Decimal

import pytest

from app.demo_data.constants import USER_A_ID
from app.services.statistics import StatisticsService
from tests.services.test_analysis import _seed


@pytest.mark.integration
async def test_statistics_never_sum_unconverted_cross_currency(session, session_factory, settings):
    await _seed(session, settings)
    stats = StatisticsService(session)
    spending = await stats.spending_summary(USER_A_ID)
    assert spending.transaction_count >= 1
    assert spending.currency == "USD"
    assert spending.value is not None
    income = await stats.income_summary(USER_A_ID)
    assert income.value is not None
    cash = await stats.cash_flow_summary(USER_A_ID)
    assert "credits" in cash.breakdown
    cats = await stats.category_breakdown(USER_A_ID)
    assert cats.breakdown
    merchants = await stats.merchant_summary(USER_A_ID)
    assert merchants.breakdown
    largest = await stats.largest_transactions(USER_A_ID)
    assert largest.transaction_count >= 1
    history = await stats.account_balance_history(USER_A_ID)
    assert history.value is not None
    search = await stats.text_search(USER_A_ID, "GROCERY")
    assert search.transaction_count >= 1
    for result in (spending, income, cash, cats, merchants, largest, history, search):
        assert result.low_confidence in {True, False}
        assert result.statement_ids is not None
