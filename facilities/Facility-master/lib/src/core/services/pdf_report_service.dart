import 'dart:typed_data';

import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;
import 'package:share_plus/share_plus.dart';

import '../../domain/entities/models.dart';
import '../utils/formatters.dart';

class PdfReportService {
  Future<Uint8List> buildReportBytes({
    required Project project,
    required List<WorkTask> tasks,
  }) async {
    final pdf = pw.Document();

    final completedCount = tasks.where((t) => t.isCompleted).length;
    final pausedCount = tasks.where((t) => t.status == TaskStatus.paused).length;
    final progress =
        tasks.isEmpty ? 0 : ((completedCount / tasks.length) * 100).round();

    pdf.addPage(
      pw.MultiPage(
        pageFormat: PdfPageFormat.a4,
        margin: const pw.EdgeInsets.all(32),
        header: (context) => pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          children: [
            pw.Text(
              'Relatório de evolução da obra',
              style: pw.TextStyle(
                fontSize: 20,
                fontWeight: pw.FontWeight.bold,
              ),
            ),
            pw.SizedBox(height: 4),
            pw.Text('Projeto: ${project.name}'),
            pw.Text('Cliente: ${project.clientName}'),
            pw.Text('ID: ${project.displayCode}'),
            if (project.clientAddress.isNotEmpty)
              pw.Text('Endereço: ${project.clientAddress}'),
            pw.SizedBox(height: 8),
            pw.Text(
              'Evolução: $progress% • $completedCount/${tasks.length} concluída(s) • $pausedCount pausada(s)',
            ),
            pw.SizedBox(height: 4),
            pw.Text(
              'Gerado em ${formatDateTime(DateTime.now())}',
              style: const pw.TextStyle(fontSize: 9),
            ),
            pw.Divider(),
          ],
        ),
        build: (context) {
          if (tasks.isEmpty) {
            return [
              pw.Text('Nenhuma tarefa cadastrada neste projeto.'),
            ];
          }

          return tasks.map((task) {
            return pw.Container(
              margin: const pw.EdgeInsets.only(bottom: 12),
              padding: const pw.EdgeInsets.all(10),
              decoration: pw.BoxDecoration(
                border: pw.Border.all(color: PdfColors.grey400),
                borderRadius: pw.BorderRadius.circular(6),
              ),
              child: pw.Column(
                crossAxisAlignment: pw.CrossAxisAlignment.start,
                children: [
                  pw.Row(
                    mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
                    children: [
                      pw.Text(
                        task.title,
                        style: pw.TextStyle(fontWeight: pw.FontWeight.bold),
                      ),
                      pw.Text(
                        task.status.label,
                        style: pw.TextStyle(
                          fontWeight: pw.FontWeight.bold,
                          color: task.status == TaskStatus.paused
                              ? PdfColors.orange800
                              : task.isCompleted
                                  ? PdfColors.green800
                                  : PdfColors.blueGrey,
                        ),
                      ),
                    ],
                  ),
                  if (task.room.isNotEmpty)
                    pw.Text('Ambiente: ${task.room}'),
                  if (task.description.isNotEmpty)
                    pw.Text('Descrição: ${task.description}'),
                  if ((task.note ?? '').isNotEmpty)
                    pw.Text('Observação: ${task.note}'),
                  if ((task.materialIssue ?? '').isNotEmpty)
                    pw.Text(
                      'Impedimento: ${task.materialIssue}',
                      style: pw.TextStyle(
                        color: PdfColors.orange800,
                        fontWeight: pw.FontWeight.bold,
                      ),
                    ),
                  if (task.hasPhoto)
                    pw.Text('Foto: anexada', style: const pw.TextStyle(color: PdfColors.green700)),
                  if (task.updatedAt != null)
                    pw.Text(
                      'Atualizado: ${formatDateTime(task.updatedAt!)}',
                      style: const pw.TextStyle(fontSize: 9),
                    ),
                ],
              ),
            );
          }).toList();
        },
      ),
    );

    return pdf.save();
  }

  Future<XFile> generateReportPdf({
    required Project project,
    required List<WorkTask> tasks,
  }) async {
    final bytes = await buildReportBytes(project: project, tasks: tasks);
    final fileName = 'evolucao_obra_${project.displayCode}.pdf';

    return XFile.fromData(
      bytes,
      name: fileName,
      mimeType: 'application/pdf',
    );
  }
}
