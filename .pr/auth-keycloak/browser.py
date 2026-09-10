"""Run real browser requests through playwright-cli; redact generated credentials."""

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

WORK = Path(__file__).resolve().parent
state = json.loads((WORK / 'state.json').read_text())
name = 'auth-keycloak-qa'


def command(*args):
    result = subprocess.run(
        ['playwright-cli', '-s=' + name, *args], text=True, capture_output=True
    )
    output = result.stdout + result.stderr
    for key in (
        'password',
        'admin_password',
        'client_secret',
        'broker_secret',
        'jwt_secret',
        'api_key',
        'legacy_llm_secret',
    ):
        output = output.replace(state[key], '[synthetic-secret-redacted]')
    print(output)
    if result.returncode:
        raise SystemExit(result.returncode)
    if '### Error' in output:
        raise SystemExit(1)


def run(body):
    boundary = """
      if(!page.context().__qaBoundary) {
        await page.context().route('**/*', route => {
          const url = route.request().url();
          return url.startsWith(cfg.app_url + '/') || url.startsWith(cfg.kc_url + '/') ? route.continue() : route.abort();
        });
        page.context().__qaBoundary = true;
      }
    """
    command(
        'run-code',
        'async page => { const cfg = '
        + json.dumps(state)
        + ';\n'
        + boundary
        + body
        + '\n}',
    )


phase = sys.argv[1]
if phase == 'open':
    config = {
        'browser': {
            'browserName': 'chromium',
            'contextOptions': {'ignoreHTTPSErrors': True},
        },
        'outputDir': str(WORK / 'browser'),
    }
    (WORK / 'playwright.json').write_text(json.dumps(config))
    command(
        'open',
        state['app_url'] + '/api/auth/capabilities',
        '--config=' + str(WORK / 'playwright.json'),
    )
elif phase == 'legacy-login':
    auth_url = (
        state['kc_url']
        + '/realms/enterprise/protocol/openid-connect/auth?'
        + urlencode(
            {
                'client_id': 'openhands',
                'redirect_uri': state['app_url'] + '/oauth/keycloak/callback',
                'response_type': 'code',
                'scope': 'openid profile email',
                'state': state['app_url'] + '/api/v1/users/me',
            }
        )
    )
    run(
        """
      const callbacks = [];
      const cookieEvents = [];
      const blocked = [];
      const cdp = await page.context().newCDPSession(page);
      await cdp.send('Network.enable');
      cdp.on('Network.responseReceivedExtraInfo', event => { for(const cookie of event.blockedCookies || []) blocked.push({name:cookie.cookieLine.split('=')[0], reasons:cookie.blockedReasons}); });
      const listener = async response => {
        if (response.url().includes('/oauth/keycloak/callback')) callbacks.push(response.status());
        const cookies = (await response.headersArray()).filter(h=>h.name.toLowerCase()==='set-cookie').map(h=>({name:h.value.split('=')[0],length:h.value.length,attributes:h.value.split(';').slice(1)}));
        if(cookies.length) cookieEvents.push({path:'/'+response.url().split('/').slice(3).join('/').split('?')[0],status:response.status(),cookies});
      };
      page.on('response', listener);
      await page.context().clearCookies();
      await page.goto("""
        + json.dumps(auth_url)
        + """);
      await page.locator('#username').fill(cfg.legacy_email);
      await page.locator('#password').fill(cfg.password);
      await page.locator('#kc-login').click();
      await page.waitForURL(cfg.app_url + '/api/v1/users/me', {timeout: 20000});
      const beforeRequest = (await page.context().cookies()).filter(c=>c.name.startsWith('keycloak_auth')).map(c=>({name:c.name,length:c.value.length}));
      await page.context().storageState({path: """
        + json.dumps(str(WORK / 'pre-request-state.json'))
        + """});
      const result = await page.context().request.get(cfg.app_url + '/api/v1/users/me');
      const user = await result.json();
      const cookies = (await page.context().cookies()).filter(c => c.name.startsWith('keycloak_auth'));
      if (result.status() !== 200 || !cookies.length || !callbacks.includes(302)) throw new Error('Legacy login did not establish browser authentication: ' + JSON.stringify({status:result.status(), body:user, beforeRequest, callbacks,cookie_names:cookies.map(c=>c.name),blocked,cookieEvents}));
      await page.context().storageState({path: """
        + json.dumps(str(WORK / 'legacy-browser-state.json'))
        + """});
      page.off('response', listener);
      return {phase: 'legacy-login', callback_statuses: callbacks, authenticated_status: result.status(),
              cookie_names: cookies.map(c=>c.name), cookie_secure: cookies.every(c=>c.secure),
              cookie_http_only: cookies.every(c=>c.httpOnly), profile_keys: Object.keys(user)};
    """
    )
elif phase == 'refresh':
    run(
        """
      const before = (await page.context().cookies()).filter(c=>c.name.startsWith('keycloak_auth'));
      await page.waitForTimeout(12000);
      const response = await page.context().request.get(cfg.app_url + '/api/v1/users/me');
      const after = (await page.context().cookies()).filter(c=>c.name.startsWith('keycloak_auth'));
      if(response.status() !== 200 || JSON.stringify(before) === JSON.stringify(after)) throw new Error('Expired token did not rotate cookie');
      const api = await page.context().request.get(cfg.app_url + '/api/v1/users/me', {headers: {'Authorization': 'Bearer ' + cfg.api_key}});
      if(api.status() !== 200) throw new Error('Legacy API key failed after login');
      await page.context().storageState({path: """
        + json.dumps(str(WORK / 'refreshed-browser-state.json'))
        + """});
      return {phase:'refresh', expired_access_token_status:response.status(), cookie_rotated:true, existing_api_key_status:api.status()};
    """
    )
elif phase == 'logout':
    run("""
      await page.waitForTimeout(12000);
      const csrf = await (await page.context().request.get(cfg.app_url + '/api/auth/csrf')).json();
      const response = await page.context().request.post(cfg.app_url + '/api/logout', {headers: {'Origin':cfg.app_url,'X-CSRF-Token':csrf.csrf_token}});
      const anonymous = await page.context().request.get(cfg.app_url + '/api/v1/users/me');
      const api = await page.context().request.get(cfg.app_url + '/api/v1/users/me', {headers: {'Authorization':'Bearer '+cfg.api_key}});
      const cookies = (await page.context().cookies()).filter(c=>c.name.startsWith('keycloak_auth'));
      if(response.status()!==200 || anonymous.status()!==401 || api.status()!==200 || cookies.length) throw new Error('Logout/API key continuity failed: ' + [response.status(),anonymous.status(),api.status(),cookies.length]);
      return {phase:'logout', logout_status:response.status(), browser_cookie_removed:true, anonymous_status:anonymous.status(), existing_api_key_status:api.status()};
    """)
elif phase == 'broker-login':
    run("""
      await page.context().clearCookies();
      const callbacks = [];
      const brokerRequests = [];
      const brokerListener = request => {
        if(request.url().includes('/protocol/saml') || request.url().includes('/broker/enterprise_sso/endpoint')) {
          const body = request.postData() || '';
          brokerRequests.push({path:'/'+request.url().split('/').slice(3).join('/').split('?')[0],method:request.method(),
                               saml_request:body.includes('SAMLRequest='),saml_response:body.includes('SAMLResponse=')});
        }
      };
      page.on('request', brokerListener);
      page.on('response', response => { if (response.url().includes('/oauth/keycloak/')) callbacks.push({path:'/'+response.url().split('/').slice(3).join('/').split('?')[0], status:response.status()}); });
      await page.goto(cfg.app_url + '/api/auth/authorize?provider=enterprise_sso&redirect_url=' + encodeURIComponent('/api/v1/users/me'));
      await page.locator('#username').fill(cfg.broker_email);
      await page.locator('#password').fill(cfg.password);
      await page.locator('#kc-login').click();
      await page.waitForTimeout(1500);
      page.off('request', brokerListener);
      return {phase:'broker-initial', path:'/'+page.url().split('/').slice(3).join('/').split('?')[0], title:await page.title(), body:await page.locator('body').innerText(), callbacks,brokerRequests};
    """)
elif phase == 'inspect':
    run("""
      const browser = await page.evaluate(async () => { const r = await fetch('/api/v1/users/me'); return {status:r.status,body:await r.text()}; });
      const api = await page.context().request.get(cfg.app_url + '/api/v1/users/me');
      return {url:page.url(), title:await page.title(), browser, api_status:api.status(),
              api_body:await api.text(), cookies:(await page.context().cookies()).map(({name,domain,path,secure,httpOnly,sameSite})=>({name,domain,path,secure,httpOnly,sameSite}))};
    """)
elif phase == 'accept-tos':
    run("""
      await page.getByRole('checkbox').check();
      await page.getByRole('button', {name:'Continue',exact:true}).click();
      await page.waitForTimeout(1000);
      return {phase:'tos',path:'/'+page.url().split('/').slice(3).join('/').split('?')[0],body:await page.locator('body').innerText()};
    """)
elif phase == 'broker-assert':
    run(
        """
      const response = await page.context().request.get(cfg.app_url + '/api/v1/users/me');
      const user = await response.json();
      if(response.status()!==200 || user.email!==cfg.broker_email || user.id!==user.org_id || !user.email_verified) throw new Error('Broker identity/graph authentication failed');
      await page.context().storageState({path: """
        + json.dumps(str(WORK / 'broker-state.json'))
        + """});
      return {phase:'broker-assert',status:response.status(),id:user.id,org_id:user.org_id,email:user.email,
              email_verified:user.email_verified,role:user.role,provider_token_names:Object.keys(user.secrets_store.provider_tokens)};
    """
    )
elif phase == 'outage':
    run("""
      const before = (await page.context().cookies()).filter(c=>c.name.startsWith('keycloak_auth'));
      if(!before.length) throw new Error('No existing browser session for outage check');
      await page.waitForTimeout(12000);
      const response = await page.context().request.get(cfg.app_url + '/api/v1/users/me');
      const after = (await page.context().cookies()).filter(c=>c.name.startsWith('keycloak_auth'));
      const callback = await page.context().request.get(cfg.app_url+'/oauth/keycloak/callback?code=synthetic-unavailable-code&state='+encodeURIComponent(cfg.app_url));
      const api = await page.context().request.get(cfg.app_url+'/api/v1/users/me',{headers:{'Authorization':'Bearer '+cfg.api_key}});
      const caps = await (await page.context().request.get(cfg.app_url+'/api/auth/capabilities')).json();
      const local = await page.context().request.post(cfg.app_url+'/api/auth/login',{data:{email:cfg.legacy_email,password:cfg.password}});
      if(response.status()!==503 || callback.status()!==503 || api.status()!==200 || JSON.stringify(before)!==JSON.stringify(after) || caps.mode!=='keycloak' || caps.password_login || local.status()!==404) {
        throw new Error('Outage policy failed: '+JSON.stringify({refresh:response.status(),callback:callback.status(),api:api.status(),cookie_preserved:JSON.stringify(before)===JSON.stringify(after),caps,local:local.status()}));
      }
      return {phase:'outage',expired_cookie_status:response.status(),callback_status:callback.status(),
              cookie_preserved:true,api_key_status:api.status(),mode:caps.mode,password_login:caps.password_login,local_login_status:local.status()};
    """)
elif phase == 'recovery':
    run("""
      const response = await page.context().request.get(cfg.app_url+'/api/v1/users/me');
      if(response.status()!==200) throw new Error('Session did not recover after outage: '+response.status());
      return {phase:'recovery',existing_cookie_status:response.status()};
    """)
elif phase == 'close':
    command('close')
else:
    raise SystemExit('Unknown phase')
