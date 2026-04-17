from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import AgendamentoSolicitacao
from conferencia_app.services.agendamento_service import estimar_rota_agendamento

app = create_app()
ctx = app.app_context()
ctx.push()

solicitacoes = AgendamentoSolicitacao.query.all()
for r in solicitacoes:
    if r.km_estimado is None:
        rota = estimar_rota_agendamento({'logradouro':r.logradouro,'numero':r.numero,'bairro':r.bairro,'cidade':r.cidade,'uf':r.uf,'cep':r.cep,'latitude':r.destino_latitude,'longitude':r.destino_longitude})
        if rota.get('km_estimado') is not None:
            r.origem_latitude = rota.get('origem_latitude')
            r.origem_longitude = rota.get('origem_longitude')
            r.destino_latitude = rota.get('destino_latitude')
            r.destino_longitude = rota.get('destino_longitude')
            r.km_estimado = rota.get('km_estimado')
            r.km_estimado_retorno = rota.get('km_estimado_retorno')
            db.session.commit()
            print('Fixed ID', r.id, "KM:", r.km_estimado)
        else:
            print('Failed ID', r.id)
