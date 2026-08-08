import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../theme/app_theme.dart';

/// Opens the "new auto-invest schedule" flow as a modal bottom sheet and
/// submits it — same shape as showBuySellSheet/showCashAmountSheet:
/// prompt in the sheet, do the actual API call after it closes, report
/// the result via a snackbar. Returns the created schedule (decoded
/// JSON) on success, or null on cancel/failure, so the caller
/// (AccountScreen's auto-invest section) knows whether to refresh its
/// list.
Future<Map<String, dynamic>?> showAddAutoInvestSheet(BuildContext context) async {
  final messenger = ScaffoldMessenger.of(context);
  final api = context.read<ApiService>();

  final draft = await showModalBottomSheet<_ScheduleDraft>(
    context: context,
    isScrollControlled: true,
    builder: (_) => const _AddAutoInvestSheet(),
  );
  if (draft == null || !context.mounted) return null;

  try {
    final res = await api.post('/auto-invest/schedules', {
      'ticker': draft.ticker,
      'asset_type': draft.assetType,
      'amount': draft.amount,
      'interval': draft.interval,
    });
    if (res.statusCode >= 200 && res.statusCode < 300) {
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      messenger.showSnackBar(
        SnackBar(content: Text('Auto-invest set up for ${body['ticker']}')),
      );
      return body;
    }
    messenger.showSnackBar(
      SnackBar(content: Text(_errorDetail(res.body)), backgroundColor: AppTheme.danger),
    );
  } catch (e) {
    messenger.showSnackBar(
      SnackBar(content: Text('Network error: $e'), backgroundColor: AppTheme.danger),
    );
  }
  return null;
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
  return 'Could not create schedule';
}

class _ScheduleDraft {
  const _ScheduleDraft({
    required this.ticker,
    required this.assetType,
    required this.amount,
    required this.interval,
  });

  final String ticker;
  final String assetType;
  final double amount;
  final String interval;
}

class _AddAutoInvestSheet extends StatefulWidget {
  const _AddAutoInvestSheet();

  @override
  State<_AddAutoInvestSheet> createState() => _AddAutoInvestSheetState();
}

class _AddAutoInvestSheetState extends State<_AddAutoInvestSheet> {
  final TextEditingController _tickerController = TextEditingController();
  final TextEditingController _amountController = TextEditingController();
  Timer? _searchDebounce;
  List<Map<String, dynamic>> _results = [];
  bool _searching = false;
  String? _selectedTicker;
  String _interval = 'daily';

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _tickerController.dispose();
    _amountController.dispose();
    super.dispose();
  }

  void _onTickerChanged(String value) {
    _selectedTicker = null;
    _searchDebounce?.cancel();
    final query = value.trim();
    if (query.isEmpty) {
      setState(() {
        _results = [];
        _searching = false;
      });
      return;
    }
    setState(() => _searching = true);
    _searchDebounce = Timer(const Duration(milliseconds: 300), () => _runSearch(query));
  }

  Future<void> _runSearch(String query) async {
    final api = context.read<ApiService>();
    try {
      final res = await api.get('/stocks/search?q=${Uri.encodeQueryComponent(query)}');
      if (!mounted || _tickerController.text.trim() != query) return;
      if (res.statusCode == 200) {
        final list = jsonDecode(res.body) as List<dynamic>;
        setState(() {
          _results = list.map((e) => e as Map<String, dynamic>).toList();
          _searching = false;
        });
      } else {
        setState(() => _searching = false);
      }
    } catch (_) {
      if (mounted) setState(() => _searching = false);
    }
  }

  void _pickTicker(String ticker) {
    setState(() {
      _selectedTicker = ticker;
      _tickerController.text = ticker;
      _results = [];
    });
  }

  double? get _enteredAmount {
    final v = double.tryParse(_amountController.text);
    if (v == null || v <= 0) return null;
    return v;
  }

  bool get _canSubmit => _selectedTicker != null && _enteredAmount != null;

  void _submit() {
    if (!_canSubmit) return;
    Navigator.pop(
      context,
      _ScheduleDraft(
        ticker: _selectedTicker!,
        assetType: 'stock',
        amount: _enteredAmount!,
        interval: _interval,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;

    return Padding(
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
          Text('New auto-invest schedule', style: t.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(
            'Simulated cash — buys a fixed dollar amount on a recurring cadence.',
            style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _tickerController,
            autofocus: true,
            textCapitalization: TextCapitalization.characters,
            decoration: const InputDecoration(labelText: 'Ticker', hintText: 'e.g. VOO'),
            onChanged: _onTickerChanged,
          ),
          if (_searching)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: LinearProgressIndicator(minHeight: 2, color: AppTheme.accent),
            ),
          if (_results.isNotEmpty)
            Container(
              margin: const EdgeInsets.only(top: 4),
              constraints: const BoxConstraints(maxHeight: 180),
              decoration: BoxDecoration(
                border: Border.all(color: AppTheme.borderSubtleOf(context)),
                borderRadius: BorderRadius.circular(10),
              ),
              child: ListView.separated(
                shrinkWrap: true,
                itemCount: _results.length,
                separatorBuilder: (_, __) => const Divider(height: 1),
                itemBuilder: (_, i) {
                  final r = _results[i];
                  return ListTile(
                    dense: true,
                    title: Text(r['ticker'] as String),
                    subtitle: Text(
                      r['name'] as String? ?? '',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    onTap: () => _pickTicker(r['ticker'] as String),
                  );
                },
              ),
            ),
          if (_selectedTicker != null)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                'Selected: $_selectedTicker',
                style: t.bodySmall?.copyWith(color: AppTheme.accent, fontWeight: FontWeight.w600),
              ),
            ),
          const SizedBox(height: 16),
          TextField(
            controller: _amountController,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Amount per execution', prefixText: '\$'),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 16),
          Text('Interval', style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context))),
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'daily', label: Text('Daily')),
                ButtonSegment(value: 'weekly', label: Text('Weekly')),
                ButtonSegment(value: 'monthly', label: Text('Monthly')),
              ],
              selected: {_interval},
              showSelectedIcon: false,
              onSelectionChanged: (s) => setState(() => _interval = s.first),
            ),
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
                  onPressed: _canSubmit ? _submit : null,
                  child: const Text('Add schedule'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
