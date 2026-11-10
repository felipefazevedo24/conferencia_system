"""Diagnóstico (SÓ LEITURA) do rateio de combustível com os dados reais.

O rateio divide o custo pelos km das viagens concluídas da semana. Este
script mostra, para as últimas semanas, se esse km existe e de onde vem
(odômetro x GPS), e quanto cai em LOGÍSTICA por falta de departamento.
Também lista veículos sem placa, que travam a importação das NFC-e.

Não grava nada no banco.

Uso no PythonAnywhere:

    cd ~/conferencia_system
    source ~/.virtualenvs/conferencia-env/bin/activate
    DATABASE_URL='<copiado do WSGI>' SKIP_APP_BOOTSTRAP=1 python scripts/diagnostico_rateio_combustivel.py [semanas]
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SKIP_APP_BOOTSTRAP", "1")

from sqlalchemy import func  # noqa: E402

from conferencia_app import create_app  # noqa: E402
from conferencia_app.extensions import db  # noqa: E402
from conferencia_app.models import (  # noqa: E402
    AgendamentoSolicitacao,
    AgendamentoVeiculo,
    Viagem,
    ViagemParada,
)
from conferencia_app.services.frota_combustivel_service import (  # noqa: E402
    DEPARTAMENTO_PADRAO,
    _departamento,
)


def _pct(parte: float, total: float) -> str:
    return f"{100 * parte / total:5.1f}%" if total else "    -"


def main() -> int:
    semanas = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 70)
        print(f"BANCO ALVO: {url.drivername} | host={url.host or '(local)'} | db={url.database}")
        print("=" * 70)

        hoje = datetime.combine(datetime.now().date(), time.min)
        segunda = hoje - timedelta(days=hoje.weekday())
        inicio_total = segunda - timedelta(weeks=semanas)
        saida = func.coalesce(Viagem.saida_real, Viagem.saida_prevista)

        geral_dep: dict[str, float] = defaultdict(float)
        geral = defaultdict(float)
        print(f"\nÚltimas {semanas} semanas fechadas (segunda a domingo)\n")
        print(f"{'semana':<12}{'viag.':>6}{'sem km':>8}{'km':>9}{'odôm.':>8}{'GPS':>8}"
              f"{'LOG s/ solic':>14}{'LOG s/ depto':>14}  departamentos")
        for n in range(semanas, 0, -1):
            ini = segunda - timedelta(weeks=n)
            fim = ini + timedelta(days=7)
            viagens = Viagem.query.filter(Viagem.status == "Concluida", saida >= ini, saida < fim).all()
            c = defaultdict(float)
            dep: dict[str, float] = defaultdict(float)
            for v in viagens:
                c["viagens"] += 1
                km = float(v.km_percorrido or 0)
                if km <= 0:
                    c["sem_km"] += 1
                    continue
                c["km"] += km
                # Mesma regra do concluir(): odômetro quando os dois km existem, senão GPS.
                odometro = v.km_inicial is not None and v.km_final is not None and v.km_final >= v.km_inicial
                c["km_odometro" if odometro else "km_gps"] += km
                paradas = ViagemParada.query.filter(
                    ViagemParada.viagem_id == v.id,
                    ViagemParada.solicitacao_id.isnot(None),
                    ViagemParada.status != "Cancelada",
                ).all()
                if not paradas:
                    c["log_sem_solicitacao"] += km
                    dep[DEPARTAMENTO_PADRAO] += km
                    continue
                sols = {
                    s.id: s for s in AgendamentoSolicitacao.query.filter(
                        AgendamentoSolicitacao.id.in_({p.solicitacao_id for p in paradas})
                    ).all()
                }
                parte = km / len(paradas)
                for p in paradas:
                    sol = sols.get(p.solicitacao_id)
                    nome = _departamento(sol)
                    if not str(getattr(sol, "departamento_solicitante", "") or "").strip():
                        c["log_sem_departamento"] += parte
                    dep[nome] += parte
            for k, val in c.items():
                geral[k] += val
            for k, val in dep.items():
                geral_dep[k] += val
            deps = ", ".join(f"{k} {_pct(val, c['km']).strip()}" for k, val in sorted(dep.items(), key=lambda x: -x[1]))
            print(f"{ini:%d/%m/%Y}  {int(c['viagens']):>6}{int(c['sem_km']):>8}{c['km']:>9.0f}"
                  f"{_pct(c['km_odometro'], c['km']):>8}{_pct(c['km_gps'], c['km']):>8}"
                  f"{_pct(c['log_sem_solicitacao'], c['km']):>14}{_pct(c['log_sem_departamento'], c['km']):>14}  {deps}")

        total = geral["km"]
        print("\nTOTAL DO PERÍODO")
        print(f"  viagens concluídas: {int(geral['viagens'])}  |  sem km (ficam fora do rateio): {int(geral['sem_km'])}")
        print(f"  km: {total:.0f}  |  pelo odômetro: {_pct(geral['km_odometro'], total).strip()}"
              f"  |  pelo GPS: {_pct(geral['km_gps'], total).strip()}")
        print(f"  {DEPARTAMENTO_PADRAO} por viagem sem solicitação: {_pct(geral['log_sem_solicitacao'], total).strip()}")
        print(f"  {DEPARTAMENTO_PADRAO} por solicitação sem departamento: {_pct(geral['log_sem_departamento'], total).strip()}")
        for k, val in sorted(geral_dep.items(), key=lambda x: -x[1]):
            print(f"    {k:<28}{val:>9.0f} km  {_pct(val, total)}")

        print("\nVEÍCULOS ATIVOS (a NFC-e acha o veículo pela placa)")
        for v in AgendamentoVeiculo.query.filter_by(ativo=True).order_by(AgendamentoVeiculo.nome_exibicao).all():
            print(f"  {v.nome_exibicao:<30} placa: {v.placa or '*** SEM PLACA ***'}")

        db.session.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
