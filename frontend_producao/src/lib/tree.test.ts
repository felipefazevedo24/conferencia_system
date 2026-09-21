import { describe, expect, it } from "vitest";

import type { AssemblyNode } from "../types";
import { expandPath, initialExpanded, visibleNodeIds } from "./tree";

function node(
  id: string,
  parent: string | null,
  path: string[],
  children: string[]
): AssemblyNode {
  return {
    id,
    aux_code: Number(id.replace("item-", "")),
    code: id,
    description: id,
    drawing_number: null,
    drawing_revision: null,
    position: null,
    quantity: 1,
    parent_id: parent,
    child_ids: children,
    has_children: children.length > 0,
    predecessor_ids: [],
    path_ids: path,
    path_labels: path,
    state: "manufacturing",
    state_reason_code: "productive_operation_running",
    state_reason: "",
    operations_total: 1,
    operations_started: 1,
    operations_completed: 0,
    source_status: null,
    current_operation: null,
    entry_date: null,
    due_date: null,
    final_date: null,
    has_drawing: false,
    thumbnail_url: null,
    detail_url: ""
  };
}

const nodes = [
  node("item-1", null, ["item-1"], ["item-2"]),
  node("item-2", "item-1", ["item-1", "item-2"], ["item-3"]),
  node("item-3", "item-2", ["item-1", "item-2", "item-3"], [])
];

describe("tree visibility", () => {
  it("only renders descendants when every ancestor is expanded", () => {
    expect([...visibleNodeIds(nodes, new Set(["item-1"]))]).toEqual([
      "item-1",
      "item-2"
    ]);
  });

  it("expands all ancestors when locating a node", () => {
    const expanded = expandPath(new Set(), nodes[2]);
    expect(expanded).toEqual(new Set(["item-1", "item-2"]));
  });

  it("expands the first two structural levels initially", () => {
    expect(initialExpanded(nodes)).toEqual(new Set(["item-1", "item-2"]));
  });
});
