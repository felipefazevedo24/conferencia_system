import 'dart:typed_data';

import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;
import 'package:share_plus/share_plus.dart';

import '../../domain/entities/models.dart';

class EpiReportService {
  Future<Uint8List> buildReportBytes({
    required List<EpiRequest> requests,
    required String generatedBy,
    required String activeTab,
    required String filtersLabel,
  }) async {
    final pdf = pw.Document();

    final requestedCount =
        requests.where((r) => r.status == EpiRequestStatus.requested).length;
    final approvedCount =
        requests.where((r) => r.status == EpiRequestStatus.approved).length;
    final deniedCount =
        requests.where((r) => r.status == EpiRequestStatus.denied).length;

    pdf.addPage(
      pw.MultiPage(
        pageFormat: PdfPageFormat.a4,
        margin: const pw.EdgeInsets.all(28),
        header: (context) => pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          children: [
            pw.Text(
              'Relatorio de Entregas de Uniforme e EPI',
              style: pw.TextStyle(fontSize: 18, fontWeight: pw.FontWeight.bold),
            ),
            pw.SizedBox(height: 4),
            pw.Text('Gerado por: $generatedBy'),
            pw.Text('Aba ativa: $activeTab'),
            pw.Text('Filtros: $filtersLabel'),
            pw.Text('Total: ${requests.length}'),
            pw.Text(
              'Solicitadas: $requestedCount | Liberadas: $approvedCount | Negadas: $deniedCount',
            ),
            pw.SizedBox(height: 4),
            pw.Divider(),
          ],
        ),
        build: (context) {
          if (requests.isEmpty) {
            return [
              pw.Text('Nenhuma solicitacao encontrada para os filtros informados.'),
            ];
          }

          return requests.map((request) {
            return pw.Container(
              margin: const pw.EdgeInsets.only(bottom: 8),
              padding: const pw.EdgeInsets.all(10),
              decoration: pw.BoxDecoration(
                border: pw.Border.all(color: PdfColors.grey500),
                borderRadius: pw.BorderRadius.circular(6),
              ),
              child: pw.Column(
                crossAxisAlignment: pw.CrossAxisAlignment.start,
                children: [
                  pw.Text(
                    '${request.internalCode}${request.internalCode.isNotEmpty ? ' - ' : ''}${request.requestType.label} - ${request.itemName}',
                    style: pw.TextStyle(fontWeight: pw.FontWeight.bold),
                  ),
                  pw.Text('Status: ${request.status.label}'),
                  pw.Text(
                    'Quantidade: ${request.quantity}${request.size.isEmpty ? '' : ' | Tamanho: ${request.size}'}',
                  ),
                  pw.Text('Colaborador: ${request.collaboratorName ?? '-'}'),
                  pw.Text('Solicitado por: ${request.requestedByName ?? '-'}'),
                  pw.Text('Data solicitacao: ${_formatDateTime(request.requestedAt)}'),
                  if (request.reason.isNotEmpty) pw.Text('Motivo: ${request.reason}'),
                  if (request.releasedByName != null || request.releasedAt != null)
                    pw.Text(
                      'Liberacao: ${request.releasedByName ?? '-'} em ${request.releasedAt == null ? '-' : _formatDateTime(request.releasedAt!)}',
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
    required List<EpiRequest> requests,
    required String generatedBy,
    required String activeTab,
    required String filtersLabel,
  }) async {
    final bytes = await buildReportBytes(
      requests: requests,
      generatedBy: generatedBy,
      activeTab: activeTab,
      filtersLabel: filtersLabel,
    );

    return XFile.fromData(
      bytes,
      name: 'relatorio_entregas_epi_uniforme.pdf',
      mimeType: 'application/pdf',
    );
  }

  String _formatDateTime(DateTime dateTime) {
    final day = dateTime.day.toString().padLeft(2, '0');
    final month = dateTime.month.toString().padLeft(2, '0');
    final year = dateTime.year.toString();
    final hour = dateTime.hour.toString().padLeft(2, '0');
    final minute = dateTime.minute.toString().padLeft(2, '0');
    return '$day/$month/$year $hour:$minute';
  }
}
