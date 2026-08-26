from server.services.free_email_domains import FREE_EMAIL_DOMAINS, is_free_email_domain


class TestFreeEmailDomains:
    def test_gmail_is_free(self):
        assert is_free_email_domain('user@gmail.com') is True

    def test_outlook_is_free(self):
        assert is_free_email_domain('user@outlook.com') is True

    def test_yahoo_is_free(self):
        assert is_free_email_domain('user@yahoo.com') is True

    def test_atomicmail_is_free(self):
        assert is_free_email_domain('user@atomicmail.io') is True

    def test_work_email_is_not_free(self):
        assert is_free_email_domain('user@acme-corp.com') is False

    def test_case_insensitive_domain(self):
        assert is_free_email_domain('user@GMAIL.COM') is True

    def test_no_at_sign_is_free(self):
        assert is_free_email_domain('invalid-email') is True

    def test_all_research_pr88_domains_present(self):
        expected = {
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
        assert expected <= FREE_EMAIL_DOMAINS
