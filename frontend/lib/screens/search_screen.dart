import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../theme/app_theme.dart';

/// How long to wait after the last keystroke before actually calling
/// GET /stocks/search — keeps a fast typist from firing one request per
/// character.
const Duration _debounceDelay = Duration(milliseconds: 350);

/// Standalone stock search — reached via the magnifying-glass icon on the
/// dashboard (home) tab's AppBar, not a bottom-nav tab (see
/// dashboard_screen.dart's comment on why: no shared AppBar exists across
/// tabs to host a persistent search bar in). The search bar itself lives
/// directly in THIS screen's own AppBar, auto-focused on entry.
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final TextEditingController _controller = TextEditingController();
  Timer? _debounce;
  bool _loading = false;
  String? _error;
  List<Map<String, dynamic>> _results = [];

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    final query = value.trim();
    if (query.isEmpty) {
      setState(() {
        _results = [];
        _loading = false;
        _error = null;
      });
      return;
    }
    // Flip the spinner on immediately (typing is the user's signal that
    // something's about to happen) even though the actual request is
    // debounced — this also keeps build() current on every keystroke,
    // which the "empty query -> show nothing" check below relies on.
    setState(() {
      _loading = true;
      _error = null;
    });
    _debounce = Timer(_debounceDelay, () => _search(query));
  }

  Future<void> _search(String query) async {
    final api = context.read<ApiService>();
    try {
      final res = await api.get('/stocks/search?q=${Uri.encodeQueryComponent(query)}');
      if (!mounted) return;
      // A response for an older, already-superseded query could in
      // principle land after a newer one (out-of-order network timing) --
      // only trust it if it still matches what's in the field right now.
      if (_controller.text.trim() != query) return;
      if (res.statusCode == 200) {
        final list = jsonDecode(res.body) as List<dynamic>;
        setState(() {
          _results = list.map((e) => e as Map<String, dynamic>).toList();
          _loading = false;
        });
      } else {
        setState(() {
          _error = 'Search failed (${res.statusCode}).';
          _loading = false;
        });
      }
    } catch (e) {
      if (!mounted || _controller.text.trim() != query) return;
      setState(() {
        _error = 'Network error: $e';
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    // Same top-level-route background handling as trade_history_screen.dart
    // and stock_detail_screen.dart — no Colors.transparent override, since
    // this is pushed outside _MainShell's gradient-painting Scaffold.
    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: _controller,
          autofocus: true,
          onChanged: _onChanged,
          textInputAction: TextInputAction.search,
          decoration: const InputDecoration(
            // The magnifying-glass icon on the left of the bar, per spec.
            prefixIcon: Icon(Icons.search_rounded),
            hintText: 'Search stocks',
            border: InputBorder.none,
          ),
        ),
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    final t = Theme.of(context).textTheme;

    // Empty query: nothing shown at all — no error, no placeholder list.
    if (_controller.text.trim().isEmpty) {
      return const SizedBox.shrink();
    }

    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: AppTheme.accent));
    }

    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(
            _error!,
            textAlign: TextAlign.center,
            style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
        ),
      );
    }

    if (_results.isEmpty) {
      return Center(
        child: Text(
          'No results',
          style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
        ),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: _results.length,
      itemBuilder: (context, i) {
        final r = _results[i];
        final ticker = r['ticker'] as String;
        return ListTile(
          title: Text(ticker, style: const TextStyle(fontWeight: FontWeight.w600)),
          subtitle: Text(
            '${r['name']} · ${r['exchange']}',
            style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
          trailing: const Icon(Icons.chevron_right_rounded),
          onTap: () => context.push('/stock/$ticker'),
        );
      },
    );
  }
}
