import hmac


def looks_like_jwt(token_value: str) -> bool:
    parts = token_value.split('.')
    return len(parts) == 3 and all(parts)


def secrets_equal(provided: str, expected: str) -> bool:
    """Compare two shared secrets in constant time.

    Use this to check a secret from a request (header, webhook token) against the
    configured value. Both sides are UTF-8 encoded first: `hmac.compare_digest`
    raises TypeError on non-ASCII `str` input, and header values can be non-ASCII.
    """
    return hmac.compare_digest(provided.encode('utf-8'), expected.encode('utf-8'))
