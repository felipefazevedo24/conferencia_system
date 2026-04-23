import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../domain/entities/models.dart';
import '../providers/providers.dart';

class CollaboratorsScreen extends ConsumerWidget {
  const CollaboratorsScreen({super.key, this.embedded = false});

  final bool embedded;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final collaboratorsAsync = ref.watch(collaboratorsProvider);

    final listContent = collaboratorsAsync.when(
      data: (list) {
        if (list.isEmpty) {
          return Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: Text(
                embedded
                    ? 'Nenhum colaborador cadastrado.\nUse "Novo colaborador" para adicionar.'
                    : 'Nenhum colaborador cadastrado.\nToque no botão abaixo para adicionar.',
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 16),
              ),
            ),
          );
        }

        return ListView.builder(
          padding: const EdgeInsets.all(16),
          itemCount: list.length,
          itemBuilder: (context, index) {
            final c = list[index];
            return Card(
              margin: const EdgeInsets.only(bottom: 12),
              child: ListTile(
                leading: const CircleAvatar(
                  child: Icon(Icons.person),
                ),
                title: Text(c.name),
                subtitle: Text(
                  [
                    if (c.role.isNotEmpty) c.role,
                    c.accessLevel.label,
                    if (c.phone.isNotEmpty) c.phone,
                  ].join(' • '),
                ),
                trailing: PopupMenuButton<String>(
                  onSelected: (value) {
                    if (value == 'edit') {
                      _showEditor(context, ref, collaborator: c);
                    }
                    if (value == 'delete') _confirmDelete(context, ref, c);
                  },
                  itemBuilder: (_) => const [
                    PopupMenuItem(value: 'edit', child: Text('Editar')),
                    PopupMenuItem(value: 'delete', child: Text('Excluir')),
                  ],
                ),
              ),
            );
          },
        );
      },
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => Center(child: Text('Erro: $e')),
    );

    final content = Container(
      decoration: const BoxDecoration(
        gradient: RadialGradient(
          center: Alignment.topCenter,
          radius: 1.15,
          colors: [Color(0xFF1A2166), Color(0xFF0B1026)],
        ),
      ),
      child: embedded
          ? Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          'Equipe cadastrada',
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                      ),
                      FilledButton.icon(
                        onPressed: () => _showEditor(context, ref),
                        icon: const Icon(Icons.person_add),
                        label: const Text('Novo colaborador'),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 8),
                Expanded(child: listContent),
              ],
            )
          : listContent,
    );

    if (embedded) {
      return content;
    }

    return Scaffold(
      appBar: AppBar(title: const Text('Colaboradores')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _showEditor(context, ref),
        icon: const Icon(Icons.person_add),
        label: const Text('Novo colaborador'),
      ),
      body: content,
    );
  }

  Future<void> _showEditor(
    BuildContext context,
    WidgetRef ref, {
    Collaborator? collaborator,
  }) async {
    final nameCtrl = TextEditingController(text: collaborator?.name ?? '');
    final roleCtrl = TextEditingController(text: collaborator?.role ?? '');
    final phoneCtrl = TextEditingController(text: collaborator?.phone ?? '');
    final isEditing = collaborator != null;
    CollaboratorAccessLevel selectedAccessLevel =
        collaborator?.accessLevel ?? CollaboratorAccessLevel.requester;

    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(isEditing ? 'Editar colaborador' : 'Novo colaborador'),
        content: SizedBox(
          width: 420,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: nameCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Nome *',
                    prefixIcon: Icon(Icons.person_outline),
                  ),
                  textCapitalization: TextCapitalization.words,
                  autofocus: true,
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: roleCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Função / cargo',
                    hintText: 'Ex: Auxiliar de limpeza',
                    prefixIcon: Icon(Icons.badge_outlined),
                  ),
                  textCapitalization: TextCapitalization.sentences,
                ),
                const SizedBox(height: 12),
                StatefulBuilder(
                  builder: (context, setStateDialog) {
                    return DropdownButtonFormField<CollaboratorAccessLevel>(
                      value: selectedAccessLevel,
                      decoration: const InputDecoration(
                        labelText: 'Nível de acesso *',
                        prefixIcon: Icon(Icons.admin_panel_settings_outlined),
                      ),
                      items: CollaboratorAccessLevel.values.map((level) {
                        return DropdownMenuItem(
                          value: level,
                          child: Text(level.label),
                        );
                      }).toList(),
                      onChanged: (value) {
                        if (value != null) {
                          setStateDialog(() => selectedAccessLevel = value);
                        }
                      },
                    );
                  },
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: phoneCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Telefone',
                    prefixIcon: Icon(Icons.phone_outlined),
                  ),
                  keyboardType: TextInputType.phone,
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
              if (nameCtrl.text.trim().isEmpty) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Informe o nome do colaborador.')),
                );
                return;
              }
              Navigator.pop(ctx, true);
            },
            child: Text(isEditing ? 'Salvar' : 'Adicionar'),
          ),
        ],
      ),
    );

    if (saved != true) {
      nameCtrl.dispose();
      roleCtrl.dispose();
      phoneCtrl.dispose();
      return;
    }

    final entity = (collaborator ?? Collaborator(name: '', createdAt: DateTime.now())).copyWith(
      name: nameCtrl.text.trim(),
      role: roleCtrl.text.trim(),
      phone: phoneCtrl.text.trim(),
      accessLevel: selectedAccessLevel,
    );

    nameCtrl.dispose();
    roleCtrl.dispose();
    phoneCtrl.dispose();

    await ref.read(collaboratorRepositoryProvider).saveCollaborator(entity);
    ref.invalidate(collaboratorsProvider);
    ref.invalidate(cleaningSchedulesProvider);
    ref.invalidate(epiRequestsProvider);
  }

  Future<void> _confirmDelete(
    BuildContext context,
    WidgetRef ref,
    Collaborator collaborator,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Excluir colaborador?'),
        content: Text(
          '"${collaborator.name}" e seus cronogramas serão excluídos permanentemente.',
        ),
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

    if (confirmed == true && collaborator.id != null) {
      await ref.read(collaboratorRepositoryProvider).deleteCollaborator(collaborator.id!);
      ref.invalidate(collaboratorsProvider);
      ref.invalidate(cleaningSchedulesProvider);
      ref.invalidate(epiRequestsProvider);
    }
  }
}
