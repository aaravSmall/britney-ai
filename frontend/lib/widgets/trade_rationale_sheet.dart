import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/timezone_controller.dart';
import '../theme/app_theme.dart';
import '../utils/format.dart' show toDisplayZone;

const List<String> _monthAbbr = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// Opens the "More info" view for an AI-badged (source == "agent") trade —
/// the reasoning, confidence, sentiment, and full news citations behind
/// that specific trade decision (GET /portfolios/{id}/trades/{id}/decision),
/// as a modal bottom sheet. Shared by trade_history_screen.dart's
/// _TradeTile and stock_detail_screen.dart's _RecentTradeRow — both AI-
/// trade surfaces this app has — rather than each screen owning its own
/// copy of the fetch + render logic (same reasoning as
/// buy_sell_bottom_sheet.dart's showBuySellSheet()).
Future<void> showTradeRationaleSheet(
  BuildContext context, {
  required int portfolioId,
  required int tradeId,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (_) => _TradeRationaleSheet(portfolioId: portfolioId, tradeId: tradeId),
  );
}

class _TradeRationaleSheet extends StatefulWidget {
  const _TradeRationaleSheet({required this.portfolioId, required this.tradeId});

  final int portfolioId;
  final int tradeId;

  @override
  State<_TradeRationaleSheet> createState() => _TradeRationaleSheetState();
}

class _TradeRationaleSheetState extends State<_TradeRationaleSheet> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _decision;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final api = context.read<ApiService>();
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await api.get(
        '/portfolios/${widget.portfolioId}/trades/${widget.tradeId}/decision',
      );
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() {
          _decision = jsonDecode(res.body) as Map<String, dynamic>;
          _loading = false;
        });
      } else if (res.statusCode == 404) {
        setState(() {
          _error = 'No AI rationale recorded for this trade.';
          _loading = false;
        });
      } else {
        setState(() {
          _error = 'Failed to load rationale (${res.statusCode}).';
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

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;

    return DraggableScrollableSheet(
      initialChildSize: 0.75,
      minChildSize: 0.4,
      maxChildSize: 0.92,
      expand: false,
      builder: (context, scrollController) {
        return Padding(
          padding: EdgeInsets.only(
            left: 20,
            right: 20,
            top: 20,
            bottom: MediaQuery.of(context).viewInsets.bottom + 20,
          ),
          child: Column(
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
              Row(
                children: [
                  const Icon(Icons.auto_awesome_rounded, color: AppTheme.accent, size: 20),
                  const SizedBox(width: 8),
                  Text(
                    'AI trade rationale',
                    style: t.titleMedium?.copyWith(fontWeight: FontWeight.w600),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Expanded(child: _buildBody(t, scrollController)),
            ],
          ),
        );
      },
    );
  }

  Widget _buildBody(TextTheme t, ScrollController scrollController) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: AppTheme.accent));
    }
    if (_error != null) {
      return Center(
        child: Text(
          _error!,
          textAlign: TextAlign.center,
          style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
        ),
      );
    }

    final d = _decision!;
    final decision = d['decision'] as String;
    final reasoning = d['reasoning'] as String;
    final confidence = (d['confidence'] as num).toDouble();
    final sentimentScore = (d['sentiment_score'] as num?)?.toDouble();
    final newsSource = d['news_source'] as String?;
    final articles = (d['articles'] as List<dynamic>)
        .map((e) => e as Map<String, dynamic>)
        .toList();

    return ListView(
      controller: scrollController,
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: Column(
              children: [
                _RationaleStatRow('Decision', decision.toUpperCase()),
                _RationaleStatRow('Confidence', '${(confidence * 100).toStringAsFixed(0)}%'),
                _RationaleStatRow(
                  'Sentiment score',
                  sentimentScore != null
                      ? '${sentimentScore >= 0 ? '+' : ''}${sentimentScore.toStringAsFixed(2)}'
                      : '—',
                  isLast: newsSource == null,
                ),
                if (newsSource != null)
                  _RationaleStatRow('News source', newsSource, isLast: true),
              ],
            ),
          ),
        ),
        const SizedBox(height: 20),
        Text('Reasoning', style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        Text(reasoning, style: t.bodyMedium?.copyWith(height: 1.4)),
        const SizedBox(height: 20),
        Text(
          'News considered (${articles.length})',
          style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 8),
        if (articles.isEmpty)
          Text(
            'No article citations recorded for this decision.',
            style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
          )
        else
          for (final a in articles) _ArticleCitationCard(a),
      ],
    );
  }
}

class _RationaleStatRow extends StatelessWidget {
  const _RationaleStatRow(this.label, this.value, {this.isLast = false});

  final String label;
  final String value;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 10),
      decoration: isLast
          ? null
          : BoxDecoration(
              border: Border(bottom: BorderSide(color: AppTheme.borderSubtleOf(context))),
            ),
      child: Row(
        children: [
          Text(label, style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context))),
          const Spacer(),
          Flexible(
            child: Text(
              value,
              textAlign: TextAlign.end,
              style: t.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }
}

class _ArticleCitationCard extends StatelessWidget {
  const _ArticleCitationCard(this.article);

  final Map<String, dynamic> article;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final headline = article['headline'] as String? ?? '';
    final source = article['source'] as String? ?? '';
    final sentiment = article['sentiment'] as String? ?? 'neutral';
    final confidence = (article['confidence'] as num?)?.toDouble() ?? 0.0;
    final reasoning = article['reasoning'] as String? ?? '';
    final publishedAtUtc = DateTime.tryParse(article['published_at'] as String? ?? '');
    final publishedAt = publishedAtUtc != null
        ? toDisplayZone(publishedAtUtc, context.watch<TimezoneController>().location)
        : null;

    final sentimentColor = sentiment == 'bullish'
        ? AppTheme.accent
        : sentiment == 'bearish'
            ? AppTheme.danger
            : AppTheme.textSecondaryOf(context);

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              headline.isNotEmpty ? headline : '(no headline)',
              style: t.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 4),
            Text(
              [source, if (publishedAt != null) _formatDate(publishedAt)]
                  .where((s) => s.isNotEmpty)
                  .join(' · '),
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                  decoration: BoxDecoration(
                    color: sentimentColor.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(
                    sentiment.toUpperCase(),
                    style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: sentimentColor),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  '${(confidence * 100).toStringAsFixed(0)}% confidence',
                  style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                ),
              ],
            ),
            if (reasoning.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(reasoning, style: t.bodySmall?.copyWith(height: 1.35)),
            ],
          ],
        ),
      ),
    );
  }
}

String _formatDate(DateTime d) => '${_monthAbbr[d.month - 1]} ${d.day}';
