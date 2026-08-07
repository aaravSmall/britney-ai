import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/portfolio_bus.dart';
import '../theme/app_theme.dart';

/// Opens the buy/sell flow for [ticker] as a modal bottom sheet, invocable
/// from any screen (search results, stock detail, favorites, ...) with
/// access to the app's root providers (ApiService, PortfolioBus — both
/// provided above the router in main.dart, so this works the same
/// regardless of which screen calls it).
///
/// This function owns the whole flow end to end — prompting for quantity,
/// submitting the trade, firing the cross-tab sync notifier, and showing
/// the result — so callers never need their own copy of that logic (the
/// same shape recommendations_screen.dart's old _trade()/_promptQuantity()
/// had, now in exactly one place).
Future<void> showBuySellSheet(
  BuildContext context, {
  required String ticker,
  required String assetType,
  required String side,
}) async {
  // Captured before the two awaits below (opening the sheet, then the
  // trade request) rather than re-derived from `context` afterward —
  // same "grab what you need before the async gap" pattern the original
  // _trade() used with its `messenger` local.
  final messenger = ScaffoldMessenger.of(context);
  final bus = context.read<PortfolioBus>();
  final api = context.read<ApiService>();

  final quantity = await showModalBottomSheet<double>(
    context: context,
    isScrollControlled: true,
    builder: (_) => BuySellBottomSheet(ticker: ticker, assetType: assetType, side: side),
  );
  if (quantity == null || !context.mounted) return;

  try {
    final res = await api.post('/trading/trade', {
      'symbol': ticker,
      'asset_type': assetType,
      'side': side,
      'quantity': quantity,
      'simulate_only': true,
    });
    if (res.statusCode >= 200 && res.statusCode < 300) {
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      bus.notifyTraded();
      messenger.showSnackBar(
        SnackBar(content: Text(body['message'] as String? ?? 'Trade filled')),
      );
    } else {
      messenger.showSnackBar(
        SnackBar(
          content: Text(_errorDetail(res.body)),
          backgroundColor: AppTheme.danger,
        ),
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
  return 'Trade failed';
}

/// Quantity-entry sheet content — pops with the entered quantity (or null
/// on cancel), same as the old _promptQuantity() AlertDialog. The actual
/// trade submission happens in showBuySellSheet() after this sheet closes,
/// not in here, so this widget stays a plain, presentation-only prompt.
class BuySellBottomSheet extends StatefulWidget {
  const BuySellBottomSheet({
    super.key,
    required this.ticker,
    required this.assetType,
    required this.side,
  });

  final String ticker;
  final String assetType;

  /// "buy" or "sell" — fixed for the lifetime of this sheet, matching the
  /// original dialog (opened already knowing which button was tapped;
  /// there was never a way to switch sides mid-flow).
  final String side;

  @override
  State<BuySellBottomSheet> createState() => _BuySellBottomSheetState();
}

class _BuySellBottomSheetState extends State<BuySellBottomSheet> {
  late final TextEditingController _controller = TextEditingController(text: '1');

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final qty = double.tryParse(_controller.text);
    Navigator.pop(context, (qty != null && qty > 0) ? qty : null);
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final buy = widget.side == 'buy';
    final label = buy ? 'Buy' : 'Sell';

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
          Text(
            '$label ${widget.ticker}',
            style: t.titleMedium?.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 4),
          Text(
            widget.assetType,
            style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _controller,
            autofocus: true,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Quantity'),
            onSubmitted: (_) => _submit(),
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
                  onPressed: _submit,
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
