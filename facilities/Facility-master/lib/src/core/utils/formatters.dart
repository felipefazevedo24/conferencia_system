import 'package:intl/intl.dart';

final _currencyFormat = NumberFormat.simpleCurrency(locale: 'pt_BR');
final _decimalFormat = NumberFormat('#,##0.##', 'pt_BR');
final _dateFormat = DateFormat("dd/MM/yyyy 'às' HH:mm", 'pt_BR');

String formatCurrency(double value) => _currencyFormat.format(value);
String formatDecimal(double value) => _decimalFormat.format(value);
String formatDateTime(DateTime date) => _dateFormat.format(date);
String formatDate(DateTime date) => DateFormat('dd/MM/yyyy', 'pt_BR').format(date);
