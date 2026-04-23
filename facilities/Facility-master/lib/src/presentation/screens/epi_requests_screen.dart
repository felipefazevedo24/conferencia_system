import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:share_plus/share_plus.dart';

import '../../domain/entities/models.dart';
import '../providers/providers.dart';

class EpiRequestsScreen extends ConsumerStatefulWidget {
  const EpiRequestsScreen({super.key});

  @override
  ConsumerState<EpiRequestsScreen> createState() => _EpiRequestsScreenState();
}

class _EpiRequestsScreenState extends ConsumerState<EpiRequestsScreen> {
  int _selectedSubmenu = 0;
  int? _selectedUserId;
  EpiRequestStatus? _filterStatus;
  EpiRequestType? _filterType;
  DateTime? _filterStartDate;
  DateTime? _filterEndDate;

  @override
  Widget build(BuildContext context) {
    final collaboratorsAsync = ref.watch(collaboratorsProvider);
    final requestsAsync = ref.watch(epiRequestsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('MMD EPI - Uniforme e EPI'),
        actions: [
          IconButton(
            onPressed: _exportFilteredReport,
            tooltip: 'Exportar relatorio PDF',
            icon: const Icon(Icons.picture_as_pdf_outlined),
          ),
        ],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(164),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: collaboratorsAsync.when(
              data: (collaborators) {
                final selectedUser = _resolveSelectedUser(collaborators);
                final isManager =
                    selectedUser?.accessLevel == CollaboratorAccessLevel.manager;

                return Column(
                  children: [
                    DropdownButtonFormField<int>(
                      value: selectedUser?.id,
                      decoration: const InputDecoration(
                        labelText: 'Usuário atual (gestão de acessos)',
                        prefixIcon: Icon(Icons.verified_user_outlined),
                      ),
                      items: collaborators
                          .where((c) => c.id != null)
                          .map(
                            (c) => DropdownMenuItem<int>(
                              value: c.id,
                              child: Text('${c.name} - ${c.accessLevel.label}'),
                            ),
                          )
                          .toList(),
                      onChanged: (value) {
                        Collaborator? nextUser;
                        for (final collaborator in collaborators) {
                          if (collaborator.id == value) {
                            nextUser = collaborator;
                            break;
                          }
                        }
                        final nextIsManager =
                            nextUser?.accessLevel == CollaboratorAccessLevel.manager;
                        setState(() {
                          _selectedUserId = value;
                          if (!nextIsManager && _selectedSubmenu == 1) {
                            _selectedSubmenu = 0;
                          }
                        });
                      },
                    ),
                    const SizedBox(height: 8),
                    SegmentedButton<int>(
                      segments: const [
                        ButtonSegment<int>(
                          value: 0,
                          icon: Icon(Icons.assignment_outlined),
                          label: Text('Solicitacoes'),
                        ),
                        ButtonSegment<int>(
                          value: 1,
                          icon: Icon(Icons.lock_open_outlined),
                          label: Text('Liberacoes'),
                        ),
                      ],
                      selected: {_selectedSubmenu},
                      onSelectionChanged: (selection) {
                        final next = selection.first;
                        if (next == 1 && !isManager) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text(
                                'Somente colaboradores com acesso de gestor podem liberar solicitacoes.',
                              ),
                            ),
                          );
                          return;
                        }
                        setState(() => _selectedSubmenu = next);
                      },
                    ),
                    const SizedBox(height: 8),
                    _RequestFilterBar(
                      selectedStatus: _filterStatus,
                      selectedType: _filterType,
                      startDate: _filterStartDate,
                      endDate: _filterEndDate,
                      onStatusChanged: (value) => setState(() => _filterStatus = value),
                      onTypeChanged: (value) => setState(() => _filterType = value),
                      onPickStartDate: () => _pickDate(isStart: true),
                      onPickEndDate: () => _pickDate(isStart: false),
                      onClearFilters: () {
                        setState(() {
                          _filterStatus = null;
                          _filterType = null;
                          _filterStartDate = null;
                          _filterEndDate = null;
                        });
                      },
                    ),
                  ],
                );
              },
              loading: () => const Padding(
                padding: EdgeInsets.all(8),
                child: LinearProgressIndicator(),
              ),
              error: (e, _) => Padding(
                padding: const EdgeInsets.all(8),
                child: Text('Erro ao carregar colaboradores: $e'),
              ),
            ),
          ),
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _showRequestEditor(context),
        icon: const Icon(Icons.add),
        label: const Text('Nova solicitacao'),
      ),
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment.topCenter,
            radius: 1.15,
            colors: [Color(0xFF1A2166), Color(0xFF0B1026)],
          ),
        ),
        child: collaboratorsAsync.when(
          data: (collaborators) {
            if (collaborators.isEmpty) {
              return const Center(
                child: Padding(
                  padding: EdgeInsets.all(24),
                  child: Text(
                    'Cadastre colaboradores para iniciar as solicitacoes de uniforme e EPI.',
                    textAlign: TextAlign.center,
                  ),
                ),
              );
            }

            final selectedUser = _resolveSelectedUser(collaborators);
            if (selectedUser == null) {
              return const Center(child: Text('Selecione um usuario para continuar.'));
            }

            return requestsAsync.when(
              data: (requests) {
                final visible = _filterRequests(requests, selectedUser)
                    .where(_matchesAdvancedFilters)
                    .toList();
                if (visible.isEmpty) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(
                        _selectedSubmenu == 0
                            ? 'Nenhuma solicitacao encontrada.'
                            : 'Nenhuma solicitacao pendente para liberacao.',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  );
                }

                return ListView.builder(
                  padding: const EdgeInsets.all(16),
                  itemCount: visible.length,
                  itemBuilder: (context, index) {
                    final request = visible[index];
                    return _RequestCard(
                      request: request,
                      showActions: _selectedSubmenu == 1 && request.isPending,
                      onApprove: () => _updateStatus(context, request, selectedUser, EpiRequestStatus.approved),
                      onDeny: () => _updateStatus(context, request, selectedUser, EpiRequestStatus.denied),
                    );
                  },
                );
              },
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => Center(child: Text('Erro ao carregar solicitacoes: $e')),
            );
          },
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => Center(child: Text('Erro: $e')),
        ),
      ),
    );
  }

  Collaborator? _resolveSelectedUser(List<Collaborator> collaborators) {
    final withId = collaborators.where((c) => c.id != null).toList();
    if (withId.isEmpty) return null;

    Collaborator? selected;
    for (final collaborator in withId) {
      if (collaborator.id == _selectedUserId) {
        selected = collaborator;
        break;
      }
    }
    if (selected != null) return selected;

    _selectedUserId = withId.first.id;
    return withId.first;
  }

  List<EpiRequest> _filterRequests(List<EpiRequest> requests, Collaborator selectedUser) {
    if (_selectedSubmenu == 1) {
      return requests.where((r) => r.status == EpiRequestStatus.requested).toList();
    }

    if (selectedUser.accessLevel == CollaboratorAccessLevel.manager) {
      return requests;
    }

    return requests
        .where((r) => r.collaboratorId == selectedUser.id || r.requestedByCollaboratorId == selectedUser.id)
        .toList();
  }

  bool _matchesAdvancedFilters(EpiRequest request) {
    final byStatus = _filterStatus == null || request.status == _filterStatus;
    final byType = _filterType == null || request.requestType == _filterType;
    final requestedDate = DateTime(request.requestedAt.year, request.requestedAt.month, request.requestedAt.day);
    final byStart = _filterStartDate == null || !requestedDate.isBefore(DateTime(_filterStartDate!.year, _filterStartDate!.month, _filterStartDate!.day));
    final byEnd = _filterEndDate == null || !requestedDate.isAfter(DateTime(_filterEndDate!.year, _filterEndDate!.month, _filterEndDate!.day));
    return byStatus && byType && byStart && byEnd;
  }

  Future<void> _pickDate({required bool isStart}) async {
    final now = DateTime.now();
    final initialDate = isStart ? (_filterStartDate ?? now) : (_filterEndDate ?? _filterStartDate ?? now);
    final picked = await showDatePicker(
      context: context,
      initialDate: initialDate,
      firstDate: DateTime(now.year - 3),
      lastDate: DateTime(now.year + 5),
    );
    if (picked == null) return;

    setState(() {
      if (isStart) {
        _filterStartDate = DateTime(picked.year, picked.month, picked.day);
        if (_filterEndDate != null && _filterEndDate!.isBefore(_filterStartDate!)) {
          _filterEndDate = _filterStartDate;
        }
      } else {
        _filterEndDate = DateTime(picked.year, picked.month, picked.day);
        if (_filterStartDate != null && _filterStartDate!.isAfter(_filterEndDate!)) {
          _filterStartDate = _filterEndDate;
        }
      }
    });
  }

  Future<void> _showRequestEditor(BuildContext context) async {
    final collaborators = await ref.read(collaboratorRepositoryProvider).getCollaborators();
    if (collaborators.isEmpty) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Cadastre colaboradores primeiro.')),
        );
      }
      return;
    }

    final selectedUser = _resolveSelectedUser(collaborators);
    if (selectedUser == null || selectedUser.id == null) return;

    final allMaterials = await ref.read(materialRepositoryProvider).getMaterials();

    int targetCollaboratorId = selectedUser.id!;
    EpiRequestType selectedType = EpiRequestType.epi;
    int? selectedMaterialId;
    final sizeCtrl = TextEditingController();
    final qtyCtrl = TextEditingController(text: '1');
    final reasonCtrl = TextEditingController();

    final saved = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        var materials = allMaterials.where((m) => m.requestType == selectedType).toList();
        return StatefulBuilder(
          builder: (context, setStateDialog) {
            return AlertDialog(
              title: const Text('Nova solicitacao de uniforme/EPI'),
              content: SizedBox(
                width: 520,
                child: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      DropdownButtonFormField<int>(
                        value: targetCollaboratorId,
                        decoration: const InputDecoration(
                          labelText: 'Colaborador que recebera *',
                          prefixIcon: Icon(Icons.person_outline),
                        ),
                        items: collaborators
                            .where((c) => c.id != null)
                            .map((c) => DropdownMenuItem<int>(value: c.id, child: Text(c.name)))
                            .toList(),
                        onChanged: (value) {
                          if (value != null) {
                            setStateDialog(() => targetCollaboratorId = value);
                          }
                        },
                      ),
                      const SizedBox(height: 12),
                      DropdownButtonFormField<EpiRequestType>(
                        value: selectedType,
                        decoration: const InputDecoration(
                          labelText: 'Tipo *',
                          prefixIcon: Icon(Icons.category_outlined),
                        ),
                        items: EpiRequestType.values
                            .map((type) => DropdownMenuItem(value: type, child: Text(type.label)))
                            .toList(),
                        onChanged: (value) {
                          if (value != null) {
                            setStateDialog(() {
                              selectedType = value;
                              materials = allMaterials.where((m) => m.requestType == value).toList();
                              selectedMaterialId = null;
                            });
                          }
                        },
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: DropdownButtonFormField<int>(
                              value: selectedMaterialId,
                              decoration: const InputDecoration(
                                labelText: 'Nome do item *',
                                prefixIcon: Icon(Icons.inventory_2_outlined),
                              ),
                              isExpanded: true,
                              items: materials
                                  .where((m) => m.id != null)
                                  .map((m) => DropdownMenuItem<int>(
                                        value: m.id,
                                        child: Text(m.name),
                                      ))
                                  .toList(),
                              onChanged: (value) {
                                setStateDialog(() => selectedMaterialId = value);
                              },
                            ),
                          ),
                          const SizedBox(width: 8),
                          IconButton(
                            tooltip: 'Cadastrar novo material',
                            icon: const Icon(Icons.add_circle_outline),
                            onPressed: () async {
                              final newMaterial = await _showNewMaterialDialog(context, selectedType);
                              if (newMaterial != null) {
                                allMaterials.add(newMaterial);
                                setStateDialog(() {
                                  materials = allMaterials.where((m) => m.requestType == selectedType).toList();
                                  selectedMaterialId = newMaterial.id;
                                });
                              }
                            },
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: TextField(
                              controller: sizeCtrl,
                              decoration: const InputDecoration(
                                labelText: 'Tamanho',
                                hintText: 'Ex: M, 40, G',
                                prefixIcon: Icon(Icons.straighten_outlined),
                              ),
                            ),
                          ),
                          const SizedBox(width: 12),
                          SizedBox(
                            width: 120,
                            child: TextField(
                              controller: qtyCtrl,
                              keyboardType: TextInputType.number,
                              decoration: const InputDecoration(
                                labelText: 'Qtd *',
                                prefixIcon: Icon(Icons.numbers_outlined),
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: reasonCtrl,
                        decoration: const InputDecoration(
                          labelText: 'Motivo',
                          prefixIcon: Icon(Icons.notes_outlined),
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
                  onPressed: () => Navigator.pop(dialogContext, false),
                  child: const Text('Cancelar'),
                ),
                FilledButton(
                  onPressed: () {
                    final qty = int.tryParse(qtyCtrl.text.trim()) ?? 0;
                    if (selectedMaterialId == null || qty <= 0) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Selecione um item e informe quantidade valida.')),
                      );
                      return;
                    }
                    Navigator.pop(dialogContext, true);
                  },
                  child: const Text('Solicitar'),
                ),
              ],
            );
          },
        );
      },
    );

    if (saved == true) {
      final qty = int.tryParse(qtyCtrl.text.trim()) ?? 1;
      final selectedMat = allMaterials.where((m) => m.id == selectedMaterialId).first;
      await ref.read(epiRequestRepositoryProvider).saveRequest(
            EpiRequest(
              collaboratorId: targetCollaboratorId,
              requestedByCollaboratorId: selectedUser.id!,
              requestType: selectedType,
              internalCode: selectedMat.internalCode,
              itemName: selectedMat.name,
              size: sizeCtrl.text.trim(),
              quantity: qty,
              reason: reasonCtrl.text.trim(),
              requestedAt: DateTime.now(),
              createdAt: DateTime.now(),
            ),
          );
      ref.invalidate(epiRequestsProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Solicitacao registrada com sucesso.')),
        );
      }
    }

    sizeCtrl.dispose();
    qtyCtrl.dispose();
    reasonCtrl.dispose();
  }

  Future<EpiMaterial?> _showNewMaterialDialog(BuildContext context, EpiRequestType type) async {
    final codeCtrl = TextEditingController();
    final nameCtrl = TextEditingController();
    var dialogType = type;

    final result = await showDialog<EpiMaterial>(
      context: context,
      builder: (dialogContext) {
        return StatefulBuilder(
          builder: (context, setStateDialog) {
            return AlertDialog(
              title: const Text('Novo material'),
              content: SizedBox(
                width: 400,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(
                      controller: codeCtrl,
                      autofocus: true,
                      decoration: const InputDecoration(
                        labelText: 'Código interno *',
                        hintText: 'Ex: EPI-001, UNI-010',
                        prefixIcon: Icon(Icons.qr_code_outlined),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: nameCtrl,
                      decoration: const InputDecoration(
                        labelText: 'Nome do material *',
                        hintText: 'Ex: Bota PVC, Luva, Camisa manga longa',
                        prefixIcon: Icon(Icons.inventory_2_outlined),
                      ),
                      textCapitalization: TextCapitalization.sentences,
                    ),
                    const SizedBox(height: 12),
                    DropdownButtonFormField<EpiRequestType>(
                      value: dialogType,
                      decoration: const InputDecoration(
                        labelText: 'Tipo *',
                        prefixIcon: Icon(Icons.category_outlined),
                      ),
                      items: EpiRequestType.values
                          .map((t) => DropdownMenuItem(value: t, child: Text(t.label)))
                          .toList(),
                      onChanged: (value) {
                        if (value != null) {
                          setStateDialog(() => dialogType = value);
                        }
                      },
                    ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(dialogContext),
                  child: const Text('Cancelar'),
                ),
                FilledButton(
                  onPressed: () async {
                    final code = codeCtrl.text.trim();
                    final name = nameCtrl.text.trim();
                    if (code.isEmpty || name.isEmpty) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Informe o código interno e o nome do material.')),
                      );
                      return;
                    }
                    final saved = await ref.read(materialRepositoryProvider).saveMaterial(
                          EpiMaterial(
                            internalCode: code,
                            name: name,
                            requestType: dialogType,
                            createdAt: DateTime.now(),
                          ),
                        );
                    ref.invalidate(materialsProvider);
                    if (dialogContext.mounted) {
                      Navigator.pop(dialogContext, saved);
                    }
                  },
                  child: const Text('Salvar'),
                ),
              ],
            );
          },
        );
      },
    );

    codeCtrl.dispose();
    nameCtrl.dispose();
    return result;
  }

  Future<void> _updateStatus(BuildContext context, EpiRequest request, Collaborator selectedUser, EpiRequestStatus status) async {
    if (selectedUser.id == null || request.id == null) return;

    await ref.read(epiRequestRepositoryProvider).updateRequestStatus(
          id: request.id!,
          status: status,
          releasedByCollaboratorId: selectedUser.id!,
        );

    ref.invalidate(epiRequestsProvider);

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            status == EpiRequestStatus.approved ? 'Solicitacao liberada.' : 'Solicitacao negada.',
          ),
        ),
      );
    }
  }

  Future<void> _exportFilteredReport() async {
    try {
      final collaborators = await ref.read(collaboratorRepositoryProvider).getCollaborators();
      final selectedUser = _resolveSelectedUser(collaborators);
      if (selectedUser == null) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Selecione um usuario para exportar.')),
          );
        }
        return;
      }

      final requests = await ref.read(epiRequestRepositoryProvider).getRequests();
      final filtered = _filterRequests(requests, selectedUser).where(_matchesAdvancedFilters).toList();

      final reportFile = await ref.read(epiReportServiceProvider).generateReportPdf(
            requests: filtered,
            generatedBy: selectedUser.name,
            activeTab: _selectedSubmenu == 0 ? 'Solicitacoes' : 'Liberacoes',
            filtersLabel: _filtersLabel,
          );

      await SharePlus.instance.share(
        ShareParams(
          files: [reportFile],
          text: 'Relatorio de entregas de uniforme e EPI.',
          title: 'Relatorio MMD EPI',
        ),
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              filtered.isEmpty
                  ? 'Relatorio exportado sem registros para os filtros atuais.'
                  : 'Relatorio exportado com ${filtered.length} registro(s).',
            ),
          ),
        );
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Erro ao exportar relatorio: $error')),
        );
      }
    }
  }

  String get _filtersLabel {
    final parts = <String>[];
    parts.add(_filterStatus?.label ?? 'Status: todos');
    parts.add(_filterType?.label ?? 'Tipo: todos');

    final dateFormatter = DateFormat('dd/MM/yyyy');
    final start = _filterStartDate == null ? 'inicio livre' : dateFormatter.format(_filterStartDate!);
    final end = _filterEndDate == null ? 'fim livre' : dateFormatter.format(_filterEndDate!);
    parts.add('Periodo: $start ate $end');
    return parts.join(' | ');
  }
}

class _RequestFilterBar extends StatelessWidget {
  const _RequestFilterBar({
    required this.selectedStatus,
    required this.selectedType,
    required this.startDate,
    required this.endDate,
    required this.onStatusChanged,
    required this.onTypeChanged,
    required this.onPickStartDate,
    required this.onPickEndDate,
    required this.onClearFilters,
  });

  final EpiRequestStatus? selectedStatus;
  final EpiRequestType? selectedType;
  final DateTime? startDate;
  final DateTime? endDate;
  final ValueChanged<EpiRequestStatus?> onStatusChanged;
  final ValueChanged<EpiRequestType?> onTypeChanged;
  final VoidCallback onPickStartDate;
  final VoidCallback onPickEndDate;
  final VoidCallback onClearFilters;

  @override
  Widget build(BuildContext context) {
    final formatter = DateFormat('dd/MM/yyyy');

    return Column(
      children: [
        Row(
          children: [
            Expanded(
              child: DropdownButtonFormField<EpiRequestStatus?>(
                value: selectedStatus,
                decoration: const InputDecoration(
                  labelText: 'Status',
                  prefixIcon: Icon(Icons.flag_outlined),
                  isDense: true,
                ),
                items: [
                  const DropdownMenuItem<EpiRequestStatus?>(
                    value: null,
                    child: Text('Todos'),
                  ),
                  ...EpiRequestStatus.values.map(
                    (status) => DropdownMenuItem<EpiRequestStatus?>(
                      value: status,
                      child: Text(status.label),
                    ),
                  ),
                ],
                onChanged: onStatusChanged,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: DropdownButtonFormField<EpiRequestType?>(
                value: selectedType,
                decoration: const InputDecoration(
                  labelText: 'Tipo',
                  prefixIcon: Icon(Icons.category_outlined),
                  isDense: true,
                ),
                items: [
                  const DropdownMenuItem<EpiRequestType?>(
                    value: null,
                    child: Text('Todos'),
                  ),
                  ...EpiRequestType.values.map(
                    (type) => DropdownMenuItem<EpiRequestType?>(
                      value: type,
                      child: Text(type.label),
                    ),
                  ),
                ],
                onChanged: onTypeChanged,
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: onPickStartDate,
                icon: const Icon(Icons.event_available_outlined),
                label: Text(startDate == null ? 'Data inicial' : formatter.format(startDate!)),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: OutlinedButton.icon(
                onPressed: onPickEndDate,
                icon: const Icon(Icons.event_outlined),
                label: Text(endDate == null ? 'Data final' : formatter.format(endDate!)),
              ),
            ),
            IconButton(
              onPressed: onClearFilters,
              tooltip: 'Limpar filtros',
              icon: const Icon(Icons.filter_alt_off_outlined),
            ),
          ],
        ),
      ],
    );
  }
}

class _RequestCard extends StatelessWidget {
  const _RequestCard({
    required this.request,
    required this.showActions,
    required this.onApprove,
    required this.onDeny,
  });

  final EpiRequest request;
  final bool showActions;
  final VoidCallback onApprove;
  final VoidCallback onDeny;

  @override
  Widget build(BuildContext context) {
    final statusColor = switch (request.status) {
      EpiRequestStatus.requested => Colors.orangeAccent,
      EpiRequestStatus.approved => Colors.greenAccent,
      EpiRequestStatus.denied => Colors.redAccent,
    };

    final formatter = DateFormat('dd/MM/yyyy HH:mm');

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '${request.internalCode}${request.internalCode.isNotEmpty ? ' - ' : ''}${request.itemName}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                Chip(
                  label: Text(request.status.label),
                  side: BorderSide(color: statusColor.withValues(alpha: 0.55)),
                  backgroundColor: statusColor.withValues(alpha: 0.16),
                  labelStyle: TextStyle(color: statusColor),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              '${request.requestType.label} • Qtd ${request.quantity}${request.size.isEmpty ? '' : ' • Tam ${request.size}'}',
            ),
            const SizedBox(height: 4),
            Text('Colaborador: ${request.collaboratorName ?? '-'}'),
            Text('Solicitado por: ${request.requestedByName ?? '-'}'),
            Text('Data: ${formatter.format(request.requestedAt)}'),
            if (request.reason.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('Motivo: ${request.reason}'),
            ],
            if (request.releasedAt != null || request.releasedByName != null) ...[
              const SizedBox(height: 4),
              Text(
                'Liberacao: ${request.releasedByName ?? '-'} em ${request.releasedAt == null ? '-' : formatter.format(request.releasedAt!)}',
              ),
            ],
            if (showActions) ...[
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: onDeny,
                      icon: const Icon(Icons.block_outlined),
                      label: const Text('Negar'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: FilledButton.icon(
                      onPressed: onApprove,
                      icon: const Icon(Icons.lock_open),
                      label: const Text('Liberar'),
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}
