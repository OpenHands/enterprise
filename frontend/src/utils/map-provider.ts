// These are provider names, not user-facing text
export const MAP_PROVIDER = {
  openai: "OpenAI",
  azure: "Azure",
  azure_ai: "Azure AI Studio",
  vertex_ai: "VertexAI",
  palm: "PaLM",
  gemini: "Gemini",
  anthropic: "Anthropic",
  sagemaker: "AWS SageMaker",
  bedrock: "AWS Bedrock",
  mistral: "Mistral AI",
  anyscale: "Anyscale",
  databricks: "Databricks",
  ollama: "Ollama",
  perlexity: "Perplexity AI",
  friendliai: "FriendliAI",
  groq: "Groq",
  fireworks_ai: "Fireworks AI",
  cloudflare: "Cloudflare Workers AI",
  deepinfra: "DeepInfra",
  ai21: "AI21",
  replicate: "Replicate",
  voyage: "Voyage AI",
  openrouter: "OpenRouter",
  openhands: "OpenHands",
  lemonade: "Lemonade",
  clarifai: "Clarifai",
};

// Without a LiteLLM gateway the "openhands" provider is OpenHands Cloud (the
// public proxy, with the user's own key) rather than a bundled proxy.
const OPENHANDS_CLOUD_LABEL = "OpenHands Cloud";

export const mapProvider = (provider: string, litellmEnabled = true) => {
  if (provider === "openhands" && !litellmEnabled) return OPENHANDS_CLOUD_LABEL;
  return Object.keys(MAP_PROVIDER).includes(provider)
    ? MAP_PROVIDER[provider as keyof typeof MAP_PROVIDER]
    : provider;
};

export const getProviderId = (displayName: string): string => {
  if (displayName === OPENHANDS_CLOUD_LABEL) return "openhands";
  const entry = Object.entries(MAP_PROVIDER).find(
    ([, value]) => value === displayName,
  );
  return entry ? entry[0] : displayName;
};
