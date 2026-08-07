import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../theme/app_theme.dart';

/// Favorited-stocks tab — GET /favorites, tap a row to open
/// StockDetailScreen (/stock/{ticker}, step 4), unfavorite directly via
/// the heart icon per row. A bottom-nav tab (not a pushed detail screen
/// like trade history) since it's a primary, frequently-checked list in
/// its own right, the same kind of destination Portfolio is — see
/// app_router.dart's comment on why trade history's precedent doesn't
/// actually apply here.
class FavoritesScreen extends StatefulWidget {
  const FavoritesScreen({super.key});

  @override
  State<FavoritesScreen> createState() => _FavoritesScreenState();
}

class _FavoritesScreenState extends State<FavoritesScreen> {
  bool _started = false;
  bool _loading = false;
  String? _error;
  List<Map<String, dynamic>> _favorites = [];

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    _load();
  }

  Future<void> _load() async {
    final api = context.read<ApiService>();
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await api.get('/favorites');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final list = jsonDecode(res.body) as List<dynamic>;
        setState(() {
          _favorites = list.map((e) => e as Map<String, dynamic>).toList();
          _loading = false;
        });
      } else {
        setState(() {
          _error = 'Failed to load favorites: ${res.body}';
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = 'Network error: $e';
          _loading = false;
        });
      }
    }
  }

  Future<void> _unfavorite(String ticker) async {
    final index = _favorites.indexWhere((f) => f['ticker'] == ticker);
    if (index == -1) return;
    final removed = _favorites[index];
    final messenger = ScaffoldMessenger.of(context);
    final api = context.read<ApiService>();

    setState(() => _favorites.removeAt(index));

    try {
      final res = await api.delete('/favorites/$ticker');
      if (!mounted) return;
      final ok = res.statusCode >= 200 && res.statusCode < 300;
      // A 404 here means the server already agrees it's not favorited
      // (e.g. removed from another tab/device in the meantime) — not a
      // real failure to revert for.
      final alreadyGone = res.statusCode == 404;
      if (!ok && !alreadyGone) {
        setState(() => _favorites.insert(index, removed));
        messenger.showSnackBar(
          SnackBar(
            content: Text('Could not remove $ticker'),
            backgroundColor: AppTheme.danger,
          ),
        );
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _favorites.insert(index, removed));
      messenger.showSnackBar(
        SnackBar(content: Text('Network error: $e'), backgroundColor: AppTheme.danger),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    // Colors.transparent + no backgroundColor override: this screen is a
    // shell-nested tab (like dashboard_screen.dart), so _MainShell's own
    // gradient paints behind it — unlike the top-level routes (stock
    // detail, search, trade history) which use the theme's flat
    // background instead.
    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(title: const Text('Favorites')),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    final t = Theme.of(context).textTheme;

    if (_loading && _favorites.isEmpty && _error == null) {
      return const Center(child: CircularProgressIndicator(color: AppTheme.accent));
    }

    if (_error != null && _favorites.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.error_outline_rounded, size: 40, color: AppTheme.danger),
              const SizedBox(height: 16),
              Text(
                _error!,
                textAlign: TextAlign.center,
                style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
              ),
              const SizedBox(height: 16),
              FilledButton(onPressed: _load, child: const Text('Try again')),
            ],
          ),
        ),
      );
    }

    if (_favorites.isEmpty) {
      // Pull-to-refresh still works from the empty state (this is
      // expected to be the common first-run case, not an error) — the
      // RefreshIndicator needs a scrollable child to trigger from, hence
      // the CustomScrollView wrapping a centered sliver rather than a
      // plain Center.
      return RefreshIndicator(
        color: AppTheme.accent,
        onRefresh: _load,
        child: CustomScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            SliverFillRemaining(
              hasScrollBody: false,
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.all(32),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(
                        Icons.favorite_border,
                        size: 40,
                        color: AppTheme.textSecondaryOf(context),
                      ),
                      const SizedBox(height: 16),
                      Text(
                        'No favorites yet',
                        style: t.titleMedium?.copyWith(fontWeight: FontWeight.w600),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        'Tap the heart icon on any stock\'s page to add it here.',
                        textAlign: TextAlign.center,
                        style: t.bodyMedium?.copyWith(
                          color: AppTheme.textSecondaryOf(context),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      );
    }

    return RefreshIndicator(
      color: AppTheme.accent,
      onRefresh: _load,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 100),
        itemCount: _favorites.length,
        itemBuilder: (context, i) => _FavoriteTile(
          _favorites[i],
          onUnfavorite: _unfavorite,
        ),
      ),
    );
  }
}

class _FavoriteTile extends StatelessWidget {
  const _FavoriteTile(this.f, {required this.onUnfavorite});

  final Map<String, dynamic> f;
  final void Function(String ticker) onUnfavorite;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final ticker = f['ticker'] as String;
    final companyName = f['company_name'] as String?;
    final price = (f['price'] as num?)?.toDouble();
    final change = (f['change'] as num?)?.toDouble();
    final changePct = (f['change_pct'] as num?)?.toDouble();
    final positive = (change ?? 0) >= 0;

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        onTap: () => context.push('/stock/$ticker'),
        leading: CircleAvatar(
          backgroundColor: AppTheme.surface2Of(context),
          child: Text(
            ticker.length >= 2 ? ticker.substring(0, 2) : ticker,
            style: TextStyle(
              fontWeight: FontWeight.w700,
              fontSize: 13,
              color: AppTheme.textPrimaryOf(context),
            ),
          ),
        ),
        title: Text(ticker, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: companyName != null
            ? Text(
                companyName,
                overflow: TextOverflow.ellipsis,
                style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
              )
            : null,
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  price != null ? '\$${price.toStringAsFixed(2)}' : '—',
                  style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600),
                ),
                if (change != null && changePct != null)
                  Text(
                    '${positive ? '+' : ''}${changePct.toStringAsFixed(2)}%',
                    style: t.bodySmall?.copyWith(
                      color: positive ? AppTheme.accent : AppTheme.danger,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
              ],
            ),
            IconButton(
              tooltip: 'Remove from favorites',
              onPressed: () => onUnfavorite(ticker),
              icon: const Icon(Icons.favorite, color: AppTheme.danger),
            ),
          ],
        ),
      ),
    );
  }
}
