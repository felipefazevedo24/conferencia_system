from datetime import date
from decimal import Decimal

from flask import Blueprint, current_app, jsonify, render_template, request, session
from psycopg2 import OperationalError

from ..auth import permission_required
from ..compras.services import compras_service


compras_bp = Blueprint("compras", __name__)
PERM = "PAGE_COMPRAS_CPS"
DATA_CAMPOS = {"dt_entrada", "dt_aprovacao", "dt_prevista"}


def _arg_int(name: str, default: int | None = None, min_value: int | None = None, max_value: int | None = None):
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _arg_bool(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "sim", "yes", "on"}


def _arg_date(name: str):
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _data_campo():
    raw = request.args.get("data_campo")
    return raw if raw in DATA_CAMPOS else None


def _json_safe(value):
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _json_response(factory):
    try:
        return jsonify(_json_safe(factory()))
    except OperationalError as exc:
        current_app.logger.exception("Falha de banco no modulo Compras")
        return jsonify(
            {
                "detail": "Banco de dados indisponivel no momento. Tente novamente em instantes.",
                "error_type": type(exc).__name__,
            }
        ), 503


def _common_filters() -> dict:
    return {
        "cod_empresa": _arg_int("empresa"),
        "classificacao": request.args.get("classificacao") or None,
        "n_os": request.args.get("n_os") or None,
        "data_campo": _data_campo(),
        "data_de": _arg_date("data_de"),
        "data_ate": _arg_date("data_ate"),
    }


@compras_bp.route("/compras")
@permission_required(PERM)
def compras_page():
    return render_template(
        "compras.html",
        user=session.get("username", "Compras"),
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


@compras_bp.route("/compras/ui")
@permission_required(PERM)
def compras_ui():
    return current_app.send_static_file("compras/index.html")


@compras_bp.route("/api/compras/health")
@permission_required(PERM)
def compras_health():
    try:
        db_ok = compras_service.healthcheck_db()
    except Exception:
        current_app.logger.warning("Healthcheck do modulo Compras sem conexao com banco")
        db_ok = False
    return jsonify({"app": "ok", "db": db_ok})


@compras_bp.route("/api/compras/os/<path:n_os>/materiais")
@permission_required(PERM)
def materiais_por_os(n_os):
    return _json_response(
        lambda: compras_service.listar_materiais_por_os(
            n_os=n_os,
            cod_empresa=_arg_int("empresa"),
            classificacao=request.args.get("classificacao") or None,
            data_campo=_data_campo(),
            data_de=_arg_date("data_de"),
            data_ate=_arg_date("data_ate"),
        )
    )


@compras_bp.route("/api/compras/materiais")
@permission_required(PERM)
def materiais():
    filters = _common_filters()
    return _json_response(
        lambda: compras_service.listar_materiais_por_os(
            limite=_arg_int("limite", 5000, 1, 20000),
            **filters,
        )
    )


@compras_bp.route("/api/compras/gap")
@permission_required(PERM)
def gap():
    filters = _common_filters()
    return _json_response(
        lambda: compras_service.indicadores_gap(
            metodo=_arg_int("metodo"),
            **filters,
        )
    )


@compras_bp.route("/api/compras/classificacoes")
@permission_required(PERM)
def classificacoes():
    return _json_response(lambda: compras_service.listar_classificacoes(cod_empresa=_arg_int("empresa")))


@compras_bp.route("/api/compras/os/painel")
@permission_required(PERM)
def painel_os():
    filters = _common_filters()
    return _json_response(
        lambda: compras_service.painel_os(
            somente_abertas=_arg_bool("somente_abertas", True),
            limite=_arg_int("limite", 2000, 1, 10000),
            **filters,
        )
    )


@compras_bp.route("/api/compras/dashboard/performance")
@permission_required(PERM)
def dashboard_performance():
    filters = _common_filters()
    return _json_response(
        lambda: compras_service.dashboard_performance(
            somente_abertas=_arg_bool("somente_abertas", True),
            **filters,
        )
    )


@compras_bp.route("/api/compras/historico/ordens-compra")
@permission_required(PERM)
def historico_ordens_compra():
    filters = _common_filters()
    return _json_response(
        lambda: compras_service.historico_ordens_compra(
            situacao=request.args.get("situacao") or "todas",
            limite=_arg_int("limite", 5000, 1, 20000),
            **filters,
        )
    )


@compras_bp.route("/api/compras/visibility")
@permission_required(PERM)
def visibility():
    filters = _common_filters()
    return _json_response(
        lambda: compras_service.visibility_compras(
            somente_sc_sem_oc=_arg_bool("somente_sc_sem_oc", False),
            limite=_arg_int("limite", 8000, 1, 30000),
            **filters,
        )
    )


@compras_bp.route("/api/compras/spend/baseline")
@permission_required(PERM)
def spend_baseline():
    return _json_response(
        lambda: compras_service.spend_baseline(
            cod_empresa=_arg_int("empresa"),
            tipo_item=request.args.get("tipo_item") or None,
            classificacao_item=request.args.get("classificacao_item") or None,
            destinatario_cnpj=request.args.get("destinatario_cnpj") or None,
            data_de=_arg_date("data_de"),
            data_ate=_arg_date("data_ate"),
            limite=_arg_int("limite", 10000, 1, 30000),
        )
    )


@compras_bp.route("/api/compras/spend/baseline/composicao")
@permission_required(PERM)
def spend_baseline_composicao():
    return _json_response(
        lambda: compras_service.spend_baseline_composicao(
            cod_empresa=_arg_int("empresa"),
            cnpj=request.args.get("cnpj") or None,
            fornecedor=request.args.get("fornecedor") or None,
            tipo_item=request.args.get("tipo_item") or None,
            data_de=_arg_date("data_de"),
            data_ate=_arg_date("data_ate"),
            limite=_arg_int("limite", 30000, 1, 50000),
        )
    )
