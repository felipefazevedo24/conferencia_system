import type { AssemblyNode } from "../types";

export function indexNodes(nodes: AssemblyNode[]): Map<string, AssemblyNode> {
  return new Map(nodes.map((node) => [node.id, node]));
}

export function visibleNodeIds(
  nodes: AssemblyNode[],
  expanded: ReadonlySet<string>
): Set<string> {
  const byId = indexNodes(nodes);
  const visible = new Set<string>();
  const stack = nodes.filter((node) => !node.parent_id || !byId.has(node.parent_id));
  while (stack.length) {
    const node = stack.pop()!;
    if (visible.has(node.id)) continue;
    visible.add(node.id);
    if (expanded.has(node.id)) {
      for (const childId of node.child_ids) {
        const child = byId.get(childId);
        if (child) stack.push(child);
      }
    }
  }
  return visible;
}

export function initialExpanded(nodes: AssemblyNode[]): Set<string> {
  return new Set(
    nodes
      .filter((node) => node.has_children && node.path_ids.length <= 2)
      .map((node) => node.id)
  );
}

export function expandPath(
  current: ReadonlySet<string>,
  node: AssemblyNode
): Set<string> {
  return new Set([...current, ...node.path_ids.slice(0, -1)]);
}
