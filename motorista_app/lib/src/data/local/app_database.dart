import 'package:path/path.dart';
import 'package:sqflite/sqflite.dart';

/// Singleton do banco local (sqflite), mesmo padrão usado no obra_tracker
/// (facilities/Facility-master/lib/src/data/local/app_database.dart).
///
/// Hoje guarda só a fila de pontos de GPS que falharam ao enviar — o motivo
/// de existir é justamente corrigir o bug do app web, onde essa fila vivia
/// só em uma variável JS e sumia se a aba morresse.
class AppDatabase {
  AppDatabase._internal();
  static final AppDatabase instance = AppDatabase._internal();

  static Database? _db;

  Future<Database> get database async {
    final existing = _db;
    if (existing != null) return existing;
    final db = await _open();
    _db = db;
    return db;
  }

  Future<Database> _open() async {
    final dbPath = await getDatabasesPath();
    final path = join(dbPath, 'motorista_app.db');
    return openDatabase(
      path,
      version: 1,
      onCreate: (db, version) async {
        await _createV1Tables(db);
      },
    );
  }

  Future<void> _createV1Tables(Database db) async {
    await db.execute('''
      CREATE TABLE pending_pings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vid INTEGER NOT NULL,
        token TEXT NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        velocidade_kmh REAL,
        rumo REAL,
        precisao_m REAL,
        criado_em TEXT NOT NULL
      )
    ''');
    await db.execute('CREATE INDEX idx_pending_pings_vid ON pending_pings (vid)');
  }
}
