import { Sparkles } from "lucide-react";
import { Trans } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import { cn } from "#/utils/utils";

const AGENT_CANVAS_LABEL = "app.all-hands.dev/canvas";
const AGENT_CANVAS_URL = `https://${AGENT_CANVAS_LABEL}`;

export function AgentCanvasBanner() {
  return (
    <div
      data-testid="agent-canvas-banner"
      className={cn(
        "mx-4 flex w-[calc(100%-2rem)] max-w-[1334px] items-center justify-center gap-4",
        "rounded-xl border border-white/15 px-5 py-5 text-center backdrop-blur-md",
        "bg-[linear-gradient(180deg,rgba(255,255,255,0.12)_0%,rgba(255,255,255,0.06)_100%)]",
        "shadow-[0_24px_60px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.12)]",
        "sm:mx-6 sm:gap-6 sm:px-8 md:py-6 lg:w-full",
      )}
    >
      <Sparkles
        aria-hidden="true"
        className="size-8 shrink-0 text-white sm:size-10"
        strokeWidth={1.7}
      />
      <Typography.Paragraph className="text-base font-semibold leading-6 text-white sm:text-xl md:text-[22px] md:leading-8">
        <Trans
          i18nKey={I18nKey.HOME$AGENT_CANVAS_BANNER}
          components={{
            link: (
              <a
                href={AGENT_CANVAS_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block cursor-pointer border-b-2 border-white pb-0.5 text-white underline decoration-white decoration-2 underline-offset-4 transition-colors hover:border-white/80 hover:text-white/80 hover:decoration-white/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white"
              >
                {AGENT_CANVAS_LABEL}
              </a>
            ),
          }}
        />
      </Typography.Paragraph>
    </div>
  );
}
