import '../local/app_database.dart';
import '../../domain/entities/models.dart';

abstract class ProjectRepository {
  Future<List<Project>> getProjects();
  Future<Project> saveProject(Project project);
  Future<void> deleteProject(int id);
}

abstract class WorkTaskRepository {
  Future<List<WorkTask>> getTasksForProject(int projectId);
  Future<WorkTask> saveTask(WorkTask task);
  Future<void> deleteTask(int id);
}

abstract class CollaboratorRepository {
  Future<List<Collaborator>> getCollaborators();
  Future<Collaborator> saveCollaborator(Collaborator collaborator);
  Future<void> deleteCollaborator(int id);
}

abstract class CleaningScheduleRepository {
  Future<List<CleaningSchedule>> getSchedules();
  Future<List<CleaningSchedule>> getSchedulesByCollaborator(int collaboratorId);
  Future<CleaningSchedule> saveSchedule(CleaningSchedule schedule);
  Future<List<CleaningSchedule>> saveSchedules(List<CleaningSchedule> schedules);
  Future<void> deleteSchedule(int id);
  Future<void> toggleDone(int id, bool done);
}

abstract class MaterialRepository {
  Future<List<EpiMaterial>> getMaterials();
  Future<List<EpiMaterial>> getMaterialsByType(EpiRequestType type);
  Future<EpiMaterial> saveMaterial(EpiMaterial material);
  Future<void> deleteMaterial(int id);
}

abstract class EpiRequestRepository {
  Future<List<EpiRequest>> getRequests();
  Future<EpiRequest> saveRequest(EpiRequest request);
  Future<void> updateRequestStatus({
    required int id,
    required EpiRequestStatus status,
    required int releasedByCollaboratorId,
  });
}

class ProjectRepositoryImpl implements ProjectRepository {
  ProjectRepositoryImpl(this._db);
  final AppDatabase _db;

  @override
  Future<List<Project>> getProjects() async {
    final db = await _db.database;
    final rows = await db.query('projects', orderBy: 'created_at DESC');
    return rows.map(Project.fromMap).toList();
  }

  @override
  Future<Project> saveProject(Project project) async {
    final db = await _db.database;
    final map = project.toMap();

    if (project.id != null) {
      await db.update('projects', map, where: 'id = ?', whereArgs: [project.id]);
      return project;
    }

    final id = await db.insert('projects', map);
    return project.copyWith(id: id);
  }

  @override
  Future<void> deleteProject(int id) async {
    final db = await _db.database;
    await db.delete('projects', where: 'id = ?', whereArgs: [id]);
  }
}

class WorkTaskRepositoryImpl implements WorkTaskRepository {
  WorkTaskRepositoryImpl(this._db);
  final AppDatabase _db;

  @override
  Future<List<WorkTask>> getTasksForProject(int projectId) async {
    final db = await _db.database;
    final rows = await db.query(
      'work_tasks',
      where: 'project_id = ?',
      whereArgs: [projectId],
      orderBy: 'id ASC',
    );
    return rows.map(WorkTask.fromMap).toList();
  }

  @override
  Future<WorkTask> saveTask(WorkTask task) async {
    final db = await _db.database;
    final map = task.toMap();

    if (task.id != null) {
      await db.update('work_tasks', map, where: 'id = ?', whereArgs: [task.id]);
      return task.copyWith(updatedAt: DateTime.now());
    }

    final id = await db.insert('work_tasks', map);
    return task.copyWith(id: id, updatedAt: DateTime.now());
  }

  @override
  Future<void> deleteTask(int id) async {
    final db = await _db.database;
    await db.delete('work_tasks', where: 'id = ?', whereArgs: [id]);
  }
}

class CollaboratorRepositoryImpl implements CollaboratorRepository {
  CollaboratorRepositoryImpl(this._db);
  final AppDatabase _db;

  @override
  Future<List<Collaborator>> getCollaborators() async {
    final db = await _db.database;
    final rows = await db.query('collaborators', orderBy: 'name ASC');
    return rows.map(Collaborator.fromMap).toList();
  }

  @override
  Future<Collaborator> saveCollaborator(Collaborator collaborator) async {
    final db = await _db.database;
    final map = collaborator.toMap();

    if (collaborator.id != null) {
      await db.update('collaborators', map, where: 'id = ?', whereArgs: [collaborator.id]);
      return collaborator;
    }

    final id = await db.insert('collaborators', map);
    return collaborator.copyWith(id: id);
  }

  @override
  Future<void> deleteCollaborator(int id) async {
    final db = await _db.database;
    await db.delete('collaborators', where: 'id = ?', whereArgs: [id]);
  }
}

class CleaningScheduleRepositoryImpl implements CleaningScheduleRepository {
  CleaningScheduleRepositoryImpl(this._db);
  final AppDatabase _db;

  @override
  Future<List<CleaningSchedule>> getSchedules() async {
    final db = await _db.database;
    final rows = await db.rawQuery('''
      SELECT cs.*, c.name AS collaborator_name
      FROM cleaning_schedules cs
      LEFT JOIN collaborators c ON c.id = cs.collaborator_id
      ORDER BY cs.scheduled_date ASC, cs.start_time ASC
    ''');
    return rows.map(CleaningSchedule.fromMap).toList();
  }

  @override
  Future<List<CleaningSchedule>> getSchedulesByCollaborator(int collaboratorId) async {
    final db = await _db.database;
    final rows = await db.rawQuery('''
      SELECT cs.*, c.name AS collaborator_name
      FROM cleaning_schedules cs
      LEFT JOIN collaborators c ON c.id = cs.collaborator_id
      WHERE cs.collaborator_id = ?
      ORDER BY cs.scheduled_date ASC, cs.start_time ASC
    ''', [collaboratorId]);
    return rows.map(CleaningSchedule.fromMap).toList();
  }

  @override
  Future<CleaningSchedule> saveSchedule(CleaningSchedule schedule) async {
    final db = await _db.database;
    final map = schedule.toMap();

    if (schedule.id != null) {
      await db.update('cleaning_schedules', map, where: 'id = ?', whereArgs: [schedule.id]);
      return schedule;
    }

    final id = await db.insert('cleaning_schedules', map);
    return schedule.copyWith(id: id);
  }

  @override
  Future<List<CleaningSchedule>> saveSchedules(List<CleaningSchedule> schedules) async {
    if (schedules.isEmpty) return <CleaningSchedule>[];
    final db = await _db.database;
    final batch = db.batch();

    for (final schedule in schedules) {
      batch.insert('cleaning_schedules', schedule.toMap());
    }

    final resultIds = await batch.commit(noResult: false);
    final created = <CleaningSchedule>[];
    for (var i = 0; i < schedules.length; i++) {
      created.add(schedules[i].copyWith(id: resultIds[i] as int?));
    }
    return created;
  }

  @override
  Future<void> deleteSchedule(int id) async {
    final db = await _db.database;
    await db.delete('cleaning_schedules', where: 'id = ?', whereArgs: [id]);
  }

  @override
  Future<void> toggleDone(int id, bool done) async {
    final db = await _db.database;
    await db.update('cleaning_schedules', {'done': done ? 1 : 0}, where: 'id = ?', whereArgs: [id]);
  }
}

class MaterialRepositoryImpl implements MaterialRepository {
  MaterialRepositoryImpl(this._db);
  final AppDatabase _db;

  @override
  Future<List<EpiMaterial>> getMaterials() async {
    final db = await _db.database;
    final rows = await db.query('materials', orderBy: 'name ASC');
    return rows.map(EpiMaterial.fromMap).toList();
  }

  @override
  Future<List<EpiMaterial>> getMaterialsByType(EpiRequestType type) async {
    final db = await _db.database;
    final rows = await db.query(
      'materials',
      where: 'request_type = ?',
      whereArgs: [type.code],
      orderBy: 'name ASC',
    );
    return rows.map(EpiMaterial.fromMap).toList();
  }

  @override
  Future<EpiMaterial> saveMaterial(EpiMaterial material) async {
    final db = await _db.database;
    final map = material.toMap();

    if (material.id != null) {
      await db.update('materials', map, where: 'id = ?', whereArgs: [material.id]);
      return material;
    }

    final id = await db.insert('materials', map);
    return material.copyWith(id: id);
  }

  @override
  Future<void> deleteMaterial(int id) async {
    final db = await _db.database;
    await db.delete('materials', where: 'id = ?', whereArgs: [id]);
  }
}

class EpiRequestRepositoryImpl implements EpiRequestRepository {
  EpiRequestRepositoryImpl(this._db);
  final AppDatabase _db;

  @override
  Future<List<EpiRequest>> getRequests() async {
    final db = await _db.database;
    final rows = await db.rawQuery('''
      SELECT er.*, c.name AS collaborator_name, req.name AS requested_by_name, rel.name AS released_by_name
      FROM epi_requests er
      LEFT JOIN collaborators c ON c.id = er.collaborator_id
      LEFT JOIN collaborators req ON req.id = er.requested_by_collaborator_id
      LEFT JOIN collaborators rel ON rel.id = er.released_by_collaborator_id
      ORDER BY er.requested_at DESC, er.id DESC
    ''');
    return rows.map(EpiRequest.fromMap).toList();
  }

  @override
  Future<EpiRequest> saveRequest(EpiRequest request) async {
    final db = await _db.database;
    final map = request.toMap();

    if (request.id != null) {
      await db.update('epi_requests', map, where: 'id = ?', whereArgs: [request.id]);
      return request;
    }

    final id = await db.insert('epi_requests', map);
    return request.copyWith(id: id);
  }

  @override
  Future<void> updateRequestStatus({required int id, required EpiRequestStatus status, required int releasedByCollaboratorId}) async {
    final db = await _db.database;
    await db.update(
      'epi_requests',
      {
        'status': status.code,
        'released_by_collaborator_id': releasedByCollaboratorId,
        'released_at': DateTime.now().toIso8601String(),
      },
      where: 'id = ?',
      whereArgs: [id],
    );
  }
}
