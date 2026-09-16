import { axiosResponse } from "../helpers/native-fixtures";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAcceptTos } from "#/hooks/mutation/use-accept-tos";
import AuthService from "#/api/auth-service/auth-service.api";

vi.mock("#/hooks/query/use-config", () => ({ useConfig: () => ({ data: { auth_mode: "native", login_methods: ["password"], app_mode: "saas" }, isLoading: false }) }));
vi.mock("#/utils/handle-capture-consent", () => ({ handleCaptureConsent: vi.fn() }));

describe("native terms acceptance", () => {
  afterEach(() => vi.restoreAllMocks());
  it("updates the native terms guard before navigating after acceptance", async () => {
    vi.spyOn(AuthService, "acceptTos").mockResolvedValue({ ...axiosResponse({ redirect_url: "/destination" }), config: { ...axiosResponse(null).config, data: { redirect_url: "/destination" } } });
    const client = new QueryClient();
    client.setQueryData(["user", "authenticated", "saas", "native"], { authenticated: true, acceptedTos: false });
    function Terms(): React.JSX.Element { const accept = useAcceptTos(); return <button onClick={() => accept.mutate({ redirectUrl: "/destination" })}>Accept terms</button>; }
    const Routes = createRoutesStub([{ path: "/accept-tos", Component: Terms }, { path: "/destination", Component: () => <p>Destination</p> }]);
    render(<QueryClientProvider client={client}><Routes initialEntries={["/accept-tos"]} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Accept terms" }));
    await screen.findByText("Destination");
    expect(client.getQueryData(["user", "authenticated", "saas", "native"])).toEqual({ authenticated: true, acceptedTos: true });
  });
});
