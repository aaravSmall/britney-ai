import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/portfolio_bus.dart';
import '../theme/app_theme.dart';

/// Opens the deposit/withdraw flow for [portfolioId] as a modal bottom
/// sheet — same shape as showBuySellSheet (buy_sell_bottom_sheet.dart):
/// prompts for an amount with a confirm step, submits to the new cash
/// endpoints, fires the cross-tab sync notifier, and reports the result
/// via a snackbar.
Future<void> showCashAmountSheet(
  BuildContext context, {
  required int portfolioId,
  required String action, // "deposit" or "withdraw"
}) async {
  final messenger = ScaffoldMessenger.of(context);
  final bus = context.read<PortfolioBus>();
  final api = context.read<ApiService>();

  final amount = await showModalBottomSheet<double>(
    context: context,
    isScrollControlled: true,
    builder: (_) => _CashAmountSheet(action: action),
  );
  if (amount == null || !context.mounted) return;

  try {
    final res = await api.post('/portfolios/$portfolioId/$action', {'amount': amount});
    if (res.statusCode >= 200 && res.statusCode < 300) {
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final newBalance = (body['cash_balance'] as num).toDouble();
      bus.notifyTraded();
      final verb = action == 'deposit' ? 'Deposited' : 'Withdrew';
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            '$verb \$${amount.toStringAsFixed(2)} (simulated) · '
            'new balance \$${newBalance.toStringAsFixed(2)}',
          ),
        ),
      );
    } else {
      messenger.showSnackBar(
        SnackBar(content: Text(_errorDetail(res.body)), backgroundColor: AppTheme.danger),
      );
    }
  } catch (e) {
    messenger.showSnackBar(
      SnackBar(content: Text('Network error: $e'), backgroundColor: AppTheme.danger),
    );
  }
}

String _errorDetail(String body) {
  try {
    final decoded = jsonDecode(body);
    if (decoded is Map && decoded['detail'] != null) {
      return decoded['detail'].toString();
    }
  } catch (_) {
    // Fall through to generic message below.
  }
  return 'Request failed';
}

/// Amount-entry sheet content — pops with the entered dollar amount, or
/// null on cancel. The actual deposit/withdraw request happens in
/// showCashAmountSheet() after this sheet closes, matching
/// BuySellBottomSheet's presentation-only shape.
class _CashAmountSheet extends StatefulWidget {
  const _CashAmountSheet({required this.action});

  /// "deposit" or "withdraw" — fixed for the lifetime of this sheet.
  final String action;

  @override
  State<_CashAmountSheet> createState() => _CashAmountSheetState();
}

class _CashAmountSheetState extends State<_CashAmountSheet> {
  late final TextEditingController _controller = TextEditingController()
    ..addListener(() => setState(() {}));

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  double? get _enteredAmount {
    final v = double.tryParse(_controller.text);
    if (v == null || v <= 0) return null;
    return v;
  }

  Future<void> _submit() async {
    final amount = _enteredAmount;
    if (amount == null) return;
    final deposit = widget.action == 'deposit';

    final confirmed = await showDialog<bool>(
      context: context,
      // Must pop with the dialog's own `dialogContext`, not the sheet's
      // outer `context` — showDialog defaults to the root Navigator while
      // showModalBottomSheet defaults to the nearest one (this app nests
      // one per shell-route tab), so popping via the wrong context
      // resolves to a different Navigator and closes the wrong route
      // (observed: it silently popped the bottom sheet itself with the
      // dialog's `true`, leaking a bool into the sheet's double? result).
      builder: (dialogContext) => AlertDialog(
        title: Text('Confirm ${deposit ? 'deposit' : 'withdrawal'}'),
        content: Text(
          '${deposit ? 'Add' : 'Withdraw'} \$${amount.toStringAsFixed(2)} of simulated '
          'cash ${deposit ? 'to' : 'from'} your portfolio?',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: Text(deposit ? 'Deposit' : 'Withdraw'),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted) {
      Navigator.pop(context, amount);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final deposit = widget.action == 'deposit';
    final label = deposit ? 'Add funds' : 'Withdraw funds';
    final canSubmit = _enteredAmount != null;

    return Padding(
      // Lifts the sheet above the on-screen keyboard, same as any
      // isScrollControlled modal bottom sheet with a text field.
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 20,
        bottom: MediaQuery.of(context).viewInsets.bottom + 20,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Center(
            child: Container(
              width: 36,
              height: 4,
              margin: const EdgeInsets.only(bottom: 16),
              decoration: BoxDecoration(
                color: AppTheme.borderSubtleOf(context),
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          Text(label, style: t.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(
            'Simulated cash — paper money only, no real funds move.',
            style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _controller,
            autofocus: true,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Amount', prefixText: '\$'),
            onSubmitted: (_) => canSubmit ? _submit() : null,
          ),
          const SizedBox(height: 20),
          Row(
            children: [
              Expanded(
                child: TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Cancel'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: FilledButton(
                  onPressed: canSubmit ? _submit : null,
                  child: Text(label),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
