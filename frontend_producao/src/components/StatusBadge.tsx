import type { ProductionState } from "../types";
import { stateLabels } from "../lib/format";

interface StatusBadgeProps {
  state: ProductionState;
  compact?: boolean;
  reason?: string;
}

export function StatusBadge({ state, compact = false, reason }: StatusBadgeProps) {
  return (
    <span
      className={`status-badge status-${state}${compact ? " compact" : ""}`}
      title={reason}
    >
      <span className="status-dot" aria-hidden="true" />
      {!compact && stateLabels[state]}
    </span>
  );
}
