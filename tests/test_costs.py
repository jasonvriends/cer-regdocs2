from regdocs_atlas.costs import AzureContentUnderstandingRates, estimate_usage_cost, usage_from_payload


def test_usage_supports_camel_case_and_costs_only_observed_meters():
    usage = usage_from_payload(
        {
            "usage": {
                "documentPagesMinimal": 100,
                "documentPagesBasic": 200,
                "documentPagesStandard": 300,
            }
        }
    )
    assert usage == {"minimal": 100, "basic": 200, "standard": 300}
    cost, missing = estimate_usage_cost(
        usage,
        AzureContentUnderstandingRates(1.0, 2.0, 5.0),
    )
    assert missing == []
    assert cost == 2.0


def test_missing_rate_returns_no_dollar_estimate():
    cost, missing = estimate_usage_cost(
        {"minimal": 0, "basic": 0, "standard": 12},
        AzureContentUnderstandingRates(None, None, None),
    )
    assert cost is None
    assert missing == ["standard"]
