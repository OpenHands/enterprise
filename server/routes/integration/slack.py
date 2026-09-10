import hashlib
import html
import json
import secrets
from datetime import timedelta
from urllib.parse import quote, urlencode
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from slack_sdk.errors import SlackApiError
from slack_sdk.oauth import AuthorizeUrlGenerator
from slack_sdk.signature import SignatureVerifier
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import delete, select, text

from integrations.models import Message, SourceType
from integrations.slack.slack_errors import SlackError, SlackErrorCode
from integrations.slack.slack_manager import SlackManager
from integrations.utils import (
    HOST_URL,
)
from openhands.app_server.config import depends_jwt_service
from openhands.app_server.integrations.service_types import (
    ProviderTimeoutError,
    ProviderType,
)
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.user_auth.user_auth import get_user_auth
from server.auth.constants import (
    KEYCLOAK_CLIENT_ID,
    KEYCLOAK_REALM_NAME,
    KEYCLOAK_SERVER_URL_EXT,
)
from server.auth.contracts import AuthenticationUnavailable, InvalidCredentials
from server.auth.keycloak.authorization import require_keycloak
from server.auth.mode import AuthMode, get_auth_mode
from server.auth.saas_user_auth import SaasUserAuth
from server.auth.slack_link import slack_login_url
from server.auth.token_manager import TokenManager
from server.constants import (
    SLACK_CLIENT_ID,
    SLACK_CLIENT_SECRET,
    SLACK_SIGNING_SECRET,
    SLACK_WEBHOOKS_ENABLED,
)
from server.logger import logger
from storage.database import a_session_maker
from storage.redis import get_redis_client_async
from storage.slack_team_store import SlackTeamStore
from storage.slack_user import SlackUser
from storage.user import User
from storage.user_store import UserStore

signature_verifier = SignatureVerifier(signing_secret=SLACK_SIGNING_SECRET or '')
slack_router = APIRouter(prefix='/slack')

# Build https://slack.com/oauth/v2/authorize with sufficient query parameters
authorize_url_generator = AuthorizeUrlGenerator(
    client_id=SLACK_CLIENT_ID or '',
    scopes=[
        'app_mentions:read',
        'chat:write',
        'users:read',
        'files:read',
        'channels:history',
        'groups:history',
        'mpim:history',
        'im:history',
    ],
)
token_manager = TokenManager()

slack_manager = SlackManager(token_manager)
slack_team_store = SlackTeamStore.get_instance()
jwt_service_dependency = depends_jwt_service()


_SLACK_STATE_COOKIE = 'oh_slack_oauth'


async def _local_link_account(request: Request) -> SaasUserAuth:
    auth = await get_user_auth(request)
    if not isinstance(auth, SaasUserAuth) or auth.principal is None:
        raise HTTPException(401, 'Authentication required')
    if auth.principal.restricted:
        raise HTTPException(403, 'Change your initial password before connecting Slack')
    return auth


def _link_session(auth: SaasUserAuth) -> str:
    assert auth.principal is not None
    principal = auth.principal
    return f'{principal.authentication_method}:{principal.session_id or principal.api_key_id}'


@slack_router.get('/install')
async def install(
    request: Request, state: str = '', jwt_service: JwtService = jwt_service_dependency
):
    """Forward into Slack OAuth, binding local linking to the current account."""
    if get_auth_mode() is AuthMode.KEYCLOAK:
        return RedirectResponse(authorize_url_generator.generate(state=state))
    try:
        auth = await _local_link_account(request)
    except InvalidCredentials:
        return RedirectResponse(
            '/login?'
            + urlencode(
                {
                    'returnTo': '/slack/install'
                    + ('?' + urlencode({'state': state}) if state else '')
                }
            ),
            302,
        )
    original = jwt_service.verify_jws_token(state) if state else {}
    nonce = secrets.token_urlsafe(32)
    state = jwt_service.create_jws_token(
        {
            'purpose': 'slack_install',
            'user_id': auth.user_id,
            'session': _link_session(auth),
            'nonce': nonce,
            'message': original,
        },
        expires_in=timedelta(minutes=10),
    )
    response = RedirectResponse(authorize_url_generator.generate(state=state), 302)
    response.set_cookie(
        _SLACK_STATE_COOKIE,
        nonce,
        max_age=600,
        secure=True,
        httponly=True,
        samesite='lax',
        path='/',
    )
    return response


@slack_router.get('/install-callback')
async def install_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str = '',
    state: str = '',
    error: str = '',
    jwt_service: JwtService = jwt_service_dependency,
):
    """Exchange Slack proof and link it to the authenticated account."""
    local = get_auth_mode() is AuthMode.LOCAL
    local_auth = None
    local_state = None
    if local:
        local_auth = await _local_link_account(request)
        try:
            local_state = jwt_service.verify_jws_token(state)
            nonce = request.cookies.get(_SLACK_STATE_COOKIE, '')
            if (
                local_state.get('purpose') != 'slack_install'
                or local_state.get('user_id') != local_auth.user_id
                or local_state.get('session') != _link_session(local_auth)
                or not nonce
                or not secrets.compare_digest(nonce, local_state.get('nonce', ''))
            ):
                raise ValueError('Invalid Slack connection state')
        except Exception:
            raise HTTPException(400, 'Invalid Slack connection state') from None
    if not code or error:
        logger.warning(
            'slack_install_callback_error',
            extra={
                'error': error,
            },
        )
        return _html_response(
            title='Error',
            description=html.escape(error or 'No code provided'),
            status_code=400,
        )

    try:
        client = AsyncWebClient()  # no prepared token needed for this
        # Complete the installation by calling oauth.v2.access API method
        oauth_response = await client.oauth_v2_access(
            client_id=SLACK_CLIENT_ID or '',
            client_secret=SLACK_CLIENT_SECRET or '',
            redirect_uri=f'https://{request.url.netloc}{request.url.path}',
            code=code,
        )
        bot_access_token = oauth_response.get('access_token')
        team = oauth_response.get('team') or {}
        team_id = team.get('id')
        authed_user = oauth_response.get('authed_user') or {}

        if local:
            assert local_auth is not None and local_state is not None
            user = await UserStore.get_user_by_id(local_auth.user_id)
            if user is None or user.is_disabled:
                raise HTTPException(401, 'Account is unavailable')
            payload = dict(local_state.get('message') or {})
            payload.update(
                {
                    'slack_user_id': authed_user.get('id'),
                    'bot_access_token': bot_access_token,
                    'team_id': team_id,
                }
            )
            response = await _complete_slack_link(user, payload, background_tasks)
            response.delete_cookie(
                _SLACK_STATE_COOKIE,
                secure=True,
                httponly=True,
                samesite='lax',
                path='/',
            )
            return response

        # Create a state variable for keycloak oauth
        payload = {}
        if state:
            payload = jwt_service.verify_jws_token(state)
        payload['slack_user_id'] = authed_user.get('id')
        payload['bot_access_token'] = bot_access_token
        payload['team_id'] = team_id

        state = jwt_service.create_jws_token(payload)

        # Redirect into keycloak
        scope = quote('openid email profile offline_access')
        redirect_uri = f'{HOST_URL}/slack/keycloak-callback'
        auth_url = (
            f'{KEYCLOAK_SERVER_URL_EXT}/realms/{KEYCLOAK_REALM_NAME}/protocol/openid-connect/auth'
            f'?client_id={KEYCLOAK_CLIENT_ID}&response_type=code'
            f'&redirect_uri={redirect_uri}'
            f'&scope={scope}'
            f'&state={state}'
        )

        return RedirectResponse(auth_url)
    except HTTPException:
        raise
    except Exception:  # type: ignore
        logger.exception('unexpected_error', stack_info=True)
        return _html_response(
            title='Error',
            description='Internal server Error',
            status_code=500,
        )


@slack_router.get('/keycloak-callback')
async def keycloak_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str = '',
    state: str = '',
    error: str = '',
    jwt_service: JwtService = jwt_service_dependency,
):
    require_keycloak()
    if not code or error:
        logger.warning(
            'problem_retrieving_keycloak_tokens',
            extra={
                'error': error,
            },
        )
        return _html_response(
            title='Error',
            description=html.escape(error or 'No code provided'),
            status_code=400,
        )

    payload: dict[str, str] = jwt_service.verify_jws_token(state)

    # Retrieve the keycloak_user_id
    redirect_uri = f'{HOST_URL}{request.url.path}'
    try:
        (
            keycloak_access_token,
            keycloak_refresh_token,
        ) = await token_manager.get_keycloak_tokens(code, redirect_uri)
    except AuthenticationUnavailable:
        logger.warning('keycloak_unavailable_during_slack_auth', exc_info=True)
        return _html_response(
            title='Authentication service temporarily unavailable.',
            description='Please try installing the OpenHands Slack App again.',
            status_code=503,
        )
    if not keycloak_access_token or not keycloak_refresh_token:
        logger.warning(
            'problem_retrieving_keycloak_tokens',
            extra={
                'error': error,
            },
        )
        return _html_response(
            title='Failed to authenticate.',
            description=f'Please re-login into <a href="{HOST_URL}" style="color:#ecedee;text-decoration:underline;">OpenHands Cloud</a>. Then try <a href="https://docs.all-hands.dev/usage/cloud/slack-installation" style="color:#ecedee;text-decoration:underline;">installing the OpenHands Slack App</a> again',
            status_code=400,
        )

    user_info = await token_manager.get_user_info(keycloak_access_token)
    keycloak_user_id = user_info.sub
    try:
        canonical_id = UUID(keycloak_user_id)
    except ValueError:
        raise HTTPException(401, 'Invalid account identifier') from None
    from server.auth.user_management import EnterpriseUserManagementService

    user = await EnterpriseUserManagementService().ensure_authenticated_account(
        canonical_id, user_info.model_dump(exclude_none=True)
    )
    if not user or user.is_disabled:
        return _html_response(
            title='Failed to authenticate.',
            description=f'Please re-login into <a href="{HOST_URL}" style="color:#ecedee;text-decoration:underline;">OpenHands Cloud</a>. Then try <a href="https://docs.all-hands.dev/usage/cloud/slack-installation" style="color:#ecedee;text-decoration:underline;">installing the OpenHands Slack App</a> again',
            status_code=400,
        )

    # These tokens are offline access tokens - store them!
    await token_manager.store_offline_token(keycloak_user_id, keycloak_refresh_token)

    idp: str = user_info.identity_provider or ProviderType.GITHUB.value
    idp_type = 'oidc'
    if ':' in idp:
        idp, idp_type = idp.rsplit(':', 1)
        idp_type = idp_type.lower()
    await token_manager.store_idp_tokens(
        ProviderType(idp), keycloak_user_id, keycloak_access_token
    )

    return await _complete_slack_link(user, payload, background_tasks)


async def _complete_slack_link(
    user: User, payload: dict, background_tasks: BackgroundTasks
) -> HTMLResponse:
    keycloak_user_id = str(user.id)
    slack_user_id = payload['slack_user_id']
    bot_access_token = payload.get('bot_access_token')
    team_id = payload['team_id']
    # Retrieve bot token
    if team_id and bot_access_token:
        await slack_team_store.create_team(team_id, bot_access_token)
    else:
        bot_access_token = await slack_team_store.get_team_bot_token(team_id)

    if not bot_access_token:
        logger.error(
            f'Account linking failed, did not find slack team {team_id} for user {keycloak_user_id}'
        )
        raise HTTPException(400, 'Slack connection could not be established')

    # Retrieve the display_name from slack
    client = AsyncWebClient(token=bot_access_token)
    try:
        slack_user_info = await client.users_info(user=slack_user_id)
    except SlackApiError as e:
        if e.response.get('error') == 'missing_scope':
            logger.warning(
                'slack_missing_scope_during_install',
                extra={'slack_user_id': slack_user_id, 'team_id': team_id},
            )
            return _html_response(
                title='Re-installation Required',
                description=(
                    'The Slack app is missing required permissions. '
                    f'Please <a href="{HOST_URL}/slack/install" style="color:#ecedee;text-decoration:underline;">re-install the OpenHands Slack App</a> '
                    'to authorize the updated permissions.'
                ),
                status_code=400,
            )
        raise
    slack_profile = (slack_user_info.get('user') or {}).get('profile') or {}
    slack_display_name = slack_profile.get('display_name') or ''
    slack_user = SlackUser(
        keycloak_user_id=keycloak_user_id,
        org_id=user.current_org_id,
        slack_user_id=slack_user_id,
        slack_display_name=slack_display_name,
    )

    async with a_session_maker(expire_on_commit=False) as session, session.begin():
        if get_auth_mode() is AuthMode.LOCAL:
            current = await session.scalar(
                select(User).where(User.id == user.id).with_for_update()
            )
            if current is None or current.is_disabled:
                raise HTTPException(401, 'Account is unavailable')
            # Serialize even the first link, when there is no SlackUser row to
            # lock. All local writes take the account lock before this lock.
            if session.get_bind().dialect.name == 'postgresql':
                actor_lock = int.from_bytes(
                    hashlib.sha256(f'slack:{slack_user_id}'.encode()).digest()[:8],
                    signed=True,
                )
                await session.execute(
                    text('SELECT pg_advisory_xact_lock(:actor_lock)'),
                    {'actor_lock': actor_lock},
                )
            existing = await session.scalars(
                select(SlackUser)
                .where(SlackUser.slack_user_id == slack_user_id)
                .with_for_update()
            )
            if any(row.keycloak_user_id != keycloak_user_id for row in existing):
                raise HTTPException(
                    409, 'This Slack account is already connected to another account'
                )
            slack_user.org_id = current.current_org_id
        # First delete any existing tokens
        await session.execute(
            delete(SlackUser).where(SlackUser.slack_user_id == slack_user_id)
        )

        # Store the token
        session.add(slack_user)

    safe_payload = {
        key: value for key, value in payload.items() if key != 'bot_access_token'
    }
    message = Message(source=SourceType.SLACK, message=safe_payload)

    background_tasks.add_task(slack_manager.receive_message, message)
    return _html_response(
        title='OpenHands Authentication Successful!',
        description='It is now safe to close this tab.',
        status_code=200,
    )


@slack_router.post('/on-event')
async def on_event(request: Request, background_tasks: BackgroundTasks):
    if not SLACK_WEBHOOKS_ENABLED:
        return JSONResponse({'success': 'slack_webhooks_disabled'})
    body = await request.body()
    payload = json.loads(body.decode())

    logger.info('slack_on_event', extra={'event_type': payload.get('type')})

    # First verify the signature
    if not SLACK_SIGNING_SECRET or not signature_verifier.is_valid(
        body=body,
        timestamp=request.headers.get('x-slack-request-timestamp', ''),
        signature=request.headers.get('x-slack-signature', ''),
    ):
        raise HTTPException(status_code=403, detail='invalid_request')

    # Slack initially / periodically sends challenges and expects this response
    if 'challenge' in payload:
        return PlainTextResponse(payload['challenge'])

    # {"message": "slack_on_event", "severity": "INFO", "payload": {"token": "i8Al1OkFR99MafAxURXhRJ7b", "team_id": "T07E1S2M2Q6", "api_app_id": "A08MFF9S6FQ", "event": {"user": "U07G13E21DK", "type": "app_mention", "ts": "1744740589.879749", "client_msg_id": "4382e009-6717-4ed7-954b-f0eb3073b88e", "text": "<@U08MFFR1AR4> Flarglebargle!", "team": "T07E1S2M2Q6", "blocks": [{"type": "rich_text", "block_id": "ynJhY", "elements": [{"type": "rich_text_section", "elements": [{"type": "user", "user_id": "U08MFFR1AR4"}, {"type": "text", "text": " Flarglebargle!"}]}]}], "channel": "C08MYQ1PQS0", "event_ts": "1744740589.879749"}, "type": "event_callback", "event_id": "Ev08NE73GEUB", "event_time": 1744740589, "authorizations": [{"enterprise_id": None, "team_id": "T07E1S2M2Q6", "user_id": "U08MFFR1AR4", "is_bot": True, "is_enterprise_install": False}], "is_ext_shared_channel": False, "event_context": "4-eyJldCI6ImFwcF9tZW50aW9uIiwidGlkIjoiVDA3RTFTMk0yUTYiLCJhaWQiOiJBMDhNRkY5UzZGUSIsImNpZCI6IkMwOE1ZUTFQUVMwIn0"}}
    if payload.get('type') != 'event_callback':
        return JSONResponse({'success': True})

    event = payload['event']
    user_msg = event['text']
    assert event['type'] == 'app_mention'
    client_msg_id = event['client_msg_id']
    message_ts = event['ts']
    thread_ts = event.get('thread_ts')
    channel_id = event['channel']
    slack_user_id = event['user']
    team_id = payload['team_id']

    # Sometimes slack sends duplicates, so we need to make sure this is not a duplicate.
    redis = get_redis_client_async()
    key = f'slack_msg:{client_msg_id}'
    created = await redis.set(key, 1, nx=True, ex=60)
    if not created:
        logger.info('slack_is_duplicate')
        return JSONResponse({'success': True})

    # TODO: Get team id
    payload = {
        'message_ts': message_ts,
        'thread_ts': thread_ts,
        'channel_id': channel_id,
        'user_msg': user_msg,
        'slack_user_id': slack_user_id,
        'team_id': team_id,
    }

    message = Message(
        source=SourceType.SLACK,
        message=payload,
    )

    background_tasks.add_task(slack_manager.receive_message, message)
    return JSONResponse({'success': True})


@slack_router.post('/on-options-load')
async def on_options_load(request: Request, background_tasks: BackgroundTasks):
    """Handle external_select options loading (block_suggestion payload).

    This endpoint is called by Slack when a user interacts with an external_select
    element. It supports dynamic repository search with pagination.

    The endpoint:
    1. Authenticates the Slack user
    2. Searches for repositories matching the user's query
    3. Returns up to 100 options for the dropdown

    Note: "No Repository" is handled by a separate button in the form, so it's
    not included in the dropdown options. Error cases return an empty list.

    Configuration: Set the Options Load URL in Slack App settings to:
    https://your-domain/slack/on-options-load
    """
    if not SLACK_WEBHOOKS_ENABLED:
        return JSONResponse({'options': []})

    body = await request.body()
    form = await request.form()
    payload_str = form.get('payload')
    if not isinstance(payload_str, str) or not payload_str:
        logger.warning('slack_on_options_load: No payload in request')
        return JSONResponse({'options': []})

    payload = json.loads(payload_str)

    logger.info('slack_on_options_load', extra={'event_type': payload.get('type')})

    # Verify the signature
    if not SLACK_SIGNING_SECRET or not signature_verifier.is_valid(
        body=body,
        timestamp=request.headers.get('X-Slack-Request-Timestamp', ''),
        signature=request.headers.get('X-Slack-Signature', ''),
    ):
        raise HTTPException(status_code=403, detail='invalid_request')

    # Verify this is a block_suggestion payload
    if payload.get('type') != 'block_suggestion':
        logger.warning(
            f'slack_on_options_load: Unexpected payload type: {payload.get("type")}'
        )
        return JSONResponse({'options': []})

    slack_user_id = payload['user']['id']
    search_value = payload.get('value', '')  # What user typed in the search box

    # Authenticate user
    slack_user, saas_user_auth = await slack_manager.authenticate_user(slack_user_id)

    if not slack_user or not saas_user_auth:
        # Send ephemeral message asking user to link their account
        background_tasks.add_task(
            slack_manager.handle_slack_error,
            payload,
            SlackError(
                SlackErrorCode.USER_NOT_AUTHENTICATED,
                message_kwargs={'login_link': _generate_login_link()},
                log_context={'slack_user_id': slack_user_id},
            ),
        )
        return JSONResponse({'options': []})

    try:
        # Search for repositories matching the query
        # Limit to 20 repos for fast initial load. Users can search for repos
        # not in this list using the type-ahead search functionality.
        options = await slack_manager.search_repos_for_slack(
            saas_user_auth, query=search_value, per_page=20
        )

        logger.info(
            'slack_on_options_load_success',
            extra={
                'slack_user_id': slack_user_id,
                'search_value': search_value,
                'num_options': len(options),
            },
        )

        return JSONResponse({'options': options})

    except ProviderTimeoutError as e:
        # Handle provider timeout with user notification
        background_tasks.add_task(
            slack_manager.handle_slack_error,
            payload,
            SlackError(
                SlackErrorCode.PROVIDER_TIMEOUT,
                log_context={'slack_user_id': slack_user_id, 'error': str(e)},
            ),
        )
        return JSONResponse({'options': []})

    except Exception as e:
        logger.exception(
            'slack_options_load_error',
            extra={
                'slack_user_id': slack_user_id,
                'search_value': search_value,
            },
            stack_info=True,
        )
        # Notify user about the unexpected error with error code
        background_tasks.add_task(
            slack_manager.handle_slack_error,
            payload,
            SlackError(
                SlackErrorCode.UNEXPECTED_ERROR,
                log_context={'slack_user_id': slack_user_id, 'error': str(e)},
            ),
        )
        return JSONResponse({'options': []})


@slack_router.post('/on-form-interaction')
async def on_form_interaction(request: Request, background_tasks: BackgroundTasks):
    """Handle repository selection form submission.

    When a user selects a repository from the external_select dropdown,
    this endpoint passes the payload to the manager which retrieves the
    original user message from Redis and starts the conversation.
    """
    if not SLACK_WEBHOOKS_ENABLED:
        return JSONResponse({'success': 'slack_webhooks_disabled'})

    body = await request.body()
    form = await request.form()
    payload_str = form.get('payload')
    if not isinstance(payload_str, str) or not payload_str:
        raise HTTPException(400, 'Missing Slack payload')
    payload = json.loads(payload_str)

    logger.info('slack_on_form_interaction', extra={'event_type': payload.get('type')})

    # Verify the signature
    if not SLACK_SIGNING_SECRET or not signature_verifier.is_valid(
        body=body,
        timestamp=request.headers.get('X-Slack-Request-Timestamp', ''),
        signature=request.headers.get('X-Slack-Signature', ''),
    ):
        raise HTTPException(status_code=403, detail='invalid_request')

    assert payload['type'] == 'block_actions'

    background_tasks.add_task(slack_manager.receive_form_interaction, payload)
    return JSONResponse({'success': True})


def _generate_login_link(state: str = '') -> str:
    """Generate the OAuth login link for Slack authentication."""
    return slack_login_url(state, authorize_url_generator.generate)


def _html_response(title: str, description: str, status_code: int) -> HTMLResponse:
    content = (
        '<style>body{background:#0d0f11;color:#ecedee;font-family:sans-serif;display:flex;justify-content:center;align-items:center;}</style>'
        '<div style="box-sizing:border-box;border:1px solid #454545;padding:24px;width:384px;background:#24272e;border-radius:0.75rem;text-align:center;">'
        f'<h1 style="font-size:24px;">{title}</h1>'
        f'<p>{description}</p>'
        '<div>'
    )
    return HTMLResponse(
        content=content,
        status_code=status_code,
    )
