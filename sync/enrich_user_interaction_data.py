import asyncio

from integrations.github.data_collector import GitHubDataCollector
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.openhands_pr import OpenhandsPR
from storage.openhands_pr_store import OpenhandsPRStore

PROCESS_AMOUNT = 50
MAX_RETRIES = 3

store = OpenhandsPRStore.get_instance()
data_collector = GitHubDataCollector()


async def get_unprocessed_prs() -> list[OpenhandsPR]:
    """
    Get unprocessed PR entries from the OpenhandsPR table.

    Args:
        limit: Maximum number of PRs to retrieve (default: 50)

    Returns:
        List of OpenhandsPR objects that need processing
    """
    unprocessed_prs = await store.get_unprocessed_prs(PROCESS_AMOUNT, MAX_RETRIES)
    logger.info(f'Retrieved {len(unprocessed_prs)} unprocessed PRs for enrichment')
    return unprocessed_prs


async def process_pr(pr: OpenhandsPR) -> bool:
    """
    Process a single PR to enrich its data.

    Returns True when this run claimed the PR and enriched it, False when
    another run had already processed it or it is out of attempts.
    """

    claimed = await store.claim_pr_for_processing(pr.repo_id, pr.pr_number, MAX_RETRIES)
    if claimed is None:
        logger.info(
            f'Skipping PR #{pr.pr_number} from repo {pr.repo_name}: '
            'already processed or out of attempts'
        )
        return False

    logger.info(f'Processing PR #{pr.pr_number} from repo {pr.repo_name}')
    await data_collector.save_full_pr(claimed)
    return True


async def main():
    """
    Main function to retrieve and process unprocessed PRs.
    """
    logger.info('Starting PR data enrichment process')

    # Get unprocessed PRs
    unprocessed_prs = await get_unprocessed_prs()
    logger.info(f'Found {len(unprocessed_prs)} PRs to process')

    # Process each PR
    for pr in unprocessed_prs:
        try:
            if await process_pr(pr):
                logger.info(
                    f'Successfully processed PR #{pr.pr_number} from repo {pr.repo_name}'
                )
        except Exception:
            logger.exception(
                f'Error processing PR #{pr.pr_number} from repo {pr.repo_name}',
                stack_info=True,
            )

    logger.info('PR data enrichment process completed')


if __name__ == '__main__':
    asyncio.run(main())
