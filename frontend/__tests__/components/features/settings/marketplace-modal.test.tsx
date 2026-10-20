import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  MarketplaceModal,
  normalizeMarketplaceSource,
} from "#/components/features/settings/marketplace-modal";
import { renderWithProviders } from "test-utils";
import SkillsService, { MarketplaceCheckResponse } from "#/api/skills-service";

const onClose = vi.fn();
const onSave = vi.fn();

const baseProps = {
  isOpen: true,
  mode: "add" as const,
  onClose,
  onSave,
  isSaving: false,
};

const checkResult = (
  overrides: Partial<MarketplaceCheckResponse> = {},
): MarketplaceCheckResponse => ({
  status: "ok",
  reason: null,
  plugin_count: 1,
  skill_count: 0,
  ...overrides,
});

const checkSpy = vi.spyOn(SkillsService, "checkMarketplace");

beforeEach(() => {
  onClose.mockReset();
  onSave.mockReset();
  checkSpy.mockReset();
  checkSpy.mockResolvedValue(checkResult());
});

describe("MarketplaceModal", () => {
  it("renders nothing when not open", () => {
    const { container } = renderWithProviders(
      <MarketplaceModal {...baseProps} isOpen={false} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("renders form fields in add mode", () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);

    // Form fields are identified by their text content
    expect(screen.getByText("SETTINGS$MARKETPLACE_NAME")).toBeInTheDocument();
    expect(screen.getByText("SETTINGS$MARKETPLACE_SOURCE")).toBeInTheDocument();
    expect(screen.getByText("SETTINGS$MARKETPLACE_REF")).toBeInTheDocument();
    expect(
      screen.getByText("SETTINGS$MARKETPLACE_REPO_PATH"),
    ).toBeInTheDocument();
  });

  it("shows name required error when name is empty", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    // Click save without entering anything
    await user.click(screen.getByTestId("marketplace-save-button"));

    expect(
      screen.getByText("SETTINGS$MARKETPLACE_NAME_REQUIRED"),
    ).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("shows name invalid error for invalid name format", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    // Find the name input by placeholder
    const nameInput = screen.getByPlaceholderText("e.g., my-skills");
    await user.type(nameInput, "123-invalid");
    await user.click(screen.getByTestId("marketplace-save-button"));

    expect(
      screen.getByText("SETTINGS$MARKETPLACE_NAME_INVALID"),
    ).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("validates that onSave is not called when source is empty", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    // Fill in name but not source
    const nameInput = screen.getByPlaceholderText("e.g., my-skills");
    await user.type(nameInput, "valid-name");
    await user.click(screen.getByTestId("marketplace-save-button"));

    // onSave should NOT have been called due to validation
    expect(onSave).not.toHaveBeenCalled();
  });

  it("clears name error when user starts typing", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    // Trigger name required error
    await user.click(screen.getByTestId("marketplace-save-button"));
    expect(
      screen.getByText("SETTINGS$MARKETPLACE_NAME_REQUIRED"),
    ).toBeInTheDocument();

    // Start typing in name field
    const nameInput = screen.getByPlaceholderText("e.g., my-skills");
    await user.type(nameInput, "a");

    // Error should be cleared
    expect(
      screen.queryByText("SETTINGS$MARKETPLACE_NAME_REQUIRED"),
    ).not.toBeInTheDocument();
  });

  it("validates source before calling onSave", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    // Fill name but not source
    const nameInput = screen.getByPlaceholderText("e.g., my-skills");
    await user.type(nameInput, "valid-name");
    await user.click(screen.getByTestId("marketplace-save-button"));

    // onSave should not be called
    expect(onSave).not.toHaveBeenCalled();

    // After filling source, onSave should be called
    const sourceInput = screen.getByPlaceholderText("github:owner/repo");
    await user.type(sourceInput, "github:owner/repo");
    await user.click(screen.getByTestId("marketplace-save-button"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("calls onSave with correct data when form is valid", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await user.type(
      screen.getByPlaceholderText("e.g., my-skills"),
      "my-marketplace",
    );
    await user.type(
      screen.getByPlaceholderText("github:owner/repo"),
      "github:owner/repo",
    );
    await user.click(screen.getByTestId("marketplace-save-button"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalledWith({
      name: "my-marketplace",
      source: "github:owner/repo",
      ref: undefined,
      repo_path: undefined,
      auto_load: false,
    });
  });

  it("rejects a URL entered in the repository path field", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await user.type(
      screen.getByPlaceholderText("e.g., my-skills"),
      "oh-extensions",
    );
    await user.type(
      screen.getByPlaceholderText("github:owner/repo"),
      "github:OpenHands/extensions",
    );
    await user.type(
      screen.getByPlaceholderText("e.g., marketplaces/internal"),
      "https://github.com/OpenHands/extensions",
    );
    await user.click(screen.getByTestId("marketplace-save-button"));

    expect(
      screen.getByText("SETTINGS$MARKETPLACE_REPO_PATH_INVALID"),
    ).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("saves a repository address without a scheme as https", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await user.type(screen.getByPlaceholderText("e.g., my-skills"), "acme");
    await user.type(
      screen.getByPlaceholderText("github:owner/repo"),
      "github.com/acme/skills",
    );

    expect(
      screen.getByText("SETTINGS$MARKETPLACE_SOURCE_SAVED_AS"),
    ).toBeInTheDocument();

    await user.click(screen.getByTestId("marketplace-save-button"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ source: "https://github.com/acme/skills" }),
    );
  });

  it.each([
    "github:acme/skills",
    "https://github.com/acme/skills",
    "marketplaces/internal",
  ])("saves the source %s unchanged", async (source) => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await user.type(screen.getByPlaceholderText("e.g., my-skills"), "acme");
    await user.type(screen.getByPlaceholderText("github:owner/repo"), source);

    expect(
      screen.queryByText("SETTINGS$MARKETPLACE_SOURCE_SAVED_AS"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByTestId("marketplace-save-button"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ source }));
  });

  it("accepts a relative subdirectory as the repository path", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await user.type(screen.getByPlaceholderText("e.g., my-skills"), "team");
    await user.type(
      screen.getByPlaceholderText("github:owner/repo"),
      "github:acme/monorepo",
    );
    await user.type(
      screen.getByPlaceholderText("e.g., marketplaces/internal"),
      "marketplaces/internal",
    );
    await user.click(screen.getByTestId("marketplace-save-button"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ repo_path: "marketplaces/internal" }),
    );
  });

  it.each([
    ["github.com/acme/skills", "https://github.com/acme/skills"],
    [
      "bitbucket.example.com:7990/scm/proj/skills.git",
      "https://bitbucket.example.com:7990/scm/proj/skills.git",
    ],
    ["github:acme/skills", "github:acme/skills"],
    ["marketplaces/internal", "marketplaces/internal"],
  ])("normalizes the source %s to %s", (source, expected) => {
    expect(normalizeMarketplaceSource(source)).toBe(expected);
  });

  const fillAndSave = async (
    user: ReturnType<typeof userEvent.setup>,
    source = "github:acme/skills",
  ) => {
    await user.type(screen.getByPlaceholderText("e.g., my-skills"), "acme");
    await user.type(screen.getByPlaceholderText("github:owner/repo"), source);
    await user.click(screen.getByTestId("marketplace-save-button"));
  };

  it("checks the marketplace as it will be saved before saving", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user, "github.com/acme/skills");

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(checkSpy).toHaveBeenCalledWith({
      name: "acme",
      source: "https://github.com/acme/skills",
      ref: undefined,
      repo_path: undefined,
      auto_load: false,
    });
    expect(
      screen.queryByTestId("marketplace-check-message"),
    ).not.toBeInTheDocument();
  });

  it("explains why a marketplace will not load and does not save it", async () => {
    checkSpy.mockResolvedValue(
      checkResult({ status: "error", reason: "not_found" }),
    );
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user);

    expect(
      await screen.findByTestId("marketplace-check-message"),
    ).toHaveTextContent("SETTINGS$MARKETPLACE_CHECK_NOT_FOUND");
    expect(onSave).not.toHaveBeenCalled();

    // Saving again checks again rather than saving regardless.
    await user.click(screen.getByTestId("marketplace-save-button"));

    await waitFor(() => expect(checkSpy).toHaveBeenCalledTimes(2));
    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not save a repository on a host that is not a connected provider", async () => {
    checkSpy.mockResolvedValue(
      checkResult({ status: "unchecked", reason: "unknown_host" }),
    );
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user, "https://git.example.org/org/skills");

    expect(
      await screen.findByTestId("marketplace-check-message"),
    ).toHaveTextContent("SETTINGS$MARKETPLACE_CHECK_UNKNOWN_HOST");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("explains that a bare owner/repo would be read as a sandbox folder", async () => {
    checkSpy.mockResolvedValue(
      checkResult({ status: "unchecked", reason: "sandbox_folder" }),
    );
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user, "acme/skills");

    expect(
      await screen.findByTestId("marketplace-check-message"),
    ).toHaveTextContent("SETTINGS$MARKETPLACE_CHECK_SANDBOX_FOLDER");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("checks again after the repository changes", async () => {
    checkSpy.mockResolvedValueOnce(
      checkResult({ status: "error", reason: "empty" }),
    );
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user);
    await screen.findByTestId("marketplace-check-message");

    await user.type(screen.getByPlaceholderText("github:owner/repo"), "-v2");

    expect(
      screen.queryByTestId("marketplace-check-message"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("marketplace-save-button")).toHaveTextContent(
      "BUTTON$SAVE",
    );

    await user.click(screen.getByTestId("marketplace-save-button"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(checkSpy).toHaveBeenCalledTimes(2);
  });

  it("notes a skills-only repository and saves it", async () => {
    checkSpy.mockResolvedValue(
      checkResult({ layout: "skills", plugin_count: 0, skill_count: 3 }),
    );
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user);

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(screen.getByTestId("marketplace-check-message")).toHaveTextContent(
      "SETTINGS$MARKETPLACE_CHECK_SKILLS_ONLY",
    );
  });

  it("adds no note for a repository with a marketplace file", async () => {
    checkSpy.mockResolvedValue(checkResult({ layout: "marketplace" }));
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user);

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(
      screen.queryByTestId("marketplace-check-message"),
    ).not.toBeInTheDocument();
  });

  it("warns that a plugin without a marketplace file loads only its skills", async () => {
    checkSpy.mockResolvedValue(
      checkResult({ layout: "plugin", plugin_count: 0, skill_count: 2 }),
    );
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user);

    expect(
      await screen.findByTestId("marketplace-check-message"),
    ).toHaveTextContent("SETTINGS$MARKETPLACE_CHECK_PLUGIN_SKILLS_ONLY");
    expect(screen.getByTestId("marketplace-save-button")).toHaveTextContent(
      "SETTINGS$MARKETPLACE_SAVE_ANYWAY",
    );
    expect(onSave).not.toHaveBeenCalled();

    // Saving anyway saves without checking again.
    await user.click(screen.getByTestId("marketplace-save-button"));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(checkSpy).toHaveBeenCalledTimes(1);
  });

  it("checks again when the plugin's repository changes", async () => {
    checkSpy.mockResolvedValueOnce(
      checkResult({ layout: "plugin", plugin_count: 0, skill_count: 2 }),
    );
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user);
    await screen.findByTestId("marketplace-check-message");

    await user.type(screen.getByPlaceholderText("github:owner/repo"), "-v2");

    expect(screen.getByTestId("marketplace-save-button")).toHaveTextContent(
      "BUTTON$SAVE",
    );
    await user.click(screen.getByTestId("marketplace-save-button"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(checkSpy).toHaveBeenCalledTimes(2);
  });

  it.each([
    [
      "the request fails",
      () => checkSpy.mockRejectedValue(new Error("network")),
    ],
    [
      "the check could not run",
      () =>
        checkSpy.mockResolvedValue(
          checkResult({ status: "unchecked", reason: "failed" }),
        ),
    ],
  ])("saves as before when %s", async (_case, arrange) => {
    arrange();
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user);

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(
      screen.queryByTestId("marketplace-check-message"),
    ).not.toBeInTheDocument();
  });

  it("shows that the check is running and disables Save", async () => {
    let finish: (value: MarketplaceCheckResponse) => void = () => {};
    checkSpy.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user);

    const saveButton = screen.getByTestId("marketplace-save-button");
    expect(saveButton).toHaveTextContent("SETTINGS$MARKETPLACE_CHECKING");
    expect(saveButton).toBeDisabled();

    finish(checkResult());

    await waitFor(() => expect(onSave).toHaveBeenCalled());
  });

  it("does not save from a check that was running when the repository changed", async () => {
    let finish: (value: MarketplaceCheckResponse) => void = () => {};
    checkSpy.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    renderWithProviders(<MarketplaceModal {...baseProps} />);
    const user = userEvent.setup();

    await fillAndSave(user);
    await waitFor(() => expect(checkSpy).toHaveBeenCalledTimes(1));
    await user.type(screen.getByPlaceholderText("github:owner/repo"), "-v2");
    expect(screen.getByTestId("marketplace-save-button")).toHaveTextContent(
      "BUTTON$SAVE",
    );

    finish(checkResult());
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not render scope selector unless scope selection is allowed", () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);

    expect(
      screen.queryByTestId("marketplace-scope-select"),
    ).not.toBeInTheDocument();
  });

  it("renders scope selector when scope selection is allowed (add mode)", () => {
    renderWithProviders(<MarketplaceModal {...baseProps} allowScopeSelection />);

    expect(screen.getByTestId("marketplace-scope-select")).toBeInTheDocument();
  });

  it("includes the chosen scope in onSave when scope selection is allowed", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} allowScopeSelection />);
    const user = userEvent.setup();

    await user.type(screen.getByPlaceholderText("e.g., my-skills"), "team");
    await user.type(
      screen.getByPlaceholderText("github:owner/repo"),
      "github:owner/repo",
    );
    await user.selectOptions(
      screen.getByTestId("marketplace-scope-select"),
      "org",
    );
    await user.click(screen.getByTestId("marketplace-save-button"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ name: "team", scope: "org" }),
    );
  });

  it("calls onClose when cancel is clicked", async () => {
    renderWithProviders(<MarketplaceModal {...baseProps} />);

    await userEvent.click(
      screen.getByRole("button", { name: "BUTTON$CANCEL" }),
    );

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("disables buttons when isSaving is true", () => {
    renderWithProviders(<MarketplaceModal {...baseProps} isSaving={true} />);

    expect(screen.getByTestId("marketplace-save-button")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "BUTTON$CANCEL" }),
    ).toBeDisabled();
  });

  it("renders in edit mode with read-only source", () => {
    renderWithProviders(
      <MarketplaceModal
        {...baseProps}
        mode="edit"
        marketplace={{
          name: "test-marketplace",
          source: "github:owner/repo",
          scope: "personal",
        }}
      />,
    );

    // Source field should be disabled in edit mode
    const sourceInput = screen.getByDisplayValue("github:owner/repo");
    expect(sourceInput).toBeDisabled();
    expect(
      screen.getByText("SETTINGS$MARKETPLACE_SOURCE_READONLY"),
    ).toBeInTheDocument();
  });
});
