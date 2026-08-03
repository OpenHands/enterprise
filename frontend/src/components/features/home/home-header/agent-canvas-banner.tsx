import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";

const AGENT_CANVAS_URL = "https://app.all-hands.dev/canvas";

export function AgentCanvasBanner() {
  const { t } = useTranslation();

  return (
    <div
      data-testid="agent-canvas-banner"
      className="mx-4 flex w-[calc(100%-2rem)] max-w-[1334px] items-center justify-center gap-4 rounded-xl border border-white/15 bg-[linear-gradient(180deg,rgba(255,255,255,0.12)_0%,rgba(255,255,255,0.06)_100%)] px-5 py-5 text-center shadow-[0_24px_60px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.12)] backdrop-blur-md sm:mx-6 sm:gap-6 sm:px-8 md:py-6 lg:w-full"
    >
      <Sparkles
        aria-hidden="true"
        className="size-8 shrink-0 text-white sm:size-10"
        strokeWidth={1.7}
      />
      <p className="text-base font-semibold leading-6 text-white sm:text-xl md:text-[22px] md:leading-8">
        {t(I18nKey.HOME$AGENT_CANVAS_BANNER_PREFIX)}{" "}
        <a
          href={AGENT_CANVAS_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="underline underline-offset-2 transition-colors hover:text-white/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white"
        >
          {t(I18nKey.HOME$AGENT_CANVAS_BANNER_LINK)}
        </a>{" "}
        {t(I18nKey.HOME$AGENT_CANVAS_BANNER_SUFFIX)}
      </p>
    </div>
  );
}
