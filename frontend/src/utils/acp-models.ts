import type { V1ACPModelInfo } from "#/api/conversation-service/v1-conversation-service.types";
import type { ACPModelOption } from "#/api/option-service/option.types";

/** The name the session gave a model id, else the id itself. */
export function acpModelLabel(
  models: V1ACPModelInfo[] | null | undefined,
  modelId: string,
): string {
  const match = models?.find((model) => model.model_id === modelId);
  return match?.name?.trim() || modelId;
}

/** Picker options from the models an ACP session reported. */
export function toAcpModelOptions(
  models: V1ACPModelInfo[] | null | undefined,
): ACPModelOption[] {
  return (models ?? []).map((model) => ({
    id: model.model_id,
    label: acpModelLabel(models, model.model_id),
  }));
}
