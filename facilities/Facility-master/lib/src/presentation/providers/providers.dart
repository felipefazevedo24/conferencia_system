import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/services/epi_report_service.dart';
import '../../core/services/pdf_report_service.dart';
import '../../data/local/app_database.dart';
import '../../data/repositories/repositories.dart';
import '../../domain/entities/models.dart';

final databaseProvider = Provider<AppDatabase>((ref) => AppDatabase.instance);

final projectRepositoryProvider = Provider<ProjectRepository>((ref) {
  return ProjectRepositoryImpl(ref.watch(databaseProvider));
});

final workTaskRepositoryProvider = Provider<WorkTaskRepository>((ref) {
  return WorkTaskRepositoryImpl(ref.watch(databaseProvider));
});

final pdfReportServiceProvider = Provider<PdfReportService>((ref) {
  return PdfReportService();
});

final epiReportServiceProvider = Provider<EpiReportService>((ref) {
  return EpiReportService();
});

final projectsProvider = FutureProvider<List<Project>>((ref) async {
  return ref.watch(projectRepositoryProvider).getProjects();
});

final projectTasksProvider =
    FutureProvider.family<List<WorkTask>, int>((ref, projectId) async {
  return ref.watch(workTaskRepositoryProvider).getTasksForProject(projectId);
});

final collaboratorRepositoryProvider = Provider<CollaboratorRepository>((ref) {
  return CollaboratorRepositoryImpl(ref.watch(databaseProvider));
});

final collaboratorsProvider = FutureProvider<List<Collaborator>>((ref) async {
  return ref.watch(collaboratorRepositoryProvider).getCollaborators();
});

final cleaningScheduleRepositoryProvider = Provider<CleaningScheduleRepository>((ref) {
  return CleaningScheduleRepositoryImpl(ref.watch(databaseProvider));
});

final cleaningSchedulesProvider = FutureProvider<List<CleaningSchedule>>((ref) async {
  return ref.watch(cleaningScheduleRepositoryProvider).getSchedules();
});

final epiRequestRepositoryProvider = Provider<EpiRequestRepository>((ref) {
  return EpiRequestRepositoryImpl(ref.watch(databaseProvider));
});

final epiRequestsProvider = FutureProvider<List<EpiRequest>>((ref) async {
  return ref.watch(epiRequestRepositoryProvider).getRequests();
});

final materialRepositoryProvider = Provider<MaterialRepository>((ref) {
  return MaterialRepositoryImpl(ref.watch(databaseProvider));
});

final materialsProvider = FutureProvider<List<EpiMaterial>>((ref) async {
  return ref.watch(materialRepositoryProvider).getMaterials();
});
