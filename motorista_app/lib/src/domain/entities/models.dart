/// Modelos de dados do app do motorista. Classes simples, sem geração de
/// código (sem freezed/json_serializable) — mesmo estilo do obra_tracker.
library;

class MotoristaConfig {
  final int motoristaId;
  final String token;
  final String? motoristaNome;

  const MotoristaConfig({
    required this.motoristaId,
    required this.token,
    this.motoristaNome,
  });
}

class Viagem {
  final int id;
  final String codigo;
  final String status; // Planejada | EmAndamento | Concluida | Cancelada
  final String? titulo;
  final String? veiculoLabel;
  final String? motoristaNome;
  final int? kmInicial;
  final int? kmFinal;
  final double? kmPercorrido;
  final String? motoristaLink; // URL completa com vid + token específico da viagem

  const Viagem({
    required this.id,
    required this.codigo,
    required this.status,
    this.titulo,
    this.veiculoLabel,
    this.motoristaNome,
    this.kmInicial,
    this.kmFinal,
    this.kmPercorrido,
    this.motoristaLink,
  });

  bool get finalizada => status == 'Concluida' || status == 'Cancelada';

  factory Viagem.fromJson(Map<String, dynamic> json) {
    return Viagem(
      id: json['id'] as int,
      codigo: json['codigo'] as String? ?? '',
      status: json['status'] as String? ?? '',
      titulo: json['titulo'] as String?,
      veiculoLabel: json['veiculo_label'] as String?,
      motoristaNome: json['motorista_nome'] as String?,
      kmInicial: (json['km_inicial'] as num?)?.toInt(),
      kmFinal: (json['km_final'] as num?)?.toInt(),
      kmPercorrido: (json['km_percorrido'] as num?)?.toDouble(),
      motoristaLink: json['motorista_link'] as String?,
    );
  }

  /// Extrai (vid, token) do campo motorista_link, ex.:
  /// https://sync.columbiamachine.com.br/motorista/viagem/123/abcdef0123456789
  ({int vid, String token})? get viagemToken {
    final link = motoristaLink;
    if (link == null) return null;
    final match = RegExp(r'/motorista/viagem/(\d+)/([a-f0-9]+)').firstMatch(link);
    if (match == null) return null;
    return (vid: int.parse(match.group(1)!), token: match.group(2)!);
  }
}

class Parada {
  final int id;
  final int sequencia;
  final String tipo; // COLETA | ENTREGA | PARADA | ABASTECIMENTO | REFEICAO
  final String? parceiroNome;
  final String? endereco;
  final String? cidade;
  final String? uf;
  final double? latitude;
  final double? longitude;
  final DateTime? previsaoChegada;
  final DateTime? chegadaReal;
  final String status; // Pendente | EmAndamento | Concluida | Nao_realizada | Cancelada
  final String? observacao;

  const Parada({
    required this.id,
    required this.sequencia,
    required this.tipo,
    this.parceiroNome,
    this.endereco,
    this.cidade,
    this.uf,
    this.latitude,
    this.longitude,
    this.previsaoChegada,
    this.chegadaReal,
    required this.status,
    this.observacao,
  });

  bool get concluida =>
      status == 'Concluida' || status == 'Nao_realizada' || status == 'Cancelada';
  bool get noLocal => status == 'EmAndamento';

  factory Parada.fromJson(Map<String, dynamic> json) {
    DateTime? parseDate(dynamic v) => v == null ? null : DateTime.tryParse(v as String);
    return Parada(
      id: json['id'] as int,
      sequencia: json['sequencia'] as int? ?? 0,
      tipo: json['tipo'] as String? ?? 'PARADA',
      parceiroNome: json['parceiro_nome'] as String?,
      endereco: json['endereco'] as String?,
      cidade: json['cidade'] as String?,
      uf: json['uf'] as String?,
      latitude: (json['latitude'] as num?)?.toDouble(),
      longitude: (json['longitude'] as num?)?.toDouble(),
      previsaoChegada: parseDate(json['previsao_chegada']),
      chegadaReal: parseDate(json['chegada_real']),
      status: json['status'] as String? ?? 'Pendente',
      observacao: json['observacao'] as String?,
    );
  }
}

/// Ponto de GPS que falhou ao enviar e fica salvo em disco (sqflite) até
/// conseguir ser drenado. Corrige o bug do app web, onde a fila offline
/// vivia só em memória e se perdia ao fechar/matar a aba.
class PendingPing {
  final int? id;
  final int vid;
  final String token;
  final double latitude;
  final double longitude;
  final double? velocidadeKmh;
  final double? rumo;
  final double? precisaoM;
  final DateTime criadoEm;

  const PendingPing({
    this.id,
    required this.vid,
    required this.token,
    required this.latitude,
    required this.longitude,
    this.velocidadeKmh,
    this.rumo,
    this.precisaoM,
    required this.criadoEm,
  });

  Map<String, Object?> toMap() {
    return {
      'id': id,
      'vid': vid,
      'token': token,
      'latitude': latitude,
      'longitude': longitude,
      'velocidade_kmh': velocidadeKmh,
      'rumo': rumo,
      'precisao_m': precisaoM,
      'criado_em': criadoEm.toIso8601String(),
    };
  }

  factory PendingPing.fromMap(Map<String, Object?> map) {
    return PendingPing(
      id: map['id'] as int?,
      vid: map['vid'] as int,
      token: map['token'] as String,
      latitude: map['latitude'] as double,
      longitude: map['longitude'] as double,
      velocidadeKmh: map['velocidade_kmh'] as double?,
      rumo: map['rumo'] as double?,
      precisaoM: map['precisao_m'] as double?,
      criadoEm: DateTime.parse(map['criado_em'] as String),
    );
  }

  Map<String, dynamic> toPingPayload() {
    return {
      'latitude': latitude,
      'longitude': longitude,
      if (velocidadeKmh != null) 'velocidade_kmh': velocidadeKmh,
      if (rumo != null) 'rumo': rumo,
      if (precisaoM != null) 'precisao_m': precisaoM,
    };
  }
}
