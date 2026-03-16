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
        return render_template("login.html"), 401

    @app.errorhandler(403)
    def handle_forbidden(err):
        if request.path.startswith("/api") or request.path == "/validar":
            return jsonify({"error": "Acesso negado"}), 403
        return render_template("acesso_negado.html", user=""), 403

    @app.errorhandler(500)
    def handle_internal_error(err):
        if request.path.startswith("/api") or request.path == "/validar":
            return jsonify({"error": "Erro interno do servidor"}), 500
        return render_template("acesso_negado.html", user=""), 500
