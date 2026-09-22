export function LoadingState() {
  return (
    <div className="loading-state" role="status">
      <div className="spinner" />
      <strong>Carregando estrutura da OS...</strong>
      <span>Consultando dados oficiais no GRV</span>
    </div>
  );
}

