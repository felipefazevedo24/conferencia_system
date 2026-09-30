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

  it("shows the effective delivery date returned for the service order", async () => {
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

    expect(await screen.findByText("02/09/2026")).toBeVisible();
    expect(screen.queryByText("Sem data")).not.toBeInTheDocument();
  });

  it("counts a budget only once when it has deliveries on different dates", async () => {
    mockedApi.getScheduledDeliveries.mockResolvedValueOnce([
      {
        orcamento: "7375", versao: "", cliente: "Cliente", descricao: "",
        classificacoes: ["MOLDE"], data_entrega: "2026-10-15", status: "",
        percentual: null,
        os: [{ numero: "10507", descricao: "OS 1", principal: true, status: "" }]
      },
      {
        orcamento: "7375", versao: "", cliente: "Cliente", descricao: "",
        classificacoes: ["MOLDE"], data_entrega: "2026-10-22", status: "",
        percentual: null,
        os: [{ numero: "10509", descricao: "OS 2", principal: true, status: "" }]
      }
    ]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <DeliveryExplosion month={10} year={2026} classification="MOLDE" search=""
        onClose={vi.fn()} onSelectOrder={vi.fn()} />,
      { wrapper: wrapper(queryClient) }
    );

    await screen.findAllByRole("button", { name: /7375/ });
    const summary = screen.getByLabelText("Resumo do período");
    expect(summary).toHaveTextContent(/1\s*orçamentos/);
    expect(summary).toHaveTextContent(/2\s*ordens de serviço/);
  });
});
