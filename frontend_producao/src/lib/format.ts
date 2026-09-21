import type { ProductionState } from "../types";

export const stateLabels: Record<ProductionState, string> = {
  not_started: "Não iniciado",
  available: "Pronto para montagem",
  manufacturing: "Em fabricação",
  assembling: "Em montagem",
  blocked: "Bloqueado",
  completed: "Concluído"
};

export function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR").format(new Date(value));
}

export function formatQuantity(value: number): string {
  return new Intl.NumberFormat("pt-BR", {
    maximumFractionDigits: 3
  }).format(value);
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}
