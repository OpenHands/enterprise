import { useEffect, useMemo } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import EventService from "#/api/event-service/event-service.api";
import { useUserConversation } from "#/hooks/query/use-user-conversation";

export const useConversationHistory = (conversationId?: string) => {
  const { data: conversation, isFetched: isConversationFetched } =
    useUserConversation(conversationId ?? null);

  // Sandbox MISSING = archived: no WebSocket replay will ever arrive, so the
  // REST preload is the only event source and must fetch every page.
  const isArchived = conversation?.sandbox_status === "MISSING";

  const {
    data,
    isFetched: isQueryFetched,
    isLoading,
    isError,
    error,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    refetch,
  } = useInfiniteQuery({
    queryKey: ["conversation-history", conversationId],
    enabled: !!conversationId && !!conversation,
    queryFn: async ({ pageParam }) => {
      if (!conversationId) return { items: [], next_page_id: null };

      return pageParam
        ? EventService.searchEventsV1(conversationId, pageParam)
        : EventService.searchEventsV1(conversationId);
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_page_id ?? undefined,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000, // 30 minutes — survive navigation away and back (AC5)
  });

  // Archived conversations: progressively drain every remaining page.
  useEffect(() => {
    if (isArchived && hasNextPage && !isFetchingNextPage && !isError) {
      fetchNextPage({ cancelRefetch: false });
    }
  }, [isArchived, hasNextPage, isFetchingNextPage, isError, fetchNextPage]);

  const events = useMemo(
    () => data?.pages.flatMap((page) => page.items),
    [data],
  );

  return {
    data: events,
    isLoading,
    isError,
    error,
    // True while the archived drain still has pages left to fetch.
    isFetchingMore:
      isArchived && !isError && (!!hasNextPage || isFetchingNextPage),
    fetchNextPage,
    refetch,
    // Query is considered fetched when:
    // 1. Conversation data is fetched AND history query has run, OR
    // 2. Conversation doesn't exist (isConversationFetched && !conversation)
    isFetched: isQueryFetched || (isConversationFetched && !conversation),
  };
};
