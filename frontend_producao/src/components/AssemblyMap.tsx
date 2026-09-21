import dagre from "@dagrejs/dagre";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps
} from "@xyflow/react";
import { Box } from "lucide-react";
import { memo, useEffect, useMemo } from "react";

import { formatQuantity } from "../lib/format";
import { visibleNodeIds } from "../lib/tree";
import type { AssemblyNode, OrderDependencyMap as DependencyMap } from "../types";
import { OrderDependencyMap } from "./OrderDependencyMap";
import { StatusBadge } from "./StatusBadge";

interface AssemblyMapProps {
  nodes: AssemblyNode[];
  selectedId: string | null;
  expanded: ReadonlySet<string>;
  onSelect: (node: AssemblyNode) => void;
  dependencyMap: DependencyMap | undefined;
  dependenciesLoading: boolean;
  dependenciesError: boolean;
  selectedOrder: string;
  onSelectOrder: (number: string) => void;
  view: "structure" | "dependencies";
  onViewChange: (view: "structure" | "dependencies") => void;
  showAllDependencies: boolean;
  onShowAllDependenciesChange: (showAll: boolean) => void;
}

type AssemblyCardData = {
  item: AssemblyNode;
  selected: boolean;
  onSelect: (node: AssemblyNode) => void;
};

type AssemblyFlowNode = Node<AssemblyCardData, "assembly">;

const AssemblyCard = memo(function AssemblyCard({
  data
}: NodeProps<AssemblyFlowNode>) {
  const { item, selected, onSelect } = data;
  return (
    <button
      type="button"
      className={`assembly-node${selected ? " selected" : ""}`}
      onClick={() => onSelect(item)}
      aria-label={`${item.code}: ${item.description}`}
    >
      <Handle type="target" position={Position.Top} />
      <div className="node-main">
        <div className="node-thumbnail">
          {item.thumbnail_url ? (
            <img
              src={item.thumbnail_url}
              alt=""
              loading="lazy"
              onError={(event) => {
                event.currentTarget.style.display = "none";
              }}
            />
          ) : (
            <Box size={29} />
          )}
        </div>
        <div>
          <strong>{item.code}</strong>
          <span>{item.description}</span>
        </div>
      </div>
      <div className="node-meta">
        <small>Qtde: {formatQuantity(item.quantity)}</small>
        <StatusBadge state={item.state} reason={item.state_reason} />
      </div>
      <Handle type="source" position={Position.Bottom} />
    </button>
  );
});

const nodeTypes = { assembly: AssemblyCard };

function FitCompleteStructure({ nodeIds }: { nodeIds: string }) {
  const { fitView } = useReactFlow();

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fitView({ padding: 0.2, maxZoom: 1.15, duration: 0 });
    }, 350);
    return () => window.clearTimeout(timer);
  }, [fitView, nodeIds]);

  return null;
}

export function AssemblyMap({
  nodes,
  selectedId,
  expanded,
  onSelect,
  dependencyMap,
  dependenciesLoading,
  dependenciesError,
  selectedOrder,
  onSelectOrder,
  view,
  onViewChange,
  showAllDependencies,
  onShowAllDependenciesChange
}: AssemblyMapProps) {
  const layout = useMemo(
    () => layoutGraph(nodes, expanded, onSelect),
    [expanded, nodes, onSelect]
  );
  const flowNodes = useMemo(
    () => layout.flowNodes.map((node) => ({
      ...node,
      data: { ...node.data, selected: node.id === selectedId }
    })),
    [layout.flowNodes, selectedId]
  );

  return (
    <section className="map-panel panel">
      <div className="panel-title">
        <h2>Mapa Visual do Conjunto</h2>
        <div>
          <div className="map-view-switch" aria-label="Visualização do mapa">
            <button
              type="button"
              className={view === "structure" ? "active" : ""}
              onClick={() => onViewChange("structure")}
            >
              Estrutura da OS
            </button>
            <button
              type="button"
              className={view === "dependencies" ? "active" : ""}
              onClick={() => onViewChange("dependencies")}
            >
              Dependências
            </button>
          </div>
          <span className="source-label">Fonte: GRV</span>
        </div>
      </div>
      {view === "structure" ? (
        <div className="flow-canvas">
          <ReactFlow
            key={selectedOrder}
            nodes={flowNodes}
            edges={layout.edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.2, maxZoom: 1.15 }}
            minZoom={0.01}
            maxZoom={1.8}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable
            onlyRenderVisibleElements
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#dce4ed" />
            <Controls showInteractive={false} position="top-right" />
            <FitCompleteStructure nodeIds={layout.flowNodes.map((node) => node.id).join("|")} />
          </ReactFlow>
        </div>
      ) : (
        <OrderDependencyMap
          data={dependencyMap}
          selectedOrder={selectedOrder}
          loading={dependenciesLoading}
          error={dependenciesError}
          onSelectOrder={onSelectOrder}
          showAll={showAllDependencies}
          onShowAllChange={onShowAllDependenciesChange}
        />
      )}
    </section>
  );
}

function layoutGraph(
  items: AssemblyNode[],
  expanded: ReadonlySet<string>,
  onSelect: (node: AssemblyNode) => void
): { flowNodes: AssemblyFlowNode[]; edges: Edge[] } {
  const visible = visibleNodeIds(items, expanded);
  const visibleItems = items.filter((item) => visible.has(item.id));
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: "TB",
    ranksep: 58,
    nodesep: 26,
    marginx: 24,
    marginy: 24
  });
  for (const item of visibleItems) graph.setNode(item.id, { width: 218, height: 138 });
  const edges: Edge[] = [];
  for (const item of visibleItems) {
    if (item.parent_id && visible.has(item.parent_id)) {
      graph.setEdge(item.parent_id, item.id);
      edges.push({
        id: `${item.parent_id}-${item.id}`,
        source: item.parent_id,
        target: item.id,
        type: "smoothstep",
        style: { stroke: "#36526e", strokeWidth: 1.5 }
      });
    }
  }
  dagre.layout(graph);
  const flowNodes: AssemblyFlowNode[] = visibleItems.map((item) => {
    const position = graph.node(item.id);
    return {
      id: item.id,
      type: "assembly",
      position: {
        x: position.x - 109,
        y: position.y - 69
      },
      data: {
        item,
        selected: false,
        onSelect
      }
    };
  });
  return { flowNodes, edges };
}
