import {
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  Filter,
  Search,
  X
} from "lucide-react";
import { useMemo, useState } from "react";

import { formatQuantity, stateLabels } from "../lib/format";
import { indexNodes } from "../lib/tree";
import type { AssemblyNode, ProductionState } from "../types";
import { StatusBadge } from "./StatusBadge";

interface TreePanelProps {
  nodes: AssemblyNode[];
  roots: string[];
  selectedId: string | null;
  expanded: ReadonlySet<string>;
  onToggle: (nodeId: string) => void;
  onSelect: (node: AssemblyNode) => void;
  onCollapseAll: () => void;
}

const states = Object.keys(stateLabels) as ProductionState[];

export function TreePanel({
  nodes,
  roots,
  selectedId,
  expanded,
  onToggle,
  onSelect,
  onCollapseAll
}: TreePanelProps) {
  const [query, setQuery] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [activeStates, setActiveStates] = useState<Set<ProductionState>>(new Set());
  const byId = useMemo(() => indexNodes(nodes), [nodes]);

  const visibleByFilter = useMemo(() => {
    if (!query.trim() && activeStates.size === 0) return null;
    const normalized = query.trim().toLocaleLowerCase("pt-BR");
    const matches = new Set<string>();
    for (const node of nodes) {
      const text = [
        node.code,
        node.description,
        node.drawing_number,
        node.position
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase("pt-BR");
      const textMatches = !normalized || text.includes(normalized);
      const stateMatches = activeStates.size === 0 || activeStates.has(node.state);
      if (textMatches && stateMatches) {
        node.path_ids.forEach((id) => matches.add(id));
      }
    }
    return matches;
  }, [activeStates, nodes, query]);

  const visibleRows = useMemo(() => {
    const rows: { node: AssemblyNode; depth: number; isExpanded: boolean }[] = [];
    const stack = roots.slice().reverse().map((id) => ({ id, depth: 0 }));
    const seen = new Set<string>();
    while (stack.length) {
      const { id, depth } = stack.pop()!;
      if (seen.has(id)) continue;
      seen.add(id);
      const node = byId.get(id);
      if (!node || (visibleByFilter && !visibleByFilter.has(id))) continue;
      const isExpanded = expanded.has(id) || Boolean(visibleByFilter);
      rows.push({ node, depth, isExpanded });
      if (isExpanded) {
        for (let index = node.child_ids.length - 1; index >= 0; index--) {
          stack.push({ id: node.child_ids[index], depth: depth + 1 });
        }
      }
    }
    return rows;
  }, [byId, expanded, roots, visibleByFilter]);

  return (
    <aside id="tree-panel" className="tree-panel panel">
      <div className="panel-title">
        <h2>Estrutura da OS</h2>
        <div>
          <button
            className="icon-button"
            type="button"
            onClick={() => setFilterOpen((value) => !value)}
            aria-label="Filtrar estrutura"
          >
            <Filter size={18} />
          </button>
          <button
            className="icon-button"
            type="button"
            onClick={onCollapseAll}
            aria-label="Recolher níveis"
          >
            <ChevronsDownUp size={18} />
          </button>
        </div>
      </div>
      <div className="tree-search">
        <Search size={17} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Pesquisar na estrutura"
        />
        {query && (
          <button type="button" onClick={() => setQuery("")} aria-label="Limpar pesquisa">
            <X size={15} />
          </button>
        )}
      </div>
      <div className="tree-content">
        {filterOpen && (
          <div className="status-filters">
            {states.map((state) => (
              <label key={state}>
                <input
                  type="checkbox"
                  checked={activeStates.has(state)}
                  onChange={() => {
                    setActiveStates((current) => {
                      const next = new Set(current);
                      if (next.has(state)) next.delete(state);
                      else next.add(state);
                      return next;
                    });
                  }}
                />
                <StatusBadge state={state} />
              </label>
            ))}
          </div>
        )}

        <div className="tree-scroll">
          {visibleRows.map(({ node, depth, isExpanded }) => (
            <div
              key={node.id}
              className={`tree-row${selectedId === node.id ? " selected" : ""}`}
              style={{ paddingLeft: 10 + Math.min(depth, 8) * 16 }}
              aria-level={depth + 1}
            >
              <button
                className="tree-expand"
                type="button"
                onClick={() => onToggle(node.id)}
                disabled={!node.has_children}
                aria-label={isExpanded ? "Recolher item" : "Expandir item"}
              >
                {node.has_children ? (
                  isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />
                ) : <span />}
              </button>
              <button className="tree-item" type="button" onClick={() => onSelect(node)}>
                <StatusBadge state={node.state} reason={node.state_reason} compact />
                <span className="tree-copy">
                  <strong>{node.code}</strong>
                  <small title={node.description}>{node.description}</small>
                </span>
                <span className="quantity-pill">{formatQuantity(node.quantity)}</span>
              </button>
            </div>
          ))}
          {nodes.length === 0 && <div className="empty-state">Nenhum item carregado.</div>}
        </div>
      </div>

      <div className="status-legend">
        <h3>Legenda de Status</h3>
        <div>
          {states.map((state) => (
            <StatusBadge key={state} state={state} />
          ))}
        </div>
      </div>
    </aside>
  );
}
