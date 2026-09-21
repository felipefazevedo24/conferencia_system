import { describe, expect, it } from "vitest";

import type { OrderDependencyMap } from "../types";
import { connectedOrderNumbers } from "./OrderDependencyMap";

const dependencyMap: OrderDependencyMap = {
  selected_order_number: "7213",
  budget_number: 6121,
  nodes: ["7212", "7213", "7236", "7237"].map((number) => ({
    number,
    title: `OS ${number}`,
    source_status: "CONCLUÍDO",
    due_date: null,
    drawing_number: null,
    is_budget_order: number === "7212" || number === "7213"
  })),
  edges: [
    { dependent_order_number: "7213", prerequisite_order_number: "7236" },
    { dependent_order_number: "7213", prerequisite_order_number: "7237" }
  ],
  source: {
    system: "GRV",
    calculated_at: "2026-08-14T12:00:00Z",
    rule_version: "1.0"
  }
};

describe("connectedOrderNumbers", () => {
  it("keeps the selected dependency chain separate from unrelated budget orders", () => {
    expect([...connectedOrderNumbers(dependencyMap, "7213")].sort()).toEqual([
      "7213",
      "7236",
      "7237"
    ]);
  });

  it("includes the dependent parent when a prerequisite order is selected", () => {
    expect([...connectedOrderNumbers(dependencyMap, "7236")].sort()).toEqual([
      "7213",
      "7236",
      "7237"
    ]);
  });

  it("shows an isolated budget order by itself", () => {
    expect([...connectedOrderNumbers(dependencyMap, "7212")]).toEqual(["7212"]);
  });
});
