enum TaskStatus {
  notPlanned('Não Planejado'),
  planned('Planejado'),
  inProgress('Em andamento'),
  paused('Pausado'),
  completed('Concluído');

  const TaskStatus(this.label);
  final String label;

  String get code => name;

  static TaskStatus fromCode(String code) {
    if (code == 'blocked') return TaskStatus.paused;
    return TaskStatus.values.firstWhere(
      (s) => s.code == code,
      orElse: () => TaskStatus.notPlanned,
    );
  }
}

class Project {
  const Project({
    this.id,
    required this.name,
    required this.clientName,
    this.clientPhone = '',
    this.clientAddress = '',
    this.notes = '',
    required this.createdAt,
  });

  final int? id;
  final String name;
  final String clientName;
  final String clientPhone;
  final String clientAddress;
  final String notes;
  final DateTime createdAt;

  String get displayCode {
    final seq = (id ?? 0).toString().padLeft(4, '0');
    final month = createdAt.month.toString().padLeft(2, '0');
    final year = (createdAt.year % 100).toString().padLeft(2, '0');
    return '$seq-$month-$year';
  }

  Project copyWith({
    int? id,
    String? name,
    String? clientName,
    String? clientPhone,
    String? clientAddress,
    String? notes,
    DateTime? createdAt,
  }) {
    return Project(
      id: id ?? this.id,
      name: name ?? this.name,
      clientName: clientName ?? this.clientName,
      clientPhone: clientPhone ?? this.clientPhone,
      clientAddress: clientAddress ?? this.clientAddress,
      notes: notes ?? this.notes,
      createdAt: createdAt ?? this.createdAt,
    );
  }

  Map<String, dynamic> toMap() {
    return {
      if (id != null) 'id': id,
      'name': name,
      'client_name': clientName,
      'client_phone': clientPhone,
      'client_address': clientAddress,
      'notes': notes,
      'created_at': createdAt.toIso8601String(),
    };
  }

  factory Project.fromMap(Map<String, dynamic> map) {
    return Project(
      id: map['id'] as int?,
      name: map['name'] as String? ?? '',
      clientName: map['client_name'] as String? ?? '',
      clientPhone: map['client_phone'] as String? ?? '',
      clientAddress: map['client_address'] as String? ?? '',
      notes: map['notes'] as String? ?? '',
      createdAt: DateTime.tryParse(map['created_at'] as String? ?? '') ??
          DateTime.now(),
    );
  }
}

class WorkTask {
  const WorkTask({
    this.id,
    required this.projectId,
    required this.title,
    this.room = '',
    this.description = '',
    this.status = TaskStatus.notPlanned,
    this.note,
    this.materialIssue,
    this.photoPath,
    this.updatedAt,
    this.plannedStartDate,
    this.plannedEndDate,
  });

  final int? id;
  final int projectId;
  final String title;
  final String room;
  final String description;
  final TaskStatus status;
  final String? note;
  final String? materialIssue;
  final String? photoPath;
  final DateTime? updatedAt;
  final DateTime? plannedStartDate;
  final DateTime? plannedEndDate;

  bool get isCompleted => status == TaskStatus.completed;
  bool get hasPhoto => photoPath != null && photoPath!.isNotEmpty;

  WorkTask copyWith({
    int? id,
    int? projectId,
    String? title,
    String? room,
    String? description,
    TaskStatus? status,
    String? note,
    bool clearNote = false,
    String? materialIssue,
    bool clearMaterialIssue = false,
    String? photoPath,
    bool clearPhoto = false,
    DateTime? updatedAt,
    DateTime? plannedStartDate,
    bool clearPlannedStartDate = false,
    DateTime? plannedEndDate,
    bool clearPlannedEndDate = false,
  }) {
    return WorkTask(
      id: id ?? this.id,
      projectId: projectId ?? this.projectId,
      title: title ?? this.title,
      room: room ?? this.room,
      description: description ?? this.description,
      status: status ?? this.status,
      note: clearNote ? null : (note ?? this.note),
      materialIssue:
          clearMaterialIssue ? null : (materialIssue ?? this.materialIssue),
      photoPath: clearPhoto ? null : (photoPath ?? this.photoPath),
      updatedAt: updatedAt ?? this.updatedAt,
      plannedStartDate: clearPlannedStartDate ? null : (plannedStartDate ?? this.plannedStartDate),
      plannedEndDate: clearPlannedEndDate ? null : (plannedEndDate ?? this.plannedEndDate),
    );
  }

  Map<String, dynamic> toMap() {
    return {
      if (id != null) 'id': id,
      'project_id': projectId,
      'title': title,
      'room': room,
      'description': description,
      'status': status.code,
      'note': note,
      'material_issue': materialIssue,
      'photo_path': photoPath,
      'updated_at': (updatedAt ?? DateTime.now()).toIso8601String(),
      'planned_start_date': plannedStartDate?.toIso8601String(),
      'planned_end_date': plannedEndDate?.toIso8601String(),
    };
  }

  factory WorkTask.fromMap(Map<String, dynamic> map) {
    return WorkTask(
      id: map['id'] as int?,
      projectId: map['project_id'] as int? ?? 0,
      title: map['title'] as String? ?? '',
      room: map['room'] as String? ?? '',
      description: map['description'] as String? ?? '',
      status: TaskStatus.fromCode(map['status'] as String? ?? 'planned'),
      note: map['note'] as String?,
      materialIssue: map['material_issue'] as String?,
      photoPath: map['photo_path'] as String?,
      updatedAt: DateTime.tryParse(map['updated_at'] as String? ?? ''),
      plannedStartDate: DateTime.tryParse(map['planned_start_date'] as String? ?? ''),
      plannedEndDate: DateTime.tryParse(map['planned_end_date'] as String? ?? ''),
    );
  }
}

class Collaborator {
  const Collaborator({
    this.id,
    required this.name,
    this.role = '',
    this.phone = '',
    this.accessLevel = CollaboratorAccessLevel.requester,
    required this.createdAt,
  });

  final int? id;
  final String name;
  final String role;
  final String phone;
  final CollaboratorAccessLevel accessLevel;
  final DateTime createdAt;

  Collaborator copyWith({
    int? id,
    String? name,
    String? role,
    String? phone,
    CollaboratorAccessLevel? accessLevel,
    DateTime? createdAt,
  }) {
    return Collaborator(
      id: id ?? this.id,
      name: name ?? this.name,
      role: role ?? this.role,
      phone: phone ?? this.phone,
      accessLevel: accessLevel ?? this.accessLevel,
      createdAt: createdAt ?? this.createdAt,
    );
  }

  Map<String, dynamic> toMap() {
    return {
      if (id != null) 'id': id,
      'name': name,
      'role': role,
      'phone': phone,
      'access_level': accessLevel.code,
      'created_at': createdAt.toIso8601String(),
    };
  }

  factory Collaborator.fromMap(Map<String, dynamic> map) {
    return Collaborator(
      id: map['id'] as int?,
      name: map['name'] as String? ?? '',
      role: map['role'] as String? ?? '',
      phone: map['phone'] as String? ?? '',
      accessLevel: CollaboratorAccessLevel.fromCode(
        map['access_level'] as String? ?? 'requester',
      ),
      createdAt: DateTime.tryParse(map['created_at'] as String? ?? '') ?? DateTime.now(),
    );
  }
}

enum CollaboratorAccessLevel {
  requester('Solicitante'),
  manager('Gestor liberador');

  const CollaboratorAccessLevel(this.label);
  final String label;

  String get code => name;

  static CollaboratorAccessLevel fromCode(String code) {
    return CollaboratorAccessLevel.values.firstWhere(
      (level) => level.code == code,
      orElse: () => CollaboratorAccessLevel.requester,
    );
  }
}

class CleaningSchedule {
  const CleaningSchedule({
    this.id,
    required this.collaboratorId,
    required this.title,
    this.location = '',
    required this.scheduledDate,
    required this.startTime,
    required this.endTime,
    this.notes = '',
    this.done = false,
    this.collaboratorName,
    required this.createdAt,
  });

  final int? id;
  final int collaboratorId;
  final String title;
  final String location;
  final DateTime scheduledDate;
  final String startTime;
  final String endTime;
  final String notes;
  final bool done;
  final String? collaboratorName;
  final DateTime createdAt;

  CleaningSchedule copyWith({
    int? id,
    int? collaboratorId,
    String? title,
    String? location,
    DateTime? scheduledDate,
    String? startTime,
    String? endTime,
    String? notes,
    bool? done,
    String? collaboratorName,
    DateTime? createdAt,
  }) {
    return CleaningSchedule(
      id: id ?? this.id,
      collaboratorId: collaboratorId ?? this.collaboratorId,
      title: title ?? this.title,
      location: location ?? this.location,
      scheduledDate: scheduledDate ?? this.scheduledDate,
      startTime: startTime ?? this.startTime,
      endTime: endTime ?? this.endTime,
      notes: notes ?? this.notes,
      done: done ?? this.done,
      collaboratorName: collaboratorName ?? this.collaboratorName,
      createdAt: createdAt ?? this.createdAt,
    );
  }

  Map<String, dynamic> toMap() {
    return {
      if (id != null) 'id': id,
      'collaborator_id': collaboratorId,
      'title': title,
      'location': location,
      'scheduled_date': scheduledDate.toIso8601String(),
      'start_time': startTime,
      'end_time': endTime,
      'notes': notes,
      'done': done ? 1 : 0,
      'created_at': createdAt.toIso8601String(),
    };
  }

  factory CleaningSchedule.fromMap(Map<String, dynamic> map) {
    return CleaningSchedule(
      id: map['id'] as int?,
      collaboratorId: map['collaborator_id'] as int? ?? 0,
      title: map['title'] as String? ?? '',
      location: map['location'] as String? ?? '',
      scheduledDate: DateTime.tryParse(map['scheduled_date'] as String? ?? '') ?? DateTime.now(),
      startTime: map['start_time'] as String? ?? '',
      endTime: map['end_time'] as String? ?? '',
      notes: map['notes'] as String? ?? '',
      done: (map['done'] as int? ?? 0) == 1,
      collaboratorName: map['collaborator_name'] as String?,
      createdAt: DateTime.tryParse(map['created_at'] as String? ?? '') ?? DateTime.now(),
    );
  }
}

enum CleaningRecurrenceFrequency {
  none('Não repetir'),
  daily('Diária'),
  weekly('Semanal'),
  biweekly('Quinzenal'),
  monthly('Mensal'),
  weekdays('Dias da semana');

  const CleaningRecurrenceFrequency(this.label);
  final String label;
}

List<CleaningSchedule> generateRecurringCleaningSchedules({
  required CleaningSchedule baseSchedule,
  required CleaningRecurrenceFrequency recurrence,
  required DateTime recurrenceEndDate,
  Set<int>? selectedWeekdays,
}) {
  final normalizedEnd = DateTime(recurrenceEndDate.year, recurrenceEndDate.month, recurrenceEndDate.day);
  final startDate = DateTime(baseSchedule.scheduledDate.year, baseSchedule.scheduledDate.month, baseSchedule.scheduledDate.day);

  if (normalizedEnd.isBefore(startDate)) return [baseSchedule];
  if (recurrence == CleaningRecurrenceFrequency.none) return [baseSchedule];

  if (recurrence == CleaningRecurrenceFrequency.weekdays) {
    final weekdays = selectedWeekdays ?? {startDate.weekday};
    if (weekdays.isEmpty) return [baseSchedule];

    final schedules = <CleaningSchedule>[];
    for (var date = startDate; !date.isAfter(normalizedEnd); date = date.add(const Duration(days: 1))) {
      if (weekdays.contains(date.weekday)) {
        schedules.add(baseSchedule.copyWith(scheduledDate: date));
      }
    }
    return schedules;
  }

  final schedules = <CleaningSchedule>[baseSchedule];
  var currentDate = startDate;

  while (true) {
    final nextDate = _nextRecurringDate(currentDate, recurrence);
    if (nextDate.isAfter(normalizedEnd)) break;
    schedules.add(baseSchedule.copyWith(scheduledDate: nextDate));
    currentDate = nextDate;
  }

  return schedules;
}

DateTime _nextRecurringDate(DateTime from, CleaningRecurrenceFrequency recurrence) {
  switch (recurrence) {
    case CleaningRecurrenceFrequency.daily:
      return from.add(const Duration(days: 1));
    case CleaningRecurrenceFrequency.weekly:
      return from.add(const Duration(days: 7));
    case CleaningRecurrenceFrequency.biweekly:
      return from.add(const Duration(days: 14));
    case CleaningRecurrenceFrequency.monthly:
      final month = from.month == 12 ? 1 : from.month + 1;
      final year = from.month == 12 ? from.year + 1 : from.year;
      final lastDayOfTargetMonth = DateTime(year, month + 1, 0).day;
      final adjustedDay = from.day <= lastDayOfTargetMonth ? from.day : lastDayOfTargetMonth;
      return DateTime(year, month, adjustedDay);
    case CleaningRecurrenceFrequency.weekdays:
    case CleaningRecurrenceFrequency.none:
      return from;
  }
}

enum EpiRequestType {
  uniform('Uniforme'),
  epi('EPI');

  const EpiRequestType(this.label);
  final String label;

  String get code => name;

  static EpiRequestType fromCode(String code) {
    return EpiRequestType.values.firstWhere(
      (type) => type.code == code,
      orElse: () => EpiRequestType.epi,
    );
  }
}

class EpiMaterial {
  const EpiMaterial({
    this.id,
    required this.internalCode,
    required this.name,
    this.requestType = EpiRequestType.epi,
    required this.createdAt,
  });

  final int? id;
  final String internalCode;
  final String name;
  final EpiRequestType requestType;
  final DateTime createdAt;

  String get displayLabel => '$internalCode - $name';

  EpiMaterial copyWith({
    int? id,
    String? internalCode,
    String? name,
    EpiRequestType? requestType,
    DateTime? createdAt,
  }) {
    return EpiMaterial(
      id: id ?? this.id,
      internalCode: internalCode ?? this.internalCode,
      name: name ?? this.name,
      requestType: requestType ?? this.requestType,
      createdAt: createdAt ?? this.createdAt,
    );
  }

  Map<String, dynamic> toMap() {
    return {
      if (id != null) 'id': id,
      'internal_code': internalCode,
      'name': name,
      'request_type': requestType.code,
      'created_at': createdAt.toIso8601String(),
    };
  }

  factory EpiMaterial.fromMap(Map<String, dynamic> map) {
    return EpiMaterial(
      id: map['id'] as int?,
      internalCode: map['internal_code'] as String? ?? '',
      name: map['name'] as String? ?? '',
      requestType: EpiRequestType.fromCode(map['request_type'] as String? ?? 'epi'),
      createdAt: DateTime.tryParse(map['created_at'] as String? ?? '') ?? DateTime.now(),
    );
  }
}

enum EpiRequestStatus {
  requested('Solicitado'),
  approved('Liberado'),
  denied('Negado');

  const EpiRequestStatus(this.label);
  final String label;

  String get code => name;

  static EpiRequestStatus fromCode(String code) {
    return EpiRequestStatus.values.firstWhere(
      (status) => status.code == code,
      orElse: () => EpiRequestStatus.requested,
    );
  }
}

class EpiRequest {
  const EpiRequest({
    this.id,
    required this.collaboratorId,
    required this.requestedByCollaboratorId,
    this.releasedByCollaboratorId,
    required this.requestType,
    required this.internalCode,
    required this.itemName,
    this.size = '',
    this.quantity = 1,
    this.reason = '',
    this.status = EpiRequestStatus.requested,
    required this.requestedAt,
    this.releasedAt,
    this.collaboratorName,
    this.requestedByName,
    this.releasedByName,
    required this.createdAt,
  });

  final int? id;
  final int collaboratorId;
  final int requestedByCollaboratorId;
  final int? releasedByCollaboratorId;
  final EpiRequestType requestType;
  final String internalCode;
  final String itemName;
  final String size;
  final int quantity;
  final String reason;
  final EpiRequestStatus status;
  final DateTime requestedAt;
  final DateTime? releasedAt;
  final String? collaboratorName;
  final String? requestedByName;
  final String? releasedByName;
  final DateTime createdAt;

  bool get isPending => status == EpiRequestStatus.requested;

  EpiRequest copyWith({
    int? id,
    int? collaboratorId,
    int? requestedByCollaboratorId,
    int? releasedByCollaboratorId,
    bool clearReleasedBy = false,
    EpiRequestType? requestType,
    String? internalCode,
    String? itemName,
    String? size,
    int? quantity,
    String? reason,
    EpiRequestStatus? status,
    DateTime? requestedAt,
    DateTime? releasedAt,
    bool clearReleasedAt = false,
    String? collaboratorName,
    String? requestedByName,
    String? releasedByName,
    DateTime? createdAt,
  }) {
    return EpiRequest(
      id: id ?? this.id,
      collaboratorId: collaboratorId ?? this.collaboratorId,
      requestedByCollaboratorId: requestedByCollaboratorId ?? this.requestedByCollaboratorId,
      releasedByCollaboratorId: clearReleasedBy ? null : (releasedByCollaboratorId ?? this.releasedByCollaboratorId),
      requestType: requestType ?? this.requestType,
      internalCode: internalCode ?? this.internalCode,
      itemName: itemName ?? this.itemName,
      size: size ?? this.size,
      quantity: quantity ?? this.quantity,
      reason: reason ?? this.reason,
      status: status ?? this.status,
      requestedAt: requestedAt ?? this.requestedAt,
      releasedAt: clearReleasedAt ? null : (releasedAt ?? this.releasedAt),
      collaboratorName: collaboratorName ?? this.collaboratorName,
      requestedByName: requestedByName ?? this.requestedByName,
      releasedByName: releasedByName ?? this.releasedByName,
      createdAt: createdAt ?? this.createdAt,
    );
  }

  Map<String, dynamic> toMap() {
    return {
      if (id != null) 'id': id,
      'collaborator_id': collaboratorId,
      'requested_by_collaborator_id': requestedByCollaboratorId,
      'released_by_collaborator_id': releasedByCollaboratorId,
      'request_type': requestType.code,
      'internal_code': internalCode,
      'item_name': itemName,
      'size': size,
      'quantity': quantity,
      'reason': reason,
      'status': status.code,
      'requested_at': requestedAt.toIso8601String(),
      'released_at': releasedAt?.toIso8601String(),
      'created_at': createdAt.toIso8601String(),
    };
  }

  factory EpiRequest.fromMap(Map<String, dynamic> map) {
    return EpiRequest(
      id: map['id'] as int?,
      collaboratorId: map['collaborator_id'] as int? ?? 0,
      requestedByCollaboratorId: map['requested_by_collaborator_id'] as int? ?? 0,
      releasedByCollaboratorId: map['released_by_collaborator_id'] as int?,
      requestType: EpiRequestType.fromCode(map['request_type'] as String? ?? 'epi'),
      internalCode: map['internal_code'] as String? ?? '',
      itemName: map['item_name'] as String? ?? '',
      size: map['size'] as String? ?? '',
      quantity: map['quantity'] as int? ?? 1,
      reason: map['reason'] as String? ?? '',
      status: EpiRequestStatus.fromCode(map['status'] as String? ?? 'requested'),
      requestedAt: DateTime.tryParse(map['requested_at'] as String? ?? '') ?? DateTime.now(),
      releasedAt: DateTime.tryParse(map['released_at'] as String? ?? ''),
      collaboratorName: map['collaborator_name'] as String?,
      requestedByName: map['requested_by_name'] as String?,
      releasedByName: map['released_by_name'] as String?,
      createdAt: DateTime.tryParse(map['created_at'] as String? ?? '') ?? DateTime.now(),
    );
  }
}
