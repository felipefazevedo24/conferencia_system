import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../lib/api";
import type { AssemblyNode, ItemDetail, MaterialList } from "../types";
import { DetailsPanel } from "./DetailsPanel";

vi.mock("../lib/api", () => ({
  api: {
    getItem: vi.fn(),
    getItemMaterials: vi.fn()
  }
}));

const mockedApi = vi.mocked(api);

function node(auxCode = 21): AssemblyNode {
  return {
    id: `item-${auxCode}`,
    aux_code: auxCode,
    code: `7807/${String(auxCode).padStart(3, "0")}`,
    description: `Item ${auxCode}`,
    drawing_number: null,
    drawing_revision: null,
    position: null,
    quantity: 1,
    parent_id: null,
    child_ids: [],
    has_children: false,
    predecessor_ids: [],
    path_ids: [`item-${auxCode}`],
    path_labels: [`7807/${String(auxCode).padStart(3, "0")}`],
    state: "manufacturing",
    state_reason_code: "productive_operation_running",
    state_reason: "Em produção",
    operations_total: 1,
    operations_started: 1,
    operations_completed: 0,
    source_status: "EM PRODUÇÃO",
    current_operation: null,
    entry_date: null,
    due_date: null,
    final_date: null,
    has_drawing: false,
    thumbnail_url: null,
    detail_url: ""
  };
}

function detail(selectedNode = node()): ItemDetail {
  return {
    node: selectedNode,
    parent: null,
    path: [selectedNode],
    operations: [],
    categories: { ph: 0, lm: 2, st: 0, pp: 0 },
    predecessors: [],
    drawings: [],
    documents: [],
    observations_count: 0,
    information_origin: "GRV",
    document_path: null
  };
}

function materialList(auxCode = 21, materials = true): MaterialList {
  const itemCode = `7807/${String(auxCode).padStart(3, "0")}`;
  return {
    order_number: "7807",
    item_aux_code: auxCode,
    item_code: itemCode,
    material_used: materials,
    materials: materials
      ? [
          {
            id: `used-${auxCode}`,
            code: `MAT-${auxCode}-A`,
            description: `Material consumido do item ${auxCode}`,
            required_quantity: 10,
            consumed_quantity: 10,
            remaining_quantity: 0,
            available_quantity: 25,
            unit: "KG",
            used: true,
            consumption_status: "consumed",
            item_code: itemCode
          },
          {
            id: `unused-${auxCode}`,
            code: `MAT-${auxCode}-B`,
            description: `Material não consumido do item ${auxCode}`,
            required_quantity: 4,
            consumed_quantity: 0,
            remaining_quantity: 4,
            available_quantity: null,
            unit: "UN",
            used: false,
            consumption_status: "not_used",
            item_code: itemCode
          }
        ]
      : [],
    source: { system: "GRV", calculated_at: "2026-08-14T12:00:00Z", rule_version: "1.0" }
  };
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

function createQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

beforeEach(() => {
  mockedApi.getItem.mockResolvedValue(detail());
  mockedApi.getItemMaterials.mockResolvedValue(materialList());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DetailsPanel materials", () => {
  it("shows the not-started status and exposes its reason without cluttering the card", async () => {
    const selectedNode = node();
    selectedNode.state = "not_started";
    selectedNode.state_reason_code = "no_productive_process";
    selectedNode.state_reason = "Sem processo produtivo cadastrado no GRV";
    selectedNode.operations_total = 0;
    selectedNode.operations_started = 0;
    selectedNode.operations_completed = 0;
    mockedApi.getItem.mockResolvedValue(detail(selectedNode));

    render(<DetailsPanel orderNumber="7807" selectedNode={selectedNode} onLocate={vi.fn()} />, {
      wrapper: wrapper(createQueryClient())
    });

    expect(await screen.findByText("Não iniciado")).toHaveAttribute(
      "title",
      "Sem processo produtivo cadastrado no GRV"
    );
  });

  it("keeps the thumbnail URL stable and versions it with the selected document revision", async () => {
    const selectedNode = node();
    selectedNode.has_drawing = true;
    selectedNode.thumbnail_url = "/api/v1/orders/7807/items/21/thumbnail?v=test";
    const itemDetail = detail(selectedNode);
    itemDetail.drawings = [
      {
        id: 7,
        kind: "drawing",
        filename: "peca.pdf",
        description: null,
        size_bytes: 2048,
        content_revision: "3361",
        open_url: "/api/v1/orders/7807/items/21/drawings/7",
        is_primary: true
      }
    ];
    mockedApi.getItem.mockResolvedValue(itemDetail);

    render(<DetailsPanel orderNumber="7807" selectedNode={selectedNode} onLocate={vi.fn()} />, {
      wrapper: wrapper(createQueryClient())
    });

    expect(await screen.findByRole("img", { name: "Vista de Item 21" })).toHaveAttribute(
      "src",
      "/api/v1/orders/7807/items/21/thumbnail?v=test&document=drawing%3A7%3A3361%3A2048%3Apeca.pdf"
    );
  });

  it("replaces a broken thumbnail with the neutral preview state", async () => {
    const selectedNode = node();
    selectedNode.has_drawing = true;
    selectedNode.thumbnail_url = "/api/v1/orders/7807/items/21/thumbnail?v=test";
    mockedApi.getItem.mockResolvedValue(detail(selectedNode));

    render(<DetailsPanel orderNumber="7807" selectedNode={selectedNode} onLocate={vi.fn()} />, {
      wrapper: wrapper(createQueryClient())
    });

    const image = await screen.findByRole("img", { name: "Vista de Item 21" });
    fireEvent.error(image);

    expect(screen.getByRole("img", { name: "Pré-visualização indisponível" })).toBeVisible();
    expect(screen.queryByRole("img", { name: "Vista de Item 21" })).not.toBeInTheDocument();
  });

  it("opens the document classified as primary instead of the first listed file", async () => {
    const selectedNode = node();
    const itemDetail = detail(selectedNode);
    itemDetail.drawings = [
      {
        id: 1,
        kind: "drawing",
        filename: "arquivo-generico.pdf",
        description: null,
        size_bytes: 100,
        open_url: "/api/v1/orders/7807/items/21/drawings/1"
      },
      {
        id: 2,
        kind: "drawing",
        filename: "item-correspondente.pdf",
        description: null,
        size_bytes: 200,
        open_url: "/api/v1/orders/7807/items/21/drawings/2",
        is_primary: true
      }
    ];
    mockedApi.getItem.mockResolvedValue(itemDetail);
    const open = vi.spyOn(window, "open").mockImplementation(() => null);

    render(<DetailsPanel orderNumber="7807" selectedNode={selectedNode} onLocate={vi.fn()} />, {
      wrapper: wrapper(createQueryClient())
    });

    fireEvent.click(await screen.findByRole("button", { name: "Abrir desenho" }));
    expect(open).toHaveBeenCalledWith(
      "/api/v1/orders/7807/items/21/drawings/2",
      "_blank",
      "noopener,noreferrer"
    );
    open.mockRestore();
  });

  it("uses an attachment as the drawing action fallback", async () => {
    const selectedNode = node();
    const itemDetail = detail(selectedNode);
    itemDetail.documents = [
      {
        id: 1,
        kind: "attachment",
        filename: "desenho-cms.pdf",
        description: "Desenho anexado na estrutura de origem",
        size_bytes: 220768,
        open_url: "/api/v1/orders/7807/items/21/attachments/1?origin=true"
      }
    ];
    mockedApi.getItem.mockResolvedValue(itemDetail);
    const open = vi.spyOn(window, "open").mockImplementation(() => null);

    render(<DetailsPanel orderNumber="7807" selectedNode={selectedNode} onLocate={vi.fn()} />, {
      wrapper: wrapper(createQueryClient())
    });

    const button = await screen.findByRole("button", { name: "Abrir desenho" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(open).toHaveBeenCalledWith(
      "/api/v1/orders/7807/items/21/attachments/1?origin=true",
      "_blank",
      "noopener,noreferrer"
    );
    open.mockRestore();
  });

  it("opens the tab and shows consumed and unused materials", async () => {
    render(<DetailsPanel orderNumber="7807" selectedNode={node()} onLocate={vi.fn()} />, {
      wrapper: wrapper(createQueryClient())
    });

    expect(screen.queryByRole("tab", { name: "Operações" })).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Material utilizado")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Lista de Materiais" }));

    expect(await screen.findByText("Material consumido do item 21")).toBeVisible();
    expect(document.querySelector(".consumption-badge.consumed")).toBeVisible();
    expect(screen.getByText("Não utilizado", { exact: true })).toBeVisible();
  });

  it("shows loading while the material request is pending", async () => {
    mockedApi.getItemMaterials.mockReturnValue(new Promise(() => undefined));
    render(<DetailsPanel orderNumber="7807" selectedNode={node()} onLocate={vi.fn()} />, {
      wrapper: wrapper(createQueryClient())
    });

    fireEvent.click(screen.getByRole("tab", { name: "Lista de Materiais" }));
    expect(screen.getByLabelText("Carregando detalhes")).toBeVisible();
  });

  it("shows the empty state", async () => {
    mockedApi.getItemMaterials.mockResolvedValue(materialList(21, false));
    render(<DetailsPanel orderNumber="7807" selectedNode={node()} onLocate={vi.fn()} />, {
      wrapper: wrapper(createQueryClient())
    });

    fireEvent.click(screen.getByRole("tab", { name: "Lista de Materiais" }));
    expect(await screen.findByText("Nenhum material encontrado para esta OS/Sub-OS.")).toBeVisible();
  });

  it("keeps the panel available when the material request fails", async () => {
    mockedApi.getItemMaterials.mockRejectedValue(new Error("GRV unavailable"));
    render(<DetailsPanel orderNumber="7807" selectedNode={node()} onLocate={vi.fn()} />, {
      wrapper: wrapper(createQueryClient())
    });

    fireEvent.click(screen.getByRole("tab", { name: "Lista de Materiais" }));
    expect(await screen.findByText("Não foi possível carregar a lista de materiais.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeEnabled();
  });

  it("updates the list when another suborder is selected", async () => {
    mockedApi.getItem.mockImplementation(async (_order, auxCode) => detail(node(auxCode)));
    mockedApi.getItemMaterials.mockImplementation(async (_order, auxCode) => materialList(auxCode));
    const queryClient = createQueryClient();
    const view = render(
      <DetailsPanel orderNumber="7807" selectedNode={node(21)} onLocate={vi.fn()} />,
      { wrapper: wrapper(queryClient) }
    );
    fireEvent.click(screen.getByRole("tab", { name: "Lista de Materiais" }));
    expect(await screen.findByText("Material consumido do item 21")).toBeVisible();

    view.rerender(<DetailsPanel orderNumber="7807" selectedNode={node(22)} onLocate={vi.fn()} />);

    expect(await screen.findByText("Material consumido do item 22")).toBeVisible();
    await waitFor(() => expect(mockedApi.getItemMaterials).toHaveBeenCalledWith("7807", 22, expect.anything()));
  });
});
