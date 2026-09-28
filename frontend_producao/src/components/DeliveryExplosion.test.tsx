import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../lib/api";
import { DeliveryExplosion } from "./DeliveryExplosion";

vi.mock("../lib/api", () => ({
  api: {
    getScheduledDeliveries: vi.fn(),
    getDeliveryStructure: vi.fn()
  }
}));

const mockedApi = vi.mocked(api);

function wrapper(queryClient: QueryClient) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("DeliveryExplosion", () => {
  beforeEach(() => {
    mockedApi.getScheduledDeliveries.mockResolvedValue([
      {
        orcamento: "6907",
        versao: "",
        cliente: "Columbia Machine",
        descricao: "",
        classificacoes: ["MOLDE"],
        data_entrega: "2026-09-02",
        status: "",
        percentual: null,
        os: [{ numero: "9644", descricao: "OS principal", principal: true, status: "" }]
      }
    ]);
    mockedApi.getDeliveryStructure.mockResolvedValue({ nos: [] });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("starts collapsed and returns to the initial state after a second click", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <DeliveryExplosion
        month={9}
        year={2026}
        classification="MOLDE"
        search=""
        onClose={vi.fn()}
        onSelectOrder={vi.fn()}
      />,
      { wrapper: wrapper(queryClient) }
    );

    const budget = await screen.findByRole("button", { name: /6907/ });
    expect(budget).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: /OS 9644/ })).not.toBeInTheDocument();

    fireEvent.click(budget);
    expect(budget).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /OS 9644/ })).toBeVisible();

    fireEvent.click(budget);
    expect(budget).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: /OS 9644/ })).not.toBeInTheDocument();
  });
});
