import { AgentCanvasBanner } from "./agent-canvas-banner";
import { GuideMessage } from "./guide-message";
import { HomeHeaderTitle } from "./home-header-title";

interface HomeHeaderProps {
  showAgentCanvasBanner?: boolean;
}

export function HomeHeader({ showAgentCanvasBanner = false }: HomeHeaderProps) {
  return (
    <header className="flex flex-col items-center gap-12">
      {showAgentCanvasBanner ? <AgentCanvasBanner /> : <GuideMessage />}
      <HomeHeaderTitle />
    </header>
  );
}
