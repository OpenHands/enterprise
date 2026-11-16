"""Git router for OpenHands App Server V1 API.

This module provides V1 API endpoints for Git operations (installations, repositories)
with pagination support. These endpoints are designed to replace the legacy V0 endpoints
in openhands/server/routes/git.py.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query, status

from openhands.app_server.config import depends_user_context, get_global_config
from openhands.app_server.git.git_models import (
    BranchPage,
    InstallationPage,
    RepositoryPage,
    SortOrder,
    SuggestedTaskPage,
)
from openhands.app_server.integrations.provider import ProviderHandler
from openhands.app_server.integrations.service_types import (
    ProviderType,
    Repository,
    SuggestedTask,
)
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.app_server.utils.paging_utils import (
    decode_page_id,
    encode_page_id,
    paginate_results,
)

if TYPE_CHECKING:
    from openhands.app_server.integrations.provider import PROVIDER_TOKEN_TYPE

# We use the get_dependencies method here to signal to the OpenAPI docs that this endpoint
# is protected. The actual protection is provided by SetAuthCookieMiddleware
router = APIRouter(
    prefix='/git',
    tags=['Git'],
    dependencies=get_dependencies(),
)
user_context_dependency = depends_user_context()


async def _get_client(
    user_context: UserContext,
    provider: ProviderType | None,
    git_provider_id: int | None,
) -> ProviderHandler:
    """The provider handler for a request.

    With a ``git_provider_id`` the user context honors, the handler uses that
    connected provider's token for its type. Otherwise it holds the user's
    tokens as they are.
    """
    if git_provider_id is not None:
        selection = await user_context.select_git_provider(provider, git_provider_id)
        if selection.git_provider_id is not None:
            return await user_context.get_provider_handler(selection.git_provider_id)

    provider_tokens = await user_context.get_provider_tokens()
    if not provider_tokens:
        # User is authenticated but has no git provider connected
        # Return 403 Forbidden (not 401) to avoid triggering frontend logout
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Git provider token required (such as GitHub).',
        )

    user_id = await user_context.get_user_id()
    typed_provider_tokens: PROVIDER_TOKEN_TYPE = provider_tokens  # type: ignore[assignment]
    return ProviderHandler(
        provider_tokens=MappingProxyType(typed_provider_tokens),
        external_auth_id=user_id,
    )


@router.get('/installations/search')
async def search_user_installations(
    provider: ProviderType,
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(title='The max number of results in the page', gt=0, le=100),
    ] = 100,
    git_provider_id: Annotated[
        int | None,
        Query(
            title=(
                'The connected git provider (an auth_providers id) to use, when '
                'the user connected several of the same type. Ignored unless OAuth '
                'v2 login is enabled.'
            )
        ),
    ] = None,
    user_context: UserContext = user_context_dependency,
) -> InstallationPage:
    """Get user installations (GitHub apps) or equivalent for other providers.

    Returns a paginated list of installation IDs or workspace IDs depending on the provider.
    Providers without installations (e.g. GitLab) return an empty page.
    """
    client = await _get_client(user_context, provider, git_provider_id)

    if provider == ProviderType.GITHUB:
        installations = await client.get_github_installations()
    elif provider == ProviderType.BITBUCKET:
        installations = await client.get_bitbucket_workspaces()
    elif provider == ProviderType.BITBUCKET_DATA_CENTER:
        installations = await client.get_bitbucket_dc_projects()
    elif provider == ProviderType.AZURE_DEVOPS:
        installations = await client.get_azure_devops_organizations()
    else:
        # GitLab, Forgejo, ... list repositories directly, with no container
        # level above them, so they have no installations.
        return InstallationPage(items=[])

    items, next_page_id = paginate_results(installations, page_id, limit)
    return InstallationPage(items=items, next_page_id=next_page_id)


@router.get('/repositories/search')
async def search_repositories(
    provider: ProviderType,
    query: Annotated[
        str | None,
        Query(
            title='Search query for finding repositories. If not provided, returns user repositories.'
        ),
    ] = None,
    installation_id: Annotated[
        str | None,
        Query(title='Filter by installation/app ID'),
    ] = None,
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(title='Results per page; a query search can return more', gt=0, le=100),
    ] = 100,
    sort_order: Annotated[
        SortOrder | None,
        Query(title='Sort order for search results (e.g., stars-desc, forks-asc)'),
    ] = None,
    git_provider_id: Annotated[
        int | None,
        Query(
            title=(
                'The connected git provider (an auth_providers id) to use, when '
                'the user connected several of the same type. Ignored unless OAuth '
                'v2 login is enabled.'
            )
        ),
    ] = None,
    user_context: UserContext = user_context_dependency,
) -> RepositoryPage:
    """Get or search repositories.

    If query is provided, searches repositories across the git provider (first page only).
    If query is not provided, returns a paginated list of the authenticated user's repositories.
    """
    client = await _get_client(user_context, provider, git_provider_id)

    page = 1
    decoded_page_id = decode_page_id(page_id)
    if decoded_page_id is not None:
        page = decoded_page_id

    # If query is provided, use search; otherwise get user's repositories
    if query:
        if page != 1:
            # TODO: Support pagination for repository search after refactoring.
            # search_repositories takes no page number, so a later page would
            # only repeat the first page.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Pagination not yet supported for repository search queries. Use empty query to list repositories with pagination.',
            )
        # Parse sort_order into sort and order components (if provided)
        if sort_order:
            search_sort, order = sort_order.value.rsplit('-', 1)
        else:
            search_sort = 'stars'
            order = 'desc'

        repos: list[Repository] = await client.search_repositories(
            selected_provider=provider,
            query=query,
            per_page=limit,
            sort=search_sort,
            order=order,
            app_mode=get_global_config().app_mode,
        )
        # Search results are a single page, so there is no next page. Do not
        # cut them to limit: some providers search the user and each org with
        # per_page=limit, so the merged results can be longer than limit.
        return RepositoryPage(items=repos, next_page_id=None)
    else:
        if sort_order:
            # TODO: This is a temporary state until we refactor the underlying API.
            # The get_repositories method does not support sorting in the same way as
            # the search method - those should be merged into a single paginated
            # method
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='sort_order is not supported when listing user repositories. It will be supported after API refactoring.',
            )
        # TODO: The underlying API needs refactoring.
        # The providers use page-number pagination, so per_page must be equal
        # to limit. A larger per_page moves each page window and skips items.
        repos = await client.get_repositories(
            sort='pushed',
            app_mode=get_global_config().app_mode,
            selected_provider=provider,
            page=page,
            per_page=limit,
            installation_id=installation_id,
        )

    # A full page can have more results after it. When the total is a multiple
    # of limit, the client gets one empty last page.
    next_page_id = encode_page_id(page + 1) if len(repos) >= limit else None

    return RepositoryPage(items=repos, next_page_id=next_page_id)


@router.get('/repositories/details')
async def get_repository_details(
    provider: ProviderType,
    repository: Annotated[
        str,
        Query(title='Repository name in format owner/repo'),
    ],
    git_provider_id: Annotated[
        int | None,
        Query(
            title=(
                'The connected git provider (an auth_providers id) to use, when '
                'the user connected several of the same type. Ignored unless OAuth '
                'v2 login is enabled.'
            )
        ),
    ] = None,
    user_context: UserContext = user_context_dependency,
) -> Repository:
    """Get one repository, including its default branch.

    Repository lists leave main_branch empty for providers that need one more
    request per repository to read it (e.g. Bitbucket Data Center).
    """
    client = await _get_client(user_context, provider, git_provider_id)

    try:
        # Ask only this provider: verify_repo_provider would try the others too
        service = client.get_service(provider)
        return await service.get_repository_details_from_repo_name(repository)
    except Exception as e:
        logger.warning(
            f'Error getting repository {repository} from {provider.value}: {e}'
        )
        # 404, not the provider's own status: a 401 would log the frontend out
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Repository not found.',
        )


@router.get('/branches/search')
async def search_branches(
    provider: ProviderType,
    repository: Annotated[
        str,
        Query(title='Repository name in format owner/repo'),
    ],
    query: Annotated[
        str,
        Query(title='Branch name search query'),
    ],
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(title='The max number of results in the page', gt=0, le=100),
    ] = 30,
    git_provider_id: Annotated[
        int | None,
        Query(
            title=(
                'The connected git provider (an auth_providers id) to use, when '
                'the user connected several of the same type. Ignored unless OAuth '
                'v2 login is enabled.'
            )
        ),
    ] = None,
    user_context: UserContext = user_context_dependency,
) -> BranchPage:
    """Search branches in a repository.

    Returns a paginated list of branches matching the search query.
    """
    client = await _get_client(user_context, provider, git_provider_id)

    if query:
        # Each provider makes its own page_id (a page number or a cursor)
        search_page = await client.search_branches(
            selected_provider=provider,
            repository=repository,
            query=query,
            per_page=limit,
            page_id=page_id,
        )
        return BranchPage(
            items=search_page.branches, next_page_id=search_page.next_page_id
        )

    page = 1
    decoded_page_id = decode_page_id(page_id)
    if decoded_page_id is not None:
        page = decoded_page_id

    # The providers use page-number pagination, so per_page must be equal to
    # limit. A larger per_page moves each page window and skips items.
    current_page = await client.get_branches(
        repository=repository,
        specified_provider=provider,
        page=page,
        per_page=limit,
    )
    next_page_id = (
        encode_page_id(page + 1) if len(current_page.branches) >= limit else None
    )

    return BranchPage(items=current_page.branches, next_page_id=next_page_id)


@router.get('/suggested-tasks/search')
async def search_suggested_tasks(
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(title='The max number of results in the page', gt=0, le=100),
    ] = 30,
    git_provider_id: Annotated[
        int | None,
        Query(
            title=(
                'The connected git provider (an auth_providers id) to use, when '
                'the user connected several of the same type. Ignored unless OAuth '
                'v2 login is enabled.'
            )
        ),
    ] = None,
    user_context: UserContext = user_context_dependency,
) -> SuggestedTaskPage:
    """Get suggested tasks for the user.

    Returns a paginated list of suggested tasks including:
    - PRs owned by the user
    - Issues assigned to the user
    """
    client = await _get_client(user_context, None, git_provider_id)

    page = 1
    decoded_page_id = decode_page_id(page_id)
    if decoded_page_id is not None:
        page = decoded_page_id

    # Get suggested tasks - we'll handle pagination ourselves
    # The underlying method doesn't have pagination, so we'll fetch all and paginate
    selected_provider = None
    if git_provider_id is not None:
        selected_provider = (
            await user_context.select_git_provider(None, git_provider_id)
        ).git_provider
    all_tasks: list[SuggestedTask] = await client.get_suggested_tasks(selected_provider)

    # Paginate results
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    paginated_tasks = all_tasks[start_idx:end_idx]

    next_page_id = None
    if end_idx < len(all_tasks):
        next_page_id = encode_page_id(page + 1)

    return SuggestedTaskPage(items=paginated_tasks, next_page_id=next_page_id)
