import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import 'package:share_plus/share_plus.dart';

import '../../core/utils/formatters.dart';
import '../../domain/entities/models.dart';
import '../providers/providers.dart';

class WorkProgressScreen extends ConsumerStatefulWidget {
  const WorkProgressScreen({super.key, required this.project});

  final Project project;

  @override
  ConsumerState<WorkProgressScreen> createState() =>
      _WorkProgressScreenState();
}

class _WorkProgressScreenState extends ConsumerState<WorkProgressScreen> {
  @override
  Widget build(BuildContext context) {
    final tasksAsync = ref.watch(projectTasksProvider(widget.project.id!));

    return Scaffold(
      appBar: AppBar(
        title: Text(widget.project.name),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _addTask,
        icon: const Icon(Icons.add_task),
        label: const Text('Nova tarefa'),
      ),
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment.topCenter,
            radius: 1.15,
            colors: [Color(0xFF1A2166), Color(0xFF0B1026)],
          ),
        ),
        child: tasksAsync.when(
          data: (tasks) {
            final completedCount = tasks.where((t) => t.isCompleted).length;
            final pausedCount = tasks.where((t) => t.status == TaskStatus.paused).length;
            final progress =
                tasks.isEmpty ? 0.0 : completedCount / tasks.length;

            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                // Header card
                Container(
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    gradient: const LinearGradient(
                      colors: [Color(0xFF00C853), Color(0xFF2979FF)],
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                    ),
                    borderRadius: BorderRadius.circular(22),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        widget.project.clientName,
                        style: Theme.of(context)
                            .textTheme
                            .titleLarge
                            ?.copyWith(color: Colors.white),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'ID ${widget.project.displayCode}',
                        style: const TextStyle(color: Colors.white70),
                      ),
                      if (widget.project.clientAddress.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text(
                          widget.project.clientAddress,
                          style: const TextStyle(color: Colors.white70),
                        ),
                      ],
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          _Pill(label: 'Tarefas: ${tasks.length}'),
                          _Pill(label: 'Concluídas: $completedCount'),
                          _Pill(label: 'Pausadas: $pausedCount'),
                        ],
                      ),
                      const SizedBox(height: 12),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(999),
                        child: LinearProgressIndicator(
                          minHeight: 10,
                          value: progress,
                          backgroundColor: Colors.white24,
                          valueColor:
                              const AlwaysStoppedAnimation<Color>(Colors.white),
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        'Evolução: ${(progress * 100).toStringAsFixed(0)}%',
                        style: const TextStyle(color: Colors.white),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 16),

                // Report button
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: tasks.isEmpty ? null : () => _shareReport(tasks),
                    icon: const Icon(Icons.picture_as_pdf_outlined),
                    label: const Text('Gerar e compartilhar relatório'),
                  ),
                ),
                const SizedBox(height: 16),

                // Task list
                if (tasks.isEmpty)
                  const Card(
                    child: Padding(
                      padding: EdgeInsets.all(20),
                      child: Text(
                        'Nenhuma tarefa cadastrada.\nToque em "Nova tarefa" para começar.',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  )
                else
                  ...tasks.map(
                    (task) => _WorkTaskCard(
                      task: task,
                      onEdit: () => _editTask(task),
                      onDelete: () => _confirmDeleteTask(task),
                    ),
                  ),
              ],
            );
          },
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (error, _) =>
              Center(child: Text('Erro ao carregar tarefas: $error')),
        ),
      ),
    );
  }

  Future<void> _addTask() async {
    final titleController = TextEditingController();
    final roomController = TextEditingController();
    final descriptionController = TextEditingController();

    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Nova tarefa'),
        content: SizedBox(
          width: 480,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: titleController,
                  decoration: const InputDecoration(
                    labelText: 'Nome da tarefa *',
                    hintText: 'Ex: Pintura teto',
                    prefixIcon: Icon(Icons.task_outlined),
                  ),
                  textCapitalization: TextCapitalization.sentences,
                  autofocus: true,
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: roomController,
                  decoration: const InputDecoration(
                    labelText: 'Ambiente / cômodo',
                    hintText: 'Ex: Sala, Quarto 1',
                    prefixIcon: Icon(Icons.room_outlined),
                  ),
                  textCapitalization: TextCapitalization.sentences,
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: descriptionController,
                  decoration: const InputDecoration(
                    labelText: 'Descrição (opcional)',
                    prefixIcon: Icon(Icons.description_outlined),
                  ),
                  maxLines: 2,
                  textCapitalization: TextCapitalization.sentences,
                ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () {
              if (titleController.text.trim().isEmpty) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Informe o nome da tarefa.')),
                );
                return;
              }
              Navigator.pop(ctx, true);
            },
            child: const Text('Adicionar'),
          ),
        ],
      ),
    );

    if (saved != true) {
      titleController.dispose();
      roomController.dispose();
      descriptionController.dispose();
      return;
    }

    final task = WorkTask(
      projectId: widget.project.id!,
      title: titleController.text.trim(),
      room: roomController.text.trim(),
      description: descriptionController.text.trim(),
    );

    titleController.dispose();
    roomController.dispose();
    descriptionController.dispose();

    await ref.read(workTaskRepositoryProvider).saveTask(task);
    ref.invalidate(projectTasksProvider(widget.project.id!));

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Tarefa adicionada.')),
      );
    }
  }

  Future<void> _editTask(WorkTask task) async {
    final noteController = TextEditingController(text: task.note ?? '');
    final materialController =
        TextEditingController(text: task.materialIssue ?? '');
    String? photoPath = task.photoPath;
    TaskStatus selectedStatus = task.status;
    DateTime? plannedStartDate = task.plannedStartDate;
    DateTime? plannedEndDate = task.plannedEndDate;

    final savedTask = await showDialog<WorkTask>(
      context: context,
      builder: (dialogContext) {
        return StatefulBuilder(
          builder: (context, setStateDialog) {
            return AlertDialog(
              title: Text('Atualizar: ${task.title}'),
              content: SizedBox(
                width: 520,
                child: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (task.room.isNotEmpty)
                        Text('Ambiente: ${task.room}'),
                      const SizedBox(height: 12),
                      DropdownButtonFormField<TaskStatus>(
                        value: selectedStatus,
                        decoration: const InputDecoration(
                          labelText: 'Status',
                          prefixIcon: Icon(Icons.flag_outlined),
                        ),
                        items: TaskStatus.values.map((s) {
                          return DropdownMenuItem(
                            value: s,
                            child: Text(s.label),
                          );
                        }).toList(),
                        onChanged: (value) {
                          if (value != null) {
                            setStateDialog(() {
                              selectedStatus = value;
                              if (value != TaskStatus.planned) {
                                plannedStartDate = null;
                                plannedEndDate = null;
                              }
                            });
                          }
                        },
                      ),
                      if (selectedStatus == TaskStatus.planned) ...[
                        const SizedBox(height: 12),
                        _DatePickerField(
                          label: 'Data inicial de execução',
                          value: plannedStartDate,
                          onPicked: (date) {
                            setStateDialog(() => plannedStartDate = date);
                          },
                        ),
                        const SizedBox(height: 12),
                        _DatePickerField(
                          label: 'Data final de execução',
                          value: plannedEndDate,
                          firstDate: plannedStartDate,
                          onPicked: (date) {
                            setStateDialog(() => plannedEndDate = date);
                          },
                        ),
                      ],
                      const SizedBox(height: 12),
                      TextField(
                        controller: noteController,
                        maxLines: 3,
                        decoration: const InputDecoration(
                          labelText: 'Observação da execução',
                          prefixIcon: Icon(Icons.notes_outlined),
                        ),
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: materialController,
                        maxLines: 2,
                        decoration: const InputDecoration(
                          labelText: 'Falta de material / impedimento',
                          prefixIcon: Icon(Icons.report_problem_outlined),
                        ),
                      ),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          FilledButton.tonalIcon(
                            onPressed: () async {
                              final picked = await _pickPhoto();
                              if (picked != null) {
                                setStateDialog(() => photoPath = picked);
                              }
                            },
                            icon: const Icon(Icons.add_a_photo_outlined),
                            label: Text(
                              photoPath == null || photoPath!.isEmpty
                                  ? 'Anexar foto'
                                  : 'Trocar foto',
                            ),
                          ),
                          if (photoPath != null && photoPath!.isNotEmpty)
                            TextButton.icon(
                              onPressed: () =>
                                  setStateDialog(() => photoPath = null),
                              icon: const Icon(Icons.delete_outline),
                              label: const Text('Remover foto'),
                            ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      _buildPhotoPreview(photoPath),
                    ],
                  ),
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(dialogContext),
                  child: const Text('Cancelar'),
                ),
                FilledButton(
                  onPressed: () {
                    final updated = task.copyWith(
                      status: selectedStatus,
                      note: noteController.text.trim(),
                      clearNote: noteController.text.trim().isEmpty,
                      materialIssue: materialController.text.trim(),
                      clearMaterialIssue: materialController.text.trim().isEmpty,
                      photoPath: photoPath,
                      clearPhoto: (photoPath ?? '').isEmpty,
                      updatedAt: DateTime.now(),
                      plannedStartDate: plannedStartDate,
                      clearPlannedStartDate: plannedStartDate == null,
                      plannedEndDate: plannedEndDate,
                      clearPlannedEndDate: plannedEndDate == null,
                    );
                    Navigator.pop(dialogContext, updated);
                  },
                  child: const Text('Salvar'),
                ),
              ],
            );
          },
        );
      },
    );

    noteController.dispose();
    materialController.dispose();

    if (savedTask == null) return;

    await ref.read(workTaskRepositoryProvider).saveTask(savedTask);
    ref.invalidate(projectTasksProvider(widget.project.id!));

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Tarefa atualizada: ${savedTask.status.label}.')),
      );
    }
  }

  Future<void> _confirmDeleteTask(WorkTask task) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Excluir tarefa?'),
        content: Text('A tarefa "${task.title}" será excluída permanentemente.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Excluir'),
          ),
        ],
      ),
    );

    if (confirmed == true && task.id != null) {
      await ref.read(workTaskRepositoryProvider).deleteTask(task.id!);
      ref.invalidate(projectTasksProvider(widget.project.id!));
    }
  }

  Future<void> _shareReport(List<WorkTask> tasks) async {
    try {
      final pdfFile = await ref.read(pdfReportServiceProvider).generateReportPdf(
            project: widget.project,
            tasks: tasks,
          );

      await SharePlus.instance.share(
        ShareParams(
          files: [pdfFile],
          text: 'Relatório de evolução da obra ${widget.project.displayCode}.',
          title: 'Evolução da obra ${widget.project.displayCode}',
        ),
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Relatório pronto para compartilhar.')),
        );
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Erro ao gerar relatório: $error')),
        );
      }
    }
  }

  Future<String?> _pickPhoto() async {
    final picker = ImagePicker();
    final file = await picker.pickImage(
      source: ImageSource.gallery,
      imageQuality: 80,
    );
    if (file == null) return null;

    final bytes = await file.readAsBytes();
    final mimeType = _resolveMimeType(file.name.isNotEmpty ? file.name : file.path);
    return 'data:$mimeType;base64,${base64Encode(bytes)}';
  }

  Widget _buildPhotoPreview(String? photoPath) {
    if (photoPath == null || photoPath.isEmpty) {
      return const Text('Nenhuma foto anexada ainda.');
    }

    final bytes = _decodeDataUrl(photoPath);
    if (bytes != null) {
      return ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: Image.memory(
          bytes,
          height: 140,
          width: double.infinity,
          fit: BoxFit.cover,
        ),
      );
    }

    if (kIsWeb) {
      return Image.network(
        photoPath,
        height: 140,
        width: double.infinity,
        fit: BoxFit.cover,
        errorBuilder: (ctx, err, st) =>
            const Text('Não foi possível carregar a foto.'),
      );
    }

    final file = File(photoPath);
    if (file.existsSync()) {
      return Image.file(file, height: 140, width: double.infinity, fit: BoxFit.cover);
    }

    return const Text('Não foi possível carregar a foto.');
  }

  String _resolveMimeType(String fileName) {
    final lower = fileName.toLowerCase();
    if (lower.endsWith('.png')) return 'image/png';
    if (lower.endsWith('.webp')) return 'image/webp';
    return 'image/jpeg';
  }

  Uint8List? _decodeDataUrl(String value) {
    if (!value.startsWith('data:')) return null;
    final marker = ';base64,';
    final markerIndex = value.indexOf(marker);
    if (markerIndex == -1) return null;
    try {
      return base64Decode(value.substring(markerIndex + marker.length));
    } catch (_) {
      return null;
    }
  }
}

// ----- Reusable widgets -----

class _WorkTaskCard extends StatelessWidget {
  const _WorkTaskCard({
    required this.task,
    required this.onEdit,
    required this.onDelete,
  });

  final WorkTask task;
  final VoidCallback onEdit;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        task.title,
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      if (task.room.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text('Ambiente: ${task.room}'),
                      ],
                    ],
                  ),
                ),
                _TaskStatusChip(status: task.status),
              ],
            ),
            if (task.description.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(task.description),
            ],
            if ((task.note ?? '').isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('Observação: ${task.note}'),
            ],
            if ((task.materialIssue ?? '').isNotEmpty) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: const Color(0xFFFFF3E0),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  'Impedimento: ${task.materialIssue}',
                  style: const TextStyle(
                    color: Color(0xFFEF6C00),
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
            if (task.plannedStartDate != null || task.plannedEndDate != null) ...[
              const SizedBox(height: 8),
              Text(
                'Período: ${task.plannedStartDate != null ? formatDate(task.plannedStartDate!) : '?'} – ${task.plannedEndDate != null ? formatDate(task.plannedEndDate!) : '?'}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            if (task.hasPhoto) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(12),
                  color: const Color(0x0F00E676),
                  border: Border.all(color: const Color(0x3300E676)),
                ),
                child: const Text(
                  'Foto anexada à execução.',
                  style: TextStyle(
                    color: Color(0xFF00E676),
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
            if (task.updatedAt != null) ...[
              const SizedBox(height: 8),
              Text(
                'Última atualização: ${formatDateTime(task.updatedAt!)}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: FilledButton.tonalIcon(
                    onPressed: onEdit,
                    icon: const Icon(Icons.edit_outlined),
                    label: const Text('Editar tarefa'),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton(
                  onPressed: onDelete,
                  icon: const Icon(Icons.delete_outline),
                  tooltip: 'Excluir tarefa',
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _TaskStatusChip extends StatelessWidget {
  const _TaskStatusChip({required this.status});

  final TaskStatus status;

  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      TaskStatus.notPlanned => const Color(0xFF78909C),
      TaskStatus.planned => const Color(0xFF5C6BC0),
      TaskStatus.inProgress => const Color(0xFF00ACC1),
      TaskStatus.paused => const Color(0xFFEF6C00),
      TaskStatus.completed => const Color(0xFF2E7D32),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        status.label,
        style: TextStyle(
          color: color,
          fontWeight: FontWeight.w700,
          fontSize: 12,
        ),
      ),
    );
  }
}

class _Pill extends StatelessWidget {
  const _Pill({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style:
            const TextStyle(color: Colors.white, fontWeight: FontWeight.w600),
      ),
    );
  }
}

class _DatePickerField extends StatelessWidget {
  const _DatePickerField({
    required this.label,
    required this.value,
    required this.onPicked,
    this.firstDate,
  });

  final String label;
  final DateTime? value;
  final ValueChanged<DateTime?> onPicked;
  final DateTime? firstDate;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () async {
        final now = DateTime.now();
        final picked = await showDatePicker(
          context: context,
          initialDate: value ?? now,
          firstDate: firstDate ?? DateTime(now.year - 1),
          lastDate: DateTime(now.year + 5),
        );
        onPicked(picked);
      },
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          prefixIcon: const Icon(Icons.calendar_today_outlined),
          suffixIcon: value != null
              ? IconButton(
                  icon: const Icon(Icons.clear),
                  onPressed: () => onPicked(null),
                )
              : null,
        ),
        child: Text(
          value != null ? formatDate(value!) : 'Selecionar data',
          style: TextStyle(
            color: value != null ? null : Theme.of(context).hintColor,
          ),
        ),
      ),
    );
  }
}
