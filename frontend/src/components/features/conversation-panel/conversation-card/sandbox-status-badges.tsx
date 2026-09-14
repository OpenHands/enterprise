import { FaArchive, FaQuestionCircle } from "react-icons/fa";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { V1SandboxStatus } from "#/api/sandbox-service/sandbox-service.types";

interface SandboxStatusBadgesProps {
  sandboxStatus?: V1SandboxStatus;
}

export function SandboxStatusBadges({
  sandboxStatus,
}: SandboxStatusBadgesProps): React.JSX.Element | null {
  const { t } = useTranslation();

  if (sandboxStatus !== "MISSING" && sandboxStatus !== "UNKNOWN") {
    return null;
  }

  return (
    <span className="flex items-center gap-1 px-1.5 py-0.5 bg-[#868E96] text-white text-xs font-medium rounded-full opacity-60">
      {sandboxStatus === "MISSING" ? (
        <FaArchive size={10} className="text-white" />
      ) : (
        <FaQuestionCircle size={10} className="text-white" />
      )}
      <span>
        {t(
          sandboxStatus === "MISSING"
            ? I18nKey.COMMON$ARCHIVED
            : I18nKey.SANDBOX$TEMPORARILY_UNAVAILABLE,
        )}
      </span>
    </span>
  );
}
