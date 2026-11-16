import React from "react";
import { HorizontalScrollFade } from "#/components/shared/horizontal-scroll-fade";

interface MarkdownTableScrollProps {
  children: React.ReactNode;
}

export function MarkdownTableScroll({ children }: MarkdownTableScrollProps) {
  return (
    <HorizontalScrollFade testId="markdown-table-scroll">
      {children}
    </HorizontalScrollFade>
  );
}
