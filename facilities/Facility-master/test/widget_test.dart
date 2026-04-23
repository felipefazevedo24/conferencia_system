import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';

import 'package:obra_tracker/src/app.dart';
import 'package:obra_tracker/src/data/repositories/repositories.dart';
import 'package:obra_tracker/src/domain/entities/models.dart';
import 'package:obra_tracker/src/presentation/providers/providers.dart';

void main() {
  setUpAll(() async {
    await initializeDateFormatting('pt_BR');
  });

  group('Project', () {
    test('displayCode formats id and month correctly', () {
      final project = Project(
        id: 3,
        name: 'Obra Teste',
        clientName: 'João',
        clientPhone: '',
        clientAddress: '',
        notes: '',
        createdAt: DateTime(2025, 7, 15),
      );
      expect(project.displayCode, '0003-07-25');
    });
  });

  group('WorkTask', () {
    WorkTask makeTask({
      String? photoPath,
      String? materialIssue,
      String? note,
      TaskStatus status = TaskStatus.notPlanned,
    }) {
      return WorkTask(
        id: 1,
        projectId: 1,
        title: 'Pintura',
        room: 'Sala',
        description: '',
        status: status,
        note: note ?? '',
        materialIssue: materialIssue ?? '',
        photoPath: photoPath ?? '',
        updatedAt: DateTime.now(),
      );
    }

    test('status is manual and independent of photo/note', () {
      final task = makeTask(
        photoPath: '/photo.jpg',
        status: TaskStatus.inProgress,
      );
      expect(task.status, TaskStatus.inProgress);
    });

    test('default status is notPlanned', () {
      final task = makeTask();
      expect(task.status, TaskStatus.notPlanned);
    });

    test('isCompleted reflects manual status', () {
      final task = makeTask(status: TaskStatus.completed);
      expect(task.isCompleted, true);
    });

    test('paused status works', () {
      final task = makeTask(status: TaskStatus.paused);
      expect(task.status, TaskStatus.paused);
    });

    test('planned with dates', () {
      final task = WorkTask(
        id: 1,
        projectId: 1,
        title: 'Pintura',
        status: TaskStatus.planned,
        plannedStartDate: DateTime(2026, 4, 10),
        plannedEndDate: DateTime(2026, 4, 20),
      );
      expect(task.status, TaskStatus.planned);
      expect(task.plannedStartDate, DateTime(2026, 4, 10));
      expect(task.plannedEndDate, DateTime(2026, 4, 20));
    });

    test('toMap and fromMap round-trip correctly', () {
      final original = makeTask(
        note: 'obs',
        materialIssue: '',
        photoPath: '/img.png',
      );
      final map = original.toMap();
      final restored = WorkTask.fromMap(map);
      expect(restored.title, original.title);
      expect(restored.room, original.room);
      expect(restored.photoPath, original.photoPath);
      expect(restored.note, original.note);
    });
  });

  testWidgets('app shows the project home screen', (
    tester,
  ) async {
    final fakeProjectRepository = _FakeProjectRepository([
      Project(
        id: 1,
        name: 'Reforma Apto 302',
        clientName: 'Marina',
        clientAddress: 'Rua das Flores, 120',
        notes: '',
        createdAt: DateTime(2026, 4, 10),
      ),
    ]);

    final fakeTaskRepository = _FakeWorkTaskRepository([
      WorkTask(
        id: 1,
        projectId: 1,
        title: 'Pintura',
        room: 'Sala',
        status: TaskStatus.inProgress,
        updatedAt: DateTime(2026, 4, 10),
      ),
    ]);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          projectRepositoryProvider.overrideWith(
            (ref) => fakeProjectRepository,
          ),
          workTaskRepositoryProvider.overrideWith((ref) => fakeTaskRepository),
        ],
        child: const ObraTrackerApp(),
      ),
    );

    await tester.pumpAndSettle();

    expect(find.text('Obra Tracker'), findsOneWidget);
    expect(find.text('Reforma Apto 302'), findsOneWidget);
    expect(find.textContaining('1 tarefa(s)'), findsOneWidget);
    expect(find.text('Nova obra'), findsOneWidget);
  });
}

class _FakeProjectRepository implements ProjectRepository {
  _FakeProjectRepository(this._projects);

  final List<Project> _projects;

  @override
  Future<void> deleteProject(int id) async {
    _projects.removeWhere((project) => project.id == id);
  }

  @override
  Future<List<Project>> getProjects() async => List.unmodifiable(_projects);

  @override
  Future<Project> saveProject(Project project) async {
    final index = _projects.indexWhere((item) => item.id == project.id);
    if (index >= 0) {
      _projects[index] = project;
      return project;
    }

    final created = project.copyWith(id: _projects.length + 1);
    _projects.add(created);
    return created;
  }
}

class _FakeWorkTaskRepository implements WorkTaskRepository {
  _FakeWorkTaskRepository(this._tasks);

  final List<WorkTask> _tasks;

  @override
  Future<void> deleteTask(int id) async {
    _tasks.removeWhere((task) => task.id == id);
  }

  @override
  Future<List<WorkTask>> getTasksForProject(int projectId) async {
    return _tasks.where((task) => task.projectId == projectId).toList();
  }

  @override
  Future<WorkTask> saveTask(WorkTask task) async {
    final index = _tasks.indexWhere((item) => item.id == task.id);
    if (index >= 0) {
      _tasks[index] = task;
      return task;
    }

    final created = task.copyWith(id: _tasks.length + 1);
    _tasks.add(created);
    return created;
  }
}

