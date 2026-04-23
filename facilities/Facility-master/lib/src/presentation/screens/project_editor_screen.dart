import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../domain/entities/models.dart';
import '../providers/providers.dart';

class ProjectEditorScreen extends ConsumerStatefulWidget {
  const ProjectEditorScreen({super.key, this.project});

  final Project? project;

  @override
  ConsumerState<ProjectEditorScreen> createState() =>
      _ProjectEditorScreenState();
}

class _ProjectEditorScreenState extends ConsumerState<ProjectEditorScreen> {
  late final TextEditingController _nameController;
  late final TextEditingController _clientController;
  late final TextEditingController _phoneController;
  late final TextEditingController _addressController;
  late final TextEditingController _notesController;

  bool get _isEditing => widget.project != null;

  @override
  void initState() {
    super.initState();
    _nameController = TextEditingController(text: widget.project?.name ?? '');
    _clientController =
        TextEditingController(text: widget.project?.clientName ?? '');
    _phoneController =
        TextEditingController(text: widget.project?.clientPhone ?? '');
    _addressController =
        TextEditingController(text: widget.project?.clientAddress ?? '');
    _notesController =
        TextEditingController(text: widget.project?.notes ?? '');
  }

  @override
  void dispose() {
    _nameController.dispose();
    _clientController.dispose();
    _phoneController.dispose();
    _addressController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_isEditing ? 'Editar obra' : 'Nova obra'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _nameController,
            decoration: const InputDecoration(
              labelText: 'Nome da obra *',
              hintText: 'Ex: Reforma apt 302',
              prefixIcon: Icon(Icons.construction_outlined),
            ),
            textCapitalization: TextCapitalization.sentences,
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _clientController,
            decoration: const InputDecoration(
              labelText: 'Nome do cliente *',
              prefixIcon: Icon(Icons.person_outline),
            ),
            textCapitalization: TextCapitalization.words,
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _phoneController,
            decoration: const InputDecoration(
              labelText: 'Telefone do cliente',
              prefixIcon: Icon(Icons.phone_outlined),
            ),
            keyboardType: TextInputType.phone,
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _addressController,
            decoration: const InputDecoration(
              labelText: 'Endereço da obra',
              prefixIcon: Icon(Icons.location_on_outlined),
            ),
            textCapitalization: TextCapitalization.sentences,
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _notesController,
            decoration: const InputDecoration(
              labelText: 'Observações gerais',
              prefixIcon: Icon(Icons.notes_outlined),
            ),
            maxLines: 3,
            textCapitalization: TextCapitalization.sentences,
          ),
          const SizedBox(height: 24),
          FilledButton.icon(
            onPressed: _save,
            icon: const Icon(Icons.save),
            label: Text(_isEditing ? 'Salvar alterações' : 'Criar obra'),
          ),
        ],
      ),
    );
  }

  Future<void> _save() async {
    final name = _nameController.text.trim();
    final client = _clientController.text.trim();

    if (name.isEmpty || client.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Preencha o nome da obra e do cliente.')),
      );
      return;
    }

    final project = (widget.project ?? Project(
      name: '',
      clientName: '',
      createdAt: DateTime.now(),
    ))
        .copyWith(
      name: name,
      clientName: client,
      clientPhone: _phoneController.text.trim(),
      clientAddress: _addressController.text.trim(),
      notes: _notesController.text.trim(),
    );

    await ref.read(projectRepositoryProvider).saveProject(project);
    ref.invalidate(projectsProvider);

    if (mounted) {
      Navigator.pop(context);
    }
  }
}
