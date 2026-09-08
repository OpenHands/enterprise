"""Free email domain validation for quota increase requests.

The domain list mirrors the FREE_EMAIL_DOMAINS set in
OpenHands/research PR #88 (the canonical PQL V4 classifier).
"""

FREE_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        '126.com',
        '163.com',
        'aol.com',
        'aliyun.com',
        'atomicmail.io',
        'foxmail.com',
        'gmx.com',
        'gmail.com',
        'googlemail.com',
        'hotmail.com',
        'icloud.com',
        'live.com',
        'live.cn',
        'mail.com',
        'outlook.com',
        'pm.me',
        'proton.me',
        'protonmail.com',
        'qq.com',
        'sina.cn',
        'sina.com',
        'sohu.com',
        'yahoo.com',
        'yandex.com',
        'yandex.ru',
        'yeah.net',
    }
)


def is_free_email_domain(email: str) -> bool:
    """Return True if the email's domain is in the free-email blocklist."""
    if '@' not in email:
        return True
    domain = email.rsplit('@', 1)[-1].lower().strip()
    return domain in FREE_EMAIL_DOMAINS
