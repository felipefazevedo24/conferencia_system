import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../core/utils/formatters.dart';
import '../../domain/entities/models.dart';
import '../providers/providers.dart';


class CleaningScheduleScreen extends ConsumerStatefulWidget {
  const CleaningScheduleScreen({super.key});

  @override
  ConsumerState<CleaningScheduleScreen> createState() =>
      _CleaningScheduleScreenState();
}

class _CleaningScheduleScreenState
    extends ConsumerState<CleaningScheduleScreen> {
  // null = "Todos"
  int? _filterCollaboratorId;
  DateTime? _filterDate;

  @override
  Widget build(BuildContext context) {
    final schedulesAsync = ref.watch(cleaningSchedulesProvider);
    final collaboratorsAsync = ref.watch(collaboratorsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Cronograma de Limpeza'),
      ),
      floatingActionButton: FloatingActionButton.extended(
              onPressed: () => _showScheduleEditor(context, ref),
              icon: const Icon(Icons.add),
              label: const Text('Novo cronograma'),
            ),
      body: Container(
              decoration: const BoxDecoration(
                gradient: RadialGradient(
                  center: Alignment.topCenter,
                  radius: 1.15,
                  colors: [Color(0xFF1A2166), Color(0xFF0B1026)],
                ),
              ),
              child: Column(
                children: [
                  // ── Filtro por colaborador ──────────────────────────────
                  collaboratorsAsync.when(
                    data: (collaborators) {
                      if (collaborators.isEmpty) return const SizedBox.shrink();
                      return Column(
                        children: [
                          _CollaboratorFilterBar(
                            collaborators: collaborators,
                            selectedId: _filterCollaboratorId,
                            onSelected: (id) =>
                                setState(() => _filterCollaboratorId = id),
                          ),
                          _DateFilterBar(
                            selectedDate: _filterDate,
                            onPickDate: () async {
                              final now = DateTime.now();
                              final picked = await showDatePicker(
                                context: context,
                                initialDate: _filterDate ?? now,
                                firstDate: DateTime(now.year - 1),
                                lastDate: DateTime(now.year + 5),
                              );
                              if (picked != null) {
                                setState(() {
                                  _filterDate = DateTime(
                                    picked.year,
                                    picked.month,
                                    picked.day,
                                  );
                                });
                              }
                            },
                            onClearDate: _filterDate == null
                                ? null
                                : () => setState(() => _filterDate = null),
                          ),
                        ],
                      );
                    },
                    loading: () => const SizedBox.shrink(),
                    error: (_, __) => const SizedBox.shrink(),
                  ),
                  // ── Agenda ──────────────────────────────────────────────
                  Expanded(
                    child: schedulesAsync.when(
                      data: (list) {
                        final filtered = list.where((s) {
                          final byCollaborator = _filterCollaboratorId == null ||
                              s.collaboratorId == _filterCollaboratorId;
                          final byDate = _filterDate == null ||
                              _isSameDay(s.scheduledDate, _filterDate!);
                          return byCollaborator && byDate;
                        }).toList();

                        if (filtered.isEmpty) {
                          return Center(
                            child: Padding(
                              padding: const EdgeInsets.all(32),
                              child: Text(
                                list.isEmpty
                                    ? 'Nenhum cronograma cadastrado.\nToque no botão abaixo para criar.'
                                    : 'Nenhuma atividade encontrada para os filtros selecionados.',
                                textAlign: TextAlign.center,
                                style: const TextStyle(fontSize: 16),
                              ),
                            ),
                          );
                        }

                        return _AgendaView(
                          schedules: filtered,
                          onToggle: (s) async {
                            await ref
                                .read(cleaningScheduleRepositoryProvider)
                                .toggleDone(s.id!, !s.done);
                            ref.invalidate(cleaningSchedulesProvider);
                          },
                          onEdit: (s) =>
                              _showScheduleEditor(context, ref, schedule: s),
                          onDelete: (s) => _confirmDelete(context, ref, s),
                        );
                      },
                      loading: () =>
                          const Center(child: CircularProgressIndicator()),
                      error: (e, _) => Center(child: Text('Erro: $e')),
                    ),
                  ),
                ],
              ),
            ),
    );
  }

  Future<void> _showScheduleEditor(
    BuildContext context,
    WidgetRef ref, {
    CleaningSchedule? schedule,
  }) async {
    // Ensure collaborators are loaded
    final collaborators =
        await ref.read(collaboratorRepositoryProvider).getCollaborators();

    if (collaborators.isEmpty) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content:
                Text('Cadastre ao menos um colaborador antes de criar um cronograma.'),
          ),
        );
      }
      return;
    }

    final isEditing = schedule != null;
    final titleCtrl = TextEditingController(text: schedule?.title ?? '');
    final locationCtrl = TextEditingController(text: schedule?.location ?? '');
    final notesCtrl = TextEditingController(text: schedule?.notes ?? '');

    int? selectedCollaboratorId =
        schedule?.collaboratorId ?? collaborators.first.id;
    DateTime selectedDate = schedule?.scheduledDate ?? DateTime.now();
    TimeOfDay startTime = _parseTime(schedule?.startTime) ??
        const TimeOfDay(hour: 8, minute: 0);
    TimeOfDay endTime =
        _parseTime(schedule?.endTime) ?? const TimeOfDay(hour: 9, minute: 0);
    CleaningRecurrenceFrequency selectedRecurrence =
        CleaningRecurrenceFrequency.none;
    DateTime recurrenceEndDate = selectedDate.add(const Duration(days: 6));
    Set<int> selectedWeekdays = {selectedDate.weekday};

    if (!context.mounted) return;

    final saved = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        return StatefulBuilder(
          builder: (context, setStateDialog) {
            return AlertDialog(
              title: Text(isEditing ? 'Editar cronograma' : 'Novo cronograma'),
              content: SizedBox(
                width: 520,
                child: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      DropdownButtonFormField<int>(
                        value: selectedCollaboratorId,
                        decoration: const InputDecoration(
                          labelText: 'Colaborador *',
                          prefixIcon: Icon(Icons.person_outline),
                        ),
                        items: collaborators.map((c) {
                          return DropdownMenuItem(
                            value: c.id,
                            child: Text(c.name),
                          );
                        }).toList(),
                        onChanged: (v) =>
                            setStateDialog(() => selectedCollaboratorId = v),
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: titleCtrl,
                        decoration: const InputDecoration(
                          labelText: 'Título da atividade *',
                          hintText: 'Ex: Limpeza geral 3º andar',
                          prefixIcon: Icon(Icons.cleaning_services_outlined),
                        ),
                        textCapitalization: TextCapitalization.sentences,
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: locationCtrl,
                        decoration: const InputDecoration(
                          labelText: 'Local / setor',
                          hintText: 'Ex: Bloco A, andar 3',
                          prefixIcon: Icon(Icons.location_on_outlined),
                        ),
                        textCapitalization: TextCapitalization.sentences,
                      ),
                      const SizedBox(height: 12),
                      InkWell(
                        onTap: () async {
                          final picked = await showDatePicker(
                            context: context,
                            initialDate: selectedDate,
                            firstDate: DateTime(DateTime.now().year - 1),
                            lastDate: DateTime(DateTime.now().year + 5),
                          );
                          if (picked != null) {
                            setStateDialog(() {
                              selectedDate = picked;
                              if (recurrenceEndDate.isBefore(selectedDate)) {
                                recurrenceEndDate = selectedDate;
                              }
                            });
                          }
                        },
                        child: InputDecorator(
                          decoration: const InputDecoration(
                            labelText: 'Data *',
                            prefixIcon: Icon(Icons.calendar_today_outlined),
                          ),
                          child: Text(formatDate(selectedDate)),
                        ),
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: InkWell(
                              onTap: () async {
                                final picked = await showTimePicker(
                                  context: context,
                                  initialTime: startTime,
                                );
                                if (picked != null) {
                                  setStateDialog(() => startTime = picked);
                                }
                              },
                              child: InputDecorator(
                                decoration: const InputDecoration(
                                  labelText: 'Hora início *',
                                  prefixIcon: Icon(Icons.access_time),
                                ),
                                child: Text(startTime.format(context)),
                              ),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: InkWell(
                              onTap: () async {
                                final picked = await showTimePicker(
                                  context: context,
                                  initialTime: endTime,
                                );
                                if (picked != null) {
                                  setStateDialog(() => endTime = picked);
                                }
                              },
                              child: InputDecorator(
                                decoration: const InputDecoration(
                                  labelText: 'Hora fim *',
                                  prefixIcon: Icon(Icons.access_time),
                                ),
                                child: Text(endTime.format(context)),
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: notesCtrl,
                        decoration: const InputDecoration(
                          labelText: 'Observações',
                          prefixIcon: Icon(Icons.notes_outlined),
                        ),
                        maxLines: 2,
                        textCapitalization: TextCapitalization.sentences,
                      ),
                      if (!isEditing) ...[
                        const SizedBox(height: 12),
                        DropdownButtonFormField<CleaningRecurrenceFrequency>(
                          value: selectedRecurrence,
                          decoration: const InputDecoration(
                            labelText: 'Recorrência',
                            prefixIcon: Icon(Icons.repeat),
                          ),
                          items: CleaningRecurrenceFrequency.values.map((item) {
                            return DropdownMenuItem(
                              value: item,
                              child: Text(item.label),
                            );
                          }).toList(),
                          onChanged: (value) {
                            if (value != null) {
                              setStateDialog(() => selectedRecurrence = value);
                            }
                          },
                        ),
                        if (selectedRecurrence != CleaningRecurrenceFrequency.none) ...[
                          const SizedBox(height: 8),
                          Align(
                            alignment: Alignment.centerLeft,
                            child: Text(
                              'Gerar automaticamente várias atividades com a mesma ação.',
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                          if (selectedRecurrence ==
                              CleaningRecurrenceFrequency.weekdays) ...[
                            const SizedBox(height: 12),
                            Align(
                              alignment: Alignment.centerLeft,
                              child: Text(
                                'Selecione os dias da semana',
                                style: Theme.of(context).textTheme.bodyMedium,
                              ),
                            ),
                            const SizedBox(height: 8),
                            Wrap(
                              spacing: 8,
                              runSpacing: 8,
                              children: List.generate(7, (index) {
                                final weekday = index + 1;
                                final selected =
                                    selectedWeekdays.contains(weekday);

                                return FilterChip(
                                  label: Text(_weekdayLabel(weekday)),
                                  selected: selected,
                                  onSelected: (value) {
                                    setStateDialog(() {
                                      if (value) {
                                        selectedWeekdays.add(weekday);
                                      } else {
                                        selectedWeekdays.remove(weekday);
                                      }
                                    });
                                  },
                                );
                              }),
                            ),
                          ],
                          const SizedBox(height: 12),
                          InkWell(
                            onTap: () async {
                              final picked = await showDatePicker(
                                context: context,
                                initialDate: recurrenceEndDate.isBefore(selectedDate)
                                    ? selectedDate
                                    : recurrenceEndDate,
                                firstDate: selectedDate,
                                lastDate: DateTime(DateTime.now().year + 5),
                              );
                              if (picked != null) {
                                setStateDialog(() => recurrenceEndDate = picked);
                              }
                            },
                            child: InputDecorator(
                              decoration: const InputDecoration(
                                labelText: 'Período da recorrência até',
                                prefixIcon: Icon(Icons.event_repeat),
                              ),
                              child: Text(formatDate(recurrenceEndDate)),
                            ),
                          ),
                          const SizedBox(height: 8),
                          Align(
                            alignment: Alignment.centerLeft,
                            child: Text(
                              'Prévia: ${generateRecurringCleaningSchedules(
                                baseSchedule: CleaningSchedule(
                                  collaboratorId: selectedCollaboratorId ?? 0,
                                  title: titleCtrl.text.trim(),
                                  location: locationCtrl.text.trim(),
                                  scheduledDate: selectedDate,
                                  startTime: _formatTime(startTime),
                                  endTime: _formatTime(endTime),
                                  notes: notesCtrl.text.trim(),
                                  createdAt: DateTime.now(),
                                ),
                                recurrence: selectedRecurrence,
                                recurrenceEndDate: recurrenceEndDate,
                                selectedWeekdays: selectedWeekdays,
                              ).length} atividade(s) serão criadas.',
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                        ],
                      ],
                    ],
                  ),
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(dialogContext, false),
                  child: const Text('Cancelar'),
                ),
                FilledButton(
                  onPressed: () {
                    if (titleCtrl.text.trim().isEmpty ||
                        selectedCollaboratorId == null) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(
                          content: Text('Preencha o colaborador e o título.'),
                        ),
                      );
                      return;
                    }
                    if (!isEditing &&
                        selectedRecurrence !=
                            CleaningRecurrenceFrequency.none &&
                        recurrenceEndDate.isBefore(selectedDate)) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(
                          content: Text(
                            'O período da recorrência deve ser igual ou maior que a data inicial.',
                          ),
                        ),
                      );
                      return;
                    }
                    if (!isEditing &&
                        selectedRecurrence ==
                            CleaningRecurrenceFrequency.weekdays &&
                        selectedWeekdays.isEmpty) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(
                          content: Text(
                            'Selecione ao menos um dia da semana para a recorrência.',
                          ),
                        ),
                      );
                      return;
                    }
                    Navigator.pop(dialogContext, true);
                  },
                  child: Text(isEditing ? 'Salvar' : 'Criar'),
                ),
              ],
            );
          },
        );
      },
    );

    if (saved != true) {
      titleCtrl.dispose();
      locationCtrl.dispose();
      notesCtrl.dispose();
      return;
    }

    final entity = (schedule ??
            CleaningSchedule(
              collaboratorId: 0,
              title: '',
              scheduledDate: DateTime.now(),
              startTime: '',
              endTime: '',
              createdAt: DateTime.now(),
            ))
        .copyWith(
      collaboratorId: selectedCollaboratorId,
      title: titleCtrl.text.trim(),
      location: locationCtrl.text.trim(),
      scheduledDate: selectedDate,
      startTime: _formatTime(startTime),
      endTime: _formatTime(endTime),
      notes: notesCtrl.text.trim(),
    );

    titleCtrl.dispose();
    locationCtrl.dispose();
    notesCtrl.dispose();

    final repository = ref.read(cleaningScheduleRepositoryProvider);

    if (!isEditing &&
        selectedRecurrence != CleaningRecurrenceFrequency.none) {
      final schedules = generateRecurringCleaningSchedules(
        baseSchedule: entity,
        recurrence: selectedRecurrence,
        recurrenceEndDate: recurrenceEndDate,
        selectedWeekdays: selectedWeekdays,
      );
      await repository.saveSchedules(schedules);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('${schedules.length} atividade(s) criadas em lote.'),
          ),
        );
      }
    } else {
      await repository.saveSchedule(entity);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(isEditing
                ? 'Cronograma atualizado.'
                : 'Cronograma criado.'),
          ),
        );
      }
    }

    ref.invalidate(cleaningSchedulesProvider);
  }

  Future<void> _confirmDelete(
    BuildContext context,
    WidgetRef ref,
    CleaningSchedule schedule,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Excluir cronograma?'),
        content: Text('"${schedule.title}" será excluído permanentemente.'),
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

    if (confirmed == true && schedule.id != null) {
      await ref
          .read(cleaningScheduleRepositoryProvider)
          .deleteSchedule(schedule.id!);
      ref.invalidate(cleaningSchedulesProvider);
    }
  }

  static String _formatTime(TimeOfDay t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

  static TimeOfDay? _parseTime(String? s) {
    if (s == null || s.isEmpty) return null;
    final parts = s.split(':');
    if (parts.length < 2) return null;
    return TimeOfDay(
      hour: int.tryParse(parts[0]) ?? 0,
      minute: int.tryParse(parts[1]) ?? 0,
    );
  }

  static bool _isSameDay(DateTime a, DateTime b) {
    return a.year == b.year && a.month == b.month && a.day == b.day;
  }

  static String _weekdayLabel(int weekday) {
    switch (weekday) {
      case DateTime.monday:
        return 'Seg';
      case DateTime.tuesday:
        return 'Ter';
      case DateTime.wednesday:
        return 'Qua';
      case DateTime.thursday:
        return 'Qui';
      case DateTime.friday:
        return 'Sex';
      case DateTime.saturday:
        return 'Sáb';
      case DateTime.sunday:
        return 'Dom';
      default:
        return '-';
    }
  }
}

class _DateFilterBar extends StatelessWidget {
  const _DateFilterBar({
    required this.selectedDate,
    required this.onPickDate,
    required this.onClearDate,
  });

  final DateTime? selectedDate;
  final VoidCallback onPickDate;
  final VoidCallback? onClearDate;

  @override
  Widget build(BuildContext context) {
    final label = selectedDate == null
        ? 'Todas as datas'
        : 'Data: ${formatDate(selectedDate!)}';

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
      child: Row(
        children: [
          Expanded(
            child: OutlinedButton.icon(
              onPressed: onPickDate,
              icon: const Icon(Icons.calendar_month_outlined),
              label: Align(
                alignment: Alignment.centerLeft,
                child: Text(label),
              ),
            ),
          ),
          const SizedBox(width: 8),
          IconButton(
            onPressed: onClearDate,
            tooltip: 'Limpar data',
            icon: const Icon(Icons.filter_alt_off_outlined),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Collaborator Filter Bar
// ---------------------------------------------------------------------------

class _CollaboratorFilterBar extends StatelessWidget {
  const _CollaboratorFilterBar({
    required this.collaborators,
    required this.selectedId,
    required this.onSelected,
  });

  final List<Collaborator> collaborators;
  final int? selectedId;
  final ValueChanged<int?> onSelected;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 48,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        children: [
          _FilterChip(
            label: 'Todos',
            selected: selectedId == null,
            onTap: () => onSelected(null),
          ),
          const SizedBox(width: 8),
          ...collaborators.map((c) => Padding(
                padding: const EdgeInsets.only(right: 8),
                child: _FilterChip(
                  label: c.name,
                  selected: selectedId == c.id,
                  onTap: () => onSelected(c.id),
                ),
              )),
        ],
      ),
    );
  }
}

class _FilterChip extends StatelessWidget {
  const _FilterChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        decoration: BoxDecoration(
          color: selected
              ? colorScheme.primary
              : colorScheme.surface.withValues(alpha: 0.15),
          borderRadius: BorderRadius.circular(999),
          border: Border.all(
            color: selected
                ? colorScheme.primary
                : Colors.white24,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 13,
            fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
            color: selected ? colorScheme.onPrimary : Colors.white70,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Agenda View
// ---------------------------------------------------------------------------

class _AgendaView extends StatelessWidget {
  const _AgendaView({
    required this.schedules,
    required this.onToggle,
    required this.onEdit,
    required this.onDelete,
  });

  final List<CleaningSchedule> schedules;
  final ValueChanged<CleaningSchedule> onToggle;
  final ValueChanged<CleaningSchedule> onEdit;
  final ValueChanged<CleaningSchedule> onDelete;

  @override
  Widget build(BuildContext context) {
    // Group by date (day only)
    final Map<DateTime, List<CleaningSchedule>> grouped = {};
    for (final s in schedules) {
      final key = DateTime(
        s.scheduledDate.year,
        s.scheduledDate.month,
        s.scheduledDate.day,
      );
      grouped.putIfAbsent(key, () => []).add(s);
    }

    final sortedDays = grouped.keys.toList()..sort();

    final today = DateTime.now();
    final todayKey = DateTime(today.year, today.month, today.day);

    // Build flat list of items (day header + events)
    final items = <_AgendaItem>[];
    for (final day in sortedDays) {
      items.add(_AgendaItem.header(day));
      for (final s in grouped[day]!) {
        items.add(_AgendaItem.event(s));
      }
    }

    return ListView.builder(
      padding: const EdgeInsets.only(bottom: 96),
      itemCount: items.length,
      itemBuilder: (context, index) {
        final item = items[index];

        if (item.isHeader) {
          final day = item.day!;
          final isToday = day == todayKey;
          final isPast = day.isBefore(todayKey);
          final label = _dayLabel(day, isToday);

          return Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
            child: Row(
              children: [
                // Day number circle
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: isToday
                        ? Theme.of(context).colorScheme.primary
                        : Colors.transparent,
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    '${day.day}',
                    style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                      color: isToday
                          ? Theme.of(context).colorScheme.onPrimary
                          : isPast
                              ? Colors.white38
                              : Colors.white,
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    label,
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      color: isToday
                          ? Theme.of(context).colorScheme.primary
                          : isPast
                              ? Colors.white38
                              : Colors.white70,
                      letterSpacing: 0.5,
                    ),
                  ),
                ),
                // Divider line
                const Expanded(
                  flex: 2,
                  child: Divider(color: Colors.white12),
                ),
              ],
            ),
          );
        }

        // Event row
        final s = item.schedule!;
        final isPast = s.scheduledDate.isBefore(todayKey);

        return Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 6),
          child: IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // Time column
                SizedBox(
                  width: 52,
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.start,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      const SizedBox(height: 12),
                      Text(
                        s.startTime,
                        style: TextStyle(
                          fontSize: 12,
                          color: isPast ? Colors.white30 : Colors.white54,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      Text(
                        s.endTime,
                        style: TextStyle(
                          fontSize: 11,
                          color: isPast
                              ? Colors.white.withValues(alpha: 0.20)
                              : Colors.white38,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 10),
                // Colored accent bar
                Container(
                  width: 3,
                  margin: const EdgeInsets.symmetric(vertical: 8),
                  decoration: BoxDecoration(
                    color: s.done
                        ? const Color(0xFF43A047)
                        : isPast
                            ? Colors.white24
                            : const Color(0xFF5C6BC0),
                    borderRadius: BorderRadius.circular(999),
                  ),
                ),
                const SizedBox(width: 10),
                // Event card
                Expanded(
                  child: _AgendaEventCard(
                    schedule: s,
                    isPast: isPast,
                    onToggle: () => onToggle(s),
                    onEdit: () => onEdit(s),
                    onDelete: () => onDelete(s),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  static String _dayLabel(DateTime day, bool isToday) {
    if (isToday) return 'HOJE • ${_weekdayFull(day.weekday)}';
    final tomorrow = DateTime.now().add(const Duration(days: 1));
    if (day.year == tomorrow.year &&
        day.month == tomorrow.month &&
        day.day == tomorrow.day) {
      return 'AMANHÃ • ${_weekdayFull(day.weekday)}';
    }
    return '${_weekdayFull(day.weekday)} • ${DateFormat("MMMM 'de' yyyy", 'pt_BR').format(day)}';
  }

  static String _weekdayFull(int weekday) {
    const labels = {
      DateTime.monday: 'SEGUNDA',
      DateTime.tuesday: 'TERÇA',
      DateTime.wednesday: 'QUARTA',
      DateTime.thursday: 'QUINTA',
      DateTime.friday: 'SEXTA',
      DateTime.saturday: 'SÁBADO',
      DateTime.sunday: 'DOMINGO',
    };
    return labels[weekday] ?? '';
  }
}

class _AgendaItem {
  _AgendaItem.header(DateTime day)
      : isHeader = true,
        day = day,
        schedule = null;

  _AgendaItem.event(CleaningSchedule schedule)
      : isHeader = false,
        day = null,
        schedule = schedule;

  final bool isHeader;
  final DateTime? day;
  final CleaningSchedule? schedule;
}

// ---------------------------------------------------------------------------
// Agenda Event Card
// ---------------------------------------------------------------------------

class _AgendaEventCard extends StatelessWidget {
  const _AgendaEventCard({
    required this.schedule,
    required this.isPast,
    required this.onToggle,
    required this.onEdit,
    required this.onDelete,
  });

  final CleaningSchedule schedule;
  final bool isPast;
  final VoidCallback onToggle;
  final VoidCallback onEdit;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      color: schedule.done
          ? const Color(0xFF1B3A1C)
          : isPast
              ? const Color(0xFF1A1E30)
              : const Color(0xFF1E2547),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
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
                        schedule.title,
                        style: TextStyle(
                          fontWeight: FontWeight.w600,
                          fontSize: 14,
                          decoration: schedule.done
                              ? TextDecoration.lineThrough
                              : null,
                          color: schedule.done
                              ? Colors.white38
                              : isPast
                                  ? Colors.white54
                                  : Colors.white,
                        ),
                      ),
                      if (schedule.collaboratorName != null) ...[
                        const SizedBox(height: 2),
                        Row(
                          children: [
                            Icon(Icons.person_outline,
                                size: 12,
                                color: isPast ? Colors.white24 : Colors.white54),
                            const SizedBox(width: 4),
                            Text(
                              schedule.collaboratorName!,
                              style: TextStyle(
                                fontSize: 12,
                                color: isPast ? Colors.white24 : Colors.white54,
                              ),
                            ),
                          ],
                        ),
                      ],
                      if (schedule.location.isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Row(
                          children: [
                            Icon(Icons.location_on_outlined,
                                size: 12,
                                color: isPast ? Colors.white24 : Colors.white54),
                            const SizedBox(width: 4),
                            Text(
                              schedule.location,
                              style: TextStyle(
                                fontSize: 12,
                                color: isPast ? Colors.white24 : Colors.white54,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
                // Status badge + actions
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 3),
                      decoration: BoxDecoration(
                        color: schedule.done
                            ? const Color(0xFF43A047).withValues(alpha: 0.18)
                            : isPast
                                ? Colors.white12
                                : const Color(0xFF5C6BC0)
                                    .withValues(alpha: 0.18),
                        borderRadius: BorderRadius.circular(999),
                      ),
                      child: Text(
                        schedule.done
                            ? 'Concluído'
                            : isPast
                                ? 'Não realizado'
                                : 'Pendente',
                        style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w700,
                          color: schedule.done
                              ? const Color(0xFF43A047)
                              : isPast
                                  ? Colors.white38
                                  : const Color(0xFF7986CB),
                        ),
                      ),
                    ),
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        IconButton(
                          iconSize: 18,
                          visualDensity: VisualDensity.compact,
                          icon: Icon(
                            schedule.done
                                ? Icons.undo
                                : Icons.check_circle_outline,
                            color: schedule.done
                                ? Colors.white38
                                : const Color(0xFF43A047),
                          ),
                          tooltip: schedule.done
                              ? 'Desfazer'
                              : 'Marcar concluído',
                          onPressed: onToggle,
                        ),
                        IconButton(
                          iconSize: 18,
                          visualDensity: VisualDensity.compact,
                          icon: const Icon(Icons.edit_outlined,
                              color: Colors.white54),
                          tooltip: 'Editar',
                          onPressed: onEdit,
                        ),
                        IconButton(
                          iconSize: 18,
                          visualDensity: VisualDensity.compact,
                          icon: const Icon(Icons.delete_outline,
                              color: Colors.white38),
                          tooltip: 'Excluir',
                          onPressed: onDelete,
                        ),
                      ],
                    ),
                  ],
                ),
              ],
            ),
            if (schedule.notes.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(
                schedule.notes,
                style: const TextStyle(
                  fontSize: 12,
                  color: Colors.white38,
                  fontStyle: FontStyle.italic,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Schedule Card (kept for possible reuse)
// ---------------------------------------------------------------------------

class _ScheduleCard extends StatelessWidget {
  const _ScheduleCard({
    required this.schedule,
    required this.onToggle,
    required this.onEdit,
    required this.onDelete,
  });

  final CleaningSchedule schedule;
  final VoidCallback onToggle;
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
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        schedule.title,
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'Colaborador: ${schedule.collaboratorName ?? '—'}',
                      ),
                    ],
                  ),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  decoration: BoxDecoration(
                    color: schedule.done
                        ? const Color(0xFF2E7D32).withValues(alpha: 0.12)
                        : const Color(0xFF5C6BC0).withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Text(
                    schedule.done ? 'Concluído' : 'Pendente',
                    style: TextStyle(
                      color: schedule.done
                          ? const Color(0xFF2E7D32)
                          : const Color(0xFF5C6BC0),
                      fontWeight: FontWeight.w700,
                      fontSize: 12,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 16,
              runSpacing: 4,
              children: [
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.calendar_today, size: 14),
                    const SizedBox(width: 4),
                    Text(formatDate(schedule.scheduledDate)),
                  ],
                ),
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.access_time, size: 14),
                    const SizedBox(width: 4),
                    Text('${schedule.startTime} – ${schedule.endTime}'),
                  ],
                ),
              ],
            ),
            if (schedule.location.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('Local: ${schedule.location}'),
            ],
            if (schedule.notes.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(
                schedule.notes,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: FilledButton.tonalIcon(
                    onPressed: onToggle,
                    icon: Icon(
                      schedule.done
                          ? Icons.undo
                          : Icons.check_circle_outline,
                    ),
                    label: Text(
                      schedule.done ? 'Desfazer conclusão' : 'Marcar concluído',
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton(
                  onPressed: onEdit,
                  icon: const Icon(Icons.edit_outlined),
                  tooltip: 'Editar',
                ),
                IconButton(
                  onPressed: onDelete,
                  icon: const Icon(Icons.delete_outline),
                  tooltip: 'Excluir',
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
