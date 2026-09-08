import os


def is_email_change_enabled() -> bool:
    return os.getenv('EMAIL_CHANGE_ENABLED', 'true').lower() in ('true', '1')
