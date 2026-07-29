from marshmallow import ValidationError
from flask import jsonify, render_template, request


def register_error_handlers(app):
    @app.errorhandler(ValidationError)
    def handle_validation_error(err):
        return jsonify({"error": "Payload inválido", "details": err.messages}), 400

    @app.errorhandler(400)
    def handle_bad_request(err):
        if request.path.startswith("/api") or request.path == "/validar":
            return jsonify({"error": "Requisição inválida"}), 400
        return render_template("acesso_negado.html", user=""), 400

    @app.errorhandler(401)
    def handle_unauthorized(err):
        if request.path.startswith("/api") or request.path == "/validar":
            return jsonify({"error": "Não autenticado"}), 401
        message = getattr(err, "description", "") or "Sua sessão expirou. Faça login novamente para continuar."
        return render_template("login.html", login_message=message, login_message_type="warning"), 401

    @app.errorhandler(403)
    def handle_forbidden(err):
        if request.path.startswith("/api") or request.path == "/validar":
            return jsonify({"error": "Acesso negado"}), 403
        return render_template("acesso_negado.html", user=""), 403

    @app.errorhandler(404)
    def handle_not_found(err):
        # Sem isso, uma rota /api inexistente devolvia a pagina HTML padrao do
        # Flask; o front-end tenta ler a resposta como JSON e quebra com um
        # erro generico de "JSON invalido" em vez do 404 real.
        if request.path.startswith("/api") or request.path == "/validar":
            return jsonify({"error": "Recurso nao encontrado"}), 404
        return render_template("acesso_negado.html", user=""), 404

    @app.errorhandler(500)
    def handle_internal_error(err):
        import traceback
        traceback.print_exc()
        if request.path.startswith("/api") or request.path == "/validar":
            return jsonify({"error": "Erro interno do servidor", "details": str(err)}), 500
        return render_template("acesso_negado.html", user=""), 500
