export type ProductionState =
  | "not_started"
  | "blocked"
  | "completed"
  | "assembling"
  | "available"
  | "manufacturing";

export type StatusReasonCode =
  | "invalid_structure_cycle"
  | "open_non_conformity"
  | "process_locked"
  | "assembly_completed"
  | "source_completed"
  | "assembly_operation_running"
  | "productive_operation_running"
  | "ready_for_assembly"
  | "waiting_components"
  | "process_not_started"
  | "no_productive_process"
  | "no_execution_evidence";

export interface OrderSearchResult {
  number: string;
  title: string;
  source_status: string | null;
  due_date: string | null;
  drawing_number: string | null;
  matched_item_code: string | null;
  matched_item_description: string | null;
  matched_budget_number: number | null;
}

export interface ProgressSummary {
  percentage: number;
  finalized_operations: number;
  total_operations: number;
}

export interface AssemblyNode {
  id: string;
  aux_code: number;
  code: string;
  description: string;
  drawing_number: string | null;
  drawing_revision: string | null;
  position: string | null;
  quantity: number;
  parent_id: string | null;
  child_ids: string[];
  has_children: boolean;
  predecessor_ids: string[];
  path_ids: string[];
  path_labels: string[];
  state: ProductionState;
  state_reason_code: StatusReasonCode;
  state_reason: string;
  operations_total: number;
  operations_started: number;
  operations_completed: number;
  source_status: string | null;
  current_operation: string | null;
  entry_date: string | null;
  due_date: string | null;
  final_date: string | null;
  has_drawing: boolean;
  thumbnail_url: string | null;
  detail_url: string;
}

export interface AssemblyStructure {
  order: {
    number: string;
    title: string;
    source_status: string | null;
    due_date: string | null;
    drawing_number: string | null;
  };
  roots: string[];
  nodes: AssemblyNode[];
  progress: ProgressSummary;
  pending_count: number;
  current_stage: string;
  source: {
    system: string;
    calculated_at: string;
    rule_version: string;
  };
}

export interface OrderDependencyNode {
  number: string;
  title: string;
  source_status: string | null;
  due_date: string | null;
  drawing_number: string | null;
  is_budget_order: boolean;
}

export interface OrderDependencyEdge {
  dependent_order_number: string;
  prerequisite_order_number: string;
}

export interface OrderDependencyMap {
  selected_order_number: string;
  budget_number: number | null;
  nodes: OrderDependencyNode[];
  edges: OrderDependencyEdge[];
  source: {
    system: string;
    calculated_at: string;
    rule_version: string;
  };
}

export interface OperationDetail {
  code: string;
  name: string;
  sequence: number | null;
  finalized: boolean;
  locked: boolean;
  started_at: string | null;
  planned_start: string | null;
  planned_end: string | null;
  finished_at: string | null;
  machine: string | null;
  first_report_at: string | null;
  last_report_at: string | null;
}

export type LiveOperationState = "running" | "paused";

export interface ActivePointing {
  operator_name: string;
  started_at: string | null;
  machine: string | null;
  paused: boolean;
}

export interface OperationLiveStatus {
  code: string | null;
  sequence: number | null;
  state: LiveOperationState;
  pointings: ActivePointing[];
}

export interface ItemLiveStatus {
  item_aux_code: number;
  operations: OperationLiveStatus[];
  source: {
    system: string;
    calculated_at: string;
    rule_version: string;
  };
  refresh_after_seconds: number;
}

export type MaterialConsumptionStatus = "not_used" | "partial" | "consumed";

export interface MaterialDetail {
  id: string;
  code: string;
  description: string;
  required_quantity: number;
  consumed_quantity: number;
  remaining_quantity: number;
  available_quantity: number | null;
  unit: string | null;
  used: boolean;
  consumption_status: MaterialConsumptionStatus;
  item_code: string;
}

export interface MaterialList {
  order_number: string;
  item_aux_code: number;
  item_code: string;
  material_used: boolean;
  materials: MaterialDetail[];
  source: {
    system: string;
    calculated_at: string;
    rule_version: string;
  };
}

export interface Drawing {
  id: number;
  kind: "drawing" | "attachment";
  filename: string;
  description: string | null;
  size_bytes: number;
  open_url: string;
  content_revision?: string | null;
  is_primary?: boolean;
}

export interface ItemDetail {
  node: AssemblyNode;
  parent: AssemblyNode | null;
  path: AssemblyNode[];
  operations: OperationDetail[];
  categories: { ph: number; lm: number; st: number; pp: number };
  predecessors: AssemblyNode[];
  drawings: Drawing[];
  documents: Drawing[];
  observations_count: number;
  information_origin: string;
  document_path: string | null;
}

export interface Observation {
  id: string;
  order_number: string;
  item_aux_code: number;
  text: string;
  author: string;
  created_at: string;
  updated_at: string;
}

export interface SequenceStep {
  id: string;
  order_number: string;
  item_aux_code: number | null;
  position: number;
  title: string;
  instructions: string | null;
  source: "application" | "grv";
  created_by: string;
  created_at: string;
}

export type PlanningReadinessStatus =
  | "done"
  | "running"
  | "ready"
  | "waiting_predecessor"
  | "waiting_component"
  | "waiting_material"
  | "blocked_quality"
  | "blocked_process"
  | "planning_incomplete";

export type MaterialAvailabilityStatus =
  | "available"
  | "partially_available"
  | "shortage"
  | "future_supply"
  | "no_confirmed_supply"
  | "unknown"
  | "planning_incomplete";

export interface PlanningReason {
  code: string;
  message: string;
  related_codes: string[];
}

export interface PlanningOperationReadiness {
  item_aux_code: number;
  operation_code: string;
  name: string;
  sequence: number | null;
  service_type_code: number | null;
  status: PlanningReadinessStatus;
  reasons: PlanningReason[];
  productive_hours: number | null;
  unit_hours: number | null;
  setup_hours: number | null;
  technical_load_hours: number | null;
  realized_hours: number | null;
  assigned_resource_code: number | null;
  third_party: boolean;
  eligible_resource_codes: number[];
}

export interface PlanningMaterialAvailability {
  line_id: string;
  item_aux_code: number;
  material_code: string;
  description: string;
  unit: string | null;
  required_qty: number;
  consumed_qty: number;
  remaining_qty: number;
  stock_before: number | null;
  status: MaterialAvailabilityStatus;
  remaining_quantity: number;
  stock_allocated: number;
  stock_after: number | null;
  open_purchase_qty: number;
  future_supply_allocated: number;
  material_ready_date: string | null;
  shortage_qty: number;
  shortage_quantity: number;
  expected_at: string | null;
  reasons: MaterialDecisionReason[];
  pegging: SupplyPegging[];
}

export interface MaterialDecisionReason {
  code: string;
  message: string;
  quantity: number | null;
  reference: string | null;
  occurred_at: string | null;
}

export interface SupplyPegging {
  source_type: "stock" | "purchase_order";
  source_reference: string;
  allocated_quantity: number;
  expected_at: string | null;
}

export interface PlanningReadiness {
  order_number: string;
  operations: PlanningOperationReadiness[];
  materials: PlanningMaterialAvailability[];
  summary: {
    items: number;
    operations: number;
    materials: number;
    active_resources: number;
    shifts: number;
    open_purchase_lines: number;
    stock_allocated_lines: number;
    purchase_orders_used: number;
    items_blocked_by_material: number;
    operations_ready: number;
  };
  source: {
    system: string;
    calculated_at: string;
    rule_version: string;
  };
}
