def budget_values_match(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return abs(actual - expected) <= 1e-6
