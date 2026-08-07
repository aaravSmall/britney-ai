import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/portfolio_bus.dart';
import '../theme/app_theme.dart';
import '../utils/format.dart';

/// Opens the buy/sell flow for [ticker] as a modal bottom sheet, invocable
/// from any screen (search results, stock detail, favorites, ...) with
/// access to the app's root providers (ApiService, PortfolioBus — both
/// provided above the router in main.dart, so this works the same
/// regardless of which screen calls it).
///
/// This function owns the whole flow end to end — prompting for quantity
/// (via a $ ⇄ shares entry sheet with a confirm step), submitting the
/// trade, firing the cross-tab sync notifier, and showing the result — so
/// callers never need their own copy of that logic (the same shape
/// recommendations_screen.dart's old _trade()/_promptQuantity() had, now
/// in exactly one place).
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

/// Which unit the amount field's text represents. Toggling recomputes the
/// field's value via the live price rather than clearing it, so switching
/// units mid-entry doesn't lose what the user typed.
enum _InputMode { dollars, shares }

/// Quantity-entry sheet content — pops with the entered quantity (in
/// shares, regardless of which mode it was entered in), or null on
/// cancel. The actual trade submission happens in showBuySellSheet() after
/// this sheet closes, not in here, so this widget stays a plain,
/// presentation-only prompt (now with its own live-price fetch, needed
/// for the $ ⇄ shares conversion and preview line, and a confirm step
/// before it pops).
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
  late final TextEditingController _controller = TextEditingController(text: '1')
    ..addListener(() => setState(() {}));
  _InputMode _mode = _InputMode.shares;

  bool _priceLoading = true;
  bool _priceError = false;
  double? _price;

  @override
  void initState() {
    super.initState();
    _loadPrice();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _loadPrice() async {
    final api = context.read<ApiService>();
    setState(() {
      _priceLoading = true;
      _priceError = false;
    });
    try {
      final res = await api.get(
        '/trading/price?symbol=${widget.ticker}&asset_type=${widget.assetType}',
      );
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = jsonDecode(res.body) as Map<String, dynamic>;
        setState(() {
          _price = (body['price'] as num).toDouble();
          _priceLoading = false;
        });
      } else {
        setState(() {
          _priceError = true;
          _priceLoading = false;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _priceError = true;
          _priceLoading = false;
        });
      }
    }
  }

  /// The entered value converted into shares — the unit the trade API and
  /// preview line both need, regardless of which mode is active.
  double? get _enteredShares {
    final entered = double.tryParse(_controller.text);
    final price = _price;
    if (entered == null || entered <= 0 || price == null || price <= 0) return null;
    return _mode == _InputMode.shares ? entered : entered / price;
  }

  void _switchMode(_InputMode newMode) {
    if (newMode == _mode) return;
    final price = _price;
    final entered = double.tryParse(_controller.text);
    if (price != null && price > 0 && entered != null && entered > 0) {
      final converted = newMode == _InputMode.shares ? entered / price : entered * price;
      _controller.text =
          newMode == _InputMode.shares ? formatQuantity(converted) : converted.toStringAsFixed(2);
    }
    setState(() => _mode = newMode);
  }

  Future<void> _submit() async {
    final shares = _enteredShares;
    final price = _price;
    if (shares == null || price == null) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => _ConfirmTradeDialog(
        side: widget.side,
        ticker: widget.ticker,
        assetType: widget.assetType,
        quantity: shares,
        price: price,
      ),
    );
    if (confirmed == true && mounted) {
      Navigator.pop(context, shares);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final buy = widget.side == 'buy';
    final label = buy ? 'Buy' : 'Sell';
    final shares = _enteredShares;
    final canSubmit = shares != null;

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
            _priceLoading
                ? widget.assetType
                : _priceError || _price == null
                    ? widget.assetType
                    : '${widget.assetType} · \$${_price!.toStringAsFixed(2)}',
            style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: SegmentedButton<_InputMode>(
              segments: const [
                ButtonSegment(value: _InputMode.dollars, label: Text('\$')),
                ButtonSegment(value: _InputMode.shares, label: Text('Shares')),
              ],
              selected: {_mode},
              showSelectedIcon: false,
              onSelectionChanged: (s) => _switchMode(s.first),
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _controller,
            autofocus: true,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
              labelText: _mode == _InputMode.dollars ? 'Amount' : 'Shares',
              prefixText: _mode == _InputMode.dollars ? '\$' : null,
            ),
            onSubmitted: (_) => canSubmit ? _submit() : null,
          ),
          const SizedBox(height: 12),
          _buildStatusLine(t),
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

  Widget _buildStatusLine(TextTheme t) {
    if (_priceLoading) {
      return Row(
        children: [
          const SizedBox(
            width: 14,
            height: 14,
            child: CircularProgressIndicator(strokeWidth: 2, color: AppTheme.accent),
          ),
          const SizedBox(width: 8),
          Text(
            'Fetching live price…',
            style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
        ],
      );
    }
    if (_priceError || _price == null) {
      return Row(
        children: [
          Expanded(
            child: Text(
              "Couldn't fetch a live price for ${widget.ticker}.",
              style: t.bodySmall?.copyWith(color: AppTheme.danger),
            ),
          ),
          TextButton(onPressed: _loadPrice, child: const Text('Retry')),
        ],
      );
    }

    final shares = _enteredShares;
    if (shares == null) {
      return Text(
        'Enter an amount to see a preview.',
        style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
      );
    }
    final dollars = shares * _price!;
    final verb = widget.side == 'buy' ? 'buying' : 'selling';
    return Text(
      'You are $verb ${formatQuantity(shares)} shares (~\$${dollars.toStringAsFixed(2)})',
      style: t.bodySmall?.copyWith(fontWeight: FontWeight.w600),
    );
  }
}

/// Explicit confirm/cancel step shown before any trade actually submits —
/// no trade fires on the first tap of Buy/Sell in the sheet above.
class _ConfirmTradeDialog extends StatelessWidget {
  const _ConfirmTradeDialog({
    required this.side,
    required this.ticker,
    required this.assetType,
    required this.quantity,
    required this.price,
  });

  final String side;
  final String ticker;
  final String assetType;
  final double quantity;
  final double price;

  @override
  Widget build(BuildContext context) {
    final buy = side == 'buy';
    final label = buy ? 'Buy' : 'Sell';
    final total = quantity * price;

    return AlertDialog(
      title: Text('Confirm $label'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _ConfirmRow('Side', label),
          _ConfirmRow('Ticker', ticker),
          _ConfirmRow('Asset type', assetType),
          _ConfirmRow('Quantity', '${formatQuantity(quantity)} shares'),
          _ConfirmRow('Price', '\$${price.toStringAsFixed(2)}'),
          _ConfirmRow('Estimated total', '\$${total.toStringAsFixed(2)}'),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: Text('Confirm $label'),
        ),
      ],
    );
  }
}

class _ConfirmRow extends StatelessWidget {
  const _ConfirmRow(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Text(
            label,
            style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
          const Spacer(),
          Text(value, style: t.bodyMedium?.copyWith(fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}
