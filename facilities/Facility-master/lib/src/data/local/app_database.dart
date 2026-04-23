import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:sqflite_common_ffi_web/sqflite_ffi_web.dart';

class AppDatabase {
  AppDatabase._();

  static final AppDatabase instance = AppDatabase._();
  static bool _initialized = false;

  static Future<void> initialize() async {
    if (_initialized) return;

    if (kIsWeb) {
      databaseFactory = databaseFactoryFfiWeb;
    } else if (Platform.isWindows || Platform.isLinux || Platform.isMacOS) {
      sqfliteFfiInit();
      databaseFactory = databaseFactoryFfi;
    }

    _initialized = true;
  }

  Database? _database;

  Future<Database> get database async {
    _database ??= await _open();
    return _database!;
  }

  Future<Database> _open() async {
    final dbPath = await getDatabasesPath();
    final path = p.join(dbPath, 'obra_tracker.db');

    return openDatabase(
      path,
      version: 6,
      onConfigure: (db) async {
        await db.execute('PRAGMA foreign_keys = ON');
      },
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE projects(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            client_name TEXT NOT NULL,
            client_phone TEXT,
            client_address TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
          )
        ''');

        await db.execute('''
          CREATE TABLE work_tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            room TEXT,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'notPlanned',
            note TEXT,
            material_issue TEXT,
            photo_path TEXT,
            updated_at TEXT,
            planned_start_date TEXT,
            planned_end_date TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
          )
        ''');

        await _createV3Tables(db);
        await _createV4Tables(db);
        await _createV5Tables(db);
        await _createV6Migrations(db);
      },
      onUpgrade: (db, oldVersion, newVersion) async {
        if (oldVersion < 2) {
          await db.execute('ALTER TABLE work_tasks ADD COLUMN planned_start_date TEXT');
          await db.execute('ALTER TABLE work_tasks ADD COLUMN planned_end_date TEXT');
          await db.execute("UPDATE work_tasks SET status = 'paused' WHERE status = 'blocked'");
          await db.execute("UPDATE work_tasks SET status = 'notPlanned' WHERE status = 'planned'");
        }
        if (oldVersion < 3) {
          await _createV3Tables(db);
        }
        if (oldVersion < 4) {
          await _createV4Tables(db);
        }
        if (oldVersion < 5) {
          await _createV5Tables(db);
        }
        if (oldVersion < 6) {
          await _createV6Migrations(db);
        }
      },
    );
  }

  static Future<void> _createV3Tables(Database db) async {
    await db.execute('''
      CREATE TABLE IF NOT EXISTS collaborators(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        role TEXT,
        phone TEXT,
        access_level TEXT NOT NULL DEFAULT 'requester',
        created_at TEXT NOT NULL
      )
    ''');

    await db.execute('''
      CREATE TABLE IF NOT EXISTS cleaning_schedules(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collaborator_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        location TEXT,
        scheduled_date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        notes TEXT,
        done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY (collaborator_id) REFERENCES collaborators(id) ON DELETE CASCADE
      )
    ''');
  }

  static Future<void> _createV4Tables(Database db) async {
    await _addColumnIfNotExists(
      db, 'collaborators', 'access_level', "TEXT NOT NULL DEFAULT 'requester'",
    );

    await db.execute('''
      CREATE TABLE IF NOT EXISTS epi_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collaborator_id INTEGER NOT NULL,
        requested_by_collaborator_id INTEGER NOT NULL,
        released_by_collaborator_id INTEGER,
        request_type TEXT NOT NULL,
        item_name TEXT NOT NULL,
        size TEXT,
        quantity INTEGER NOT NULL DEFAULT 1,
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'requested',
        requested_at TEXT NOT NULL,
        released_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (collaborator_id) REFERENCES collaborators(id) ON DELETE CASCADE,
        FOREIGN KEY (requested_by_collaborator_id) REFERENCES collaborators(id) ON DELETE CASCADE,
        FOREIGN KEY (released_by_collaborator_id) REFERENCES collaborators(id) ON DELETE SET NULL
      )
    ''');
  }

  static Future<void> _createV5Tables(Database db) async {
    await db.execute('''
      CREATE TABLE IF NOT EXISTS materials(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        internal_code TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        request_type TEXT NOT NULL DEFAULT 'epi',
        created_at TEXT NOT NULL
      )
    ''');
  }

  static Future<void> _createV6Migrations(Database db) async {
    await _addColumnIfNotExists(db, 'materials', 'internal_code', "TEXT DEFAULT ''");
    await _addColumnIfNotExists(db, 'epi_requests', 'internal_code', "TEXT DEFAULT ''");
  }

  static Future<void> _addColumnIfNotExists(
    Database db,
    String table,
    String column,
    String type,
  ) async {
    final cols = await db.rawQuery('PRAGMA table_info($table)');
    final exists = cols.any((row) => row['name'] == column);
    if (!exists) {
      await db.execute('ALTER TABLE $table ADD COLUMN $column $type');
    }
  }

  Future<void> resetAllData() async {
    final db = await database;
    await db.delete('materials');
    await db.delete('epi_requests');
    await db.delete('cleaning_schedules');
    await db.delete('collaborators');
    await db.delete('work_tasks');
    await db.delete('projects');
  }
}
