import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/auth_controller.dart';
import '../services/theme_controller.dart';
import '../services/time_format_controller.dart';
import '../theme/app_theme.dart';
import '../utils/format.dart';
import '../widgets/add_auto_invest_sheet.dart';
import '../widgets/cash_amount_sheet.dart';

/// Resolves the signed-in user's own portfolio id (as opposed to one of
/// the agent's three model portfolios) and opens the deposit/withdraw
/// sheet for it. Looked up fresh on each tap rather than cached on the
/// screen's state, since AccountScreen otherwise carries no portfolio
/// data at all — same "fetch what you need when you need it" approach
/// buy_sell_bottom_sheet.dart's price lookup uses.
Future<void> _openCashSheet(BuildContext context, String action) async {
  final messenger = ScaffoldMessenger.of(context);
  final api = context.read<ApiService>();
  try {
    final res = await api.get('/portfolios');
    if (res.statusCode != 200) {
      messenger.showSnackBar(
        SnackBar(content: Text('Could not load your portfolio (${res.statusCode})')),
      );
      return;
    }
    final list = (jsonDecode(res.body) as List).cast<Map<String, dynamic>>();
    final mine = list.where((p) => p['owner_type'] == 'user').toList();
    if (mine.isEmpty) {
      messenger.showSnackBar(const SnackBar(content: Text('No portfolio found for your account')));
      return;
    }
    if (!context.mounted) return;
    await showCashAmountSheet(context, portfolioId: mine.first['id'] as int, action: action);
  } catch (e) {
    messenger.showSnackBar(SnackBar(content: Text('Network error: $e')));
  }
}

class AccountScreen extends StatelessWidget {
  const AccountScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final t = Theme.of(context).textTheme;

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Account',
              style: t.titleLarge?.copyWith(fontWeight: FontWeight.w600),
            ),
            Text(
              'Profile & security',
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
          ],
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 100),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Row(
                children: [
                  CircleAvatar(
                    radius: 32,
                    backgroundColor: AppTheme.accent.withValues(alpha: 0.15),
                    child: Text(
                      auth.displayName.isNotEmpty
                          ? auth.displayName[0].toUpperCase()
                          : '?',
                      style: const TextStyle(
                        color: AppTheme.accent,
                        fontSize: 28,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          auth.displayName,
                          style: t.titleMedium?.copyWith(
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          auth.displayEmail ?? '',
                          style: t.bodySmall?.copyWith(
                            color: AppTheme.textSecondaryOf(context),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          Text(
            'Profile',
            style: t.titleSmall?.copyWith(
              fontWeight: FontWeight.w600,
              color: AppTheme.textSecondaryOf(context),
            ),
          ),
          const SizedBox(height: 8),
          Card(
            child: ListTile(
              leading: const Icon(Icons.tune_rounded),
              title: const Text('Investment profile'),
              subtitle: Text(
                'Risk, goals, and time horizon',
                style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
              ),
              trailing: const Icon(Icons.chevron_right_rounded),
              onTap: () => context.push('/onboarding'),
            ),
          ),
          const SizedBox(height: 20),
          Text(
            'Cash',
            style: t.titleSmall?.copyWith(
              fontWeight: FontWeight.w600,
              color: AppTheme.textSecondaryOf(context),
            ),
          ),
          const SizedBox(height: 8),
          Card(
            child: Column(
              children: [
                ListTile(
                  leading: const Icon(Icons.add_circle_outline_rounded),
                  title: const Text('Add funds'),
                  subtitle: Text(
                    'Simulated cash, paper trading only',
                    style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                  ),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: () => _openCashSheet(context, 'deposit'),
                ),
                const Divider(height: 1),
                ListTile(
                  leading: const Icon(Icons.remove_circle_outline_rounded),
                  title: const Text('Withdraw funds'),
                  subtitle: Text(
                    'Simulated cash, paper trading only',
                    style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                  ),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: () => _openCashSheet(context, 'withdraw'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          const _AutoInvestSection(),
          const SizedBox(height: 20),
          Text(
            'Settings',
            style: t.titleSmall?.copyWith(
              fontWeight: FontWeight.w600,
              color: AppTheme.textSecondaryOf(context),
            ),
          ),
          const SizedBox(height: 8),
          Card(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(Icons.palette_outlined,
                          color: AppTheme.textSecondaryOf(context)),
                      const SizedBox(width: 16),
                      Text(
                        'Appearance',
                        style:
                            t.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  const SizedBox(
                    width: double.infinity,
                    child: _AppearanceSelector(),
                  ),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      Icon(Icons.schedule_rounded,
                          color: AppTheme.textSecondaryOf(context)),
                      const SizedBox(width: 16),
                      Text(
                        'Time format',
                        style:
                            t.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  const SizedBox(
                    width: double.infinity,
                    child: _TimeFormatSelector(),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: Column(
              children: [
                ListTile(
                  leading: const Icon(Icons.notifications_outlined),
                  title: const Text('Notifications'),
                  subtitle: Text(
                    'Coming soon',
                    style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                  ),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: () {},
                ),
                const Divider(height: 1),
                ListTile(
                  leading: const Icon(Icons.lock_outline_rounded),
                  title: const Text('App lock'),
                  subtitle: Text(
                    'Coming soon',
                    style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                  ),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: () {},
                ),
                const Divider(height: 1),
                ListTile(
                  leading: const Icon(Icons.attach_money_rounded),
                  title: const Text('Currency'),
                  subtitle: Text(
                    'USD',
                    style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                  ),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: () {},
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          Text(
            'Session',
            style: t.titleSmall?.copyWith(
              fontWeight: FontWeight.w600,
              color: AppTheme.textSecondaryOf(context),
            ),
          ),
          const SizedBox(height: 8),
          FilledButton.tonal(
            onPressed: () async {
              await auth.signOut();
              if (context.mounted) context.go('/login');
            },
            style: FilledButton.styleFrom(
              foregroundColor: AppTheme.danger,
              backgroundColor: AppTheme.danger.withValues(alpha: 0.12),
            ),
            child: const Text('Sign out'),
          ),
          const SizedBox(height: 24),
          Center(
            child: Text(
              'britney.ai v0.1.0',
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
          ),
        ],
      ),
    );
  }
}

class _AppearanceSelector extends StatelessWidget {
  const _AppearanceSelector();

  @override
  Widget build(BuildContext context) {
    final theme = context.watch<ThemeController>();
    return SegmentedButton<ThemeMode>(
      segments: const [
        ButtonSegment(
          value: ThemeMode.system,
          icon: Icon(Icons.brightness_auto_rounded, size: 18),
          label: Text('Auto'),
        ),
        ButtonSegment(
          value: ThemeMode.light,
          icon: Icon(Icons.light_mode_rounded, size: 18),
          label: Text('Light'),
        ),
        ButtonSegment(
          value: ThemeMode.dark,
          icon: Icon(Icons.dark_mode_rounded, size: 18),
          label: Text('Dark'),
        ),
      ],
      selected: {theme.mode},
      showSelectedIcon: false,
      onSelectionChanged: (s) => theme.setMode(s.first),
    );
  }
}

class _TimeFormatSelector extends StatelessWidget {
  const _TimeFormatSelector();

  @override
  Widget build(BuildContext context) {
    final timeFormat = context.watch<TimeFormatController>();
    return SegmentedButton<bool>(
      segments: const [
        ButtonSegment(value: false, label: Text('12-hour')),
        ButtonSegment(value: true, label: Text('24-hour')),
      ],
      selected: {timeFormat.use24Hour},
      showSelectedIcon: false,
      onSelectionChanged: (s) => timeFormat.setUse24Hour(s.first),
    );
  }
}

const Map<String, String> _intervalLabels = {
  'daily': 'Daily',
  'weekly': 'Weekly',
  'monthly': 'Monthly',
};

/// Lists the signed-in user's recurring auto-invest schedules and lets
/// them add/toggle/delete one. Unrelated to the "Auto-invest (paper)"
/// switch on the dashboard (User.auto_invest_enabled, which only gates
/// simulated-vs-live execution on manually submitted trades) — that
/// toggle and its label are untouched by this section.
class _AutoInvestSection extends StatefulWidget {
  const _AutoInvestSection();

  @override
  State<_AutoInvestSection> createState() => _AutoInvestSectionState();
}

class _AutoInvestSectionState extends State<_AutoInvestSection> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _schedules = [];

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
      final res = await api.get('/auto-invest/schedules');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final list = jsonDecode(res.body) as List<dynamic>;
        setState(() {
          _schedules = list.map((e) => e as Map<String, dynamic>).toList();
          _loading = false;
        });
      } else {
        setState(() {
          _error = 'Could not load schedules (${res.statusCode}).';
          _loading = false;
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Network error: $e';
        _loading = false;
      });
    }
  }

  Future<void> _addSchedule() async {
    final created = await showAddAutoInvestSheet(context);
    if (created != null && mounted) {
      setState(() => _schedules = [created, ..._schedules]);
    }
  }

  Future<void> _toggleEnabled(Map<String, dynamic> schedule, bool value) async {
    final api = context.read<ApiService>();
    final messenger = ScaffoldMessenger.of(context);
    setState(() {
      schedule['enabled'] = value;
    });
    final res = await api.patch(
      '/auto-invest/schedules/${schedule['id']}',
      {'enabled': value},
    );
    if (res.statusCode != 200 && mounted) {
      setState(() => schedule['enabled'] = !value);
      messenger.showSnackBar(
        SnackBar(content: Text('Could not update schedule (${res.statusCode}).')),
      );
    }
  }

  Future<void> _deleteSchedule(Map<String, dynamic> schedule) async {
    final api = context.read<ApiService>();
    final messenger = ScaffoldMessenger.of(context);
    final res = await api.delete('/auto-invest/schedules/${schedule['id']}');
    if (res.statusCode == 204 && mounted) {
      setState(() => _schedules.removeWhere((s) => s['id'] == schedule['id']));
    } else if (mounted) {
      messenger.showSnackBar(
        SnackBar(content: Text('Could not delete schedule (${res.statusCode}).')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final use24Hour = context.watch<TimeFormatController>().use24Hour;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text(
              'Auto-invest',
              style: t.titleSmall?.copyWith(
                fontWeight: FontWeight.w600,
                color: AppTheme.textSecondaryOf(context),
              ),
            ),
            const Spacer(),
            IconButton(
              icon: const Icon(Icons.add_circle_outline_rounded),
              tooltip: 'New schedule',
              onPressed: _addSchedule,
            ),
          ],
        ),
        if (_loading)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: LinearProgressIndicator(minHeight: 2, color: AppTheme.accent),
          )
        else if (_error != null)
          Card(
            child: ListTile(
              leading: const Icon(Icons.error_outline_rounded, color: AppTheme.danger),
              title: Text(_error!),
              trailing: TextButton(onPressed: _load, child: const Text('Retry')),
            ),
          )
        else if (_schedules.isEmpty)
          Card(
            child: ListTile(
              leading: const Icon(Icons.auto_awesome_rounded),
              title: const Text('No schedules yet'),
              subtitle: Text(
                'Recurring buys of a fixed dollar amount, simulated cash only.',
                style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
              ),
            ),
          )
        else
          Card(
            child: Column(
              children: [
                for (final schedule in _schedules) ...[
                  if (schedule != _schedules.first) const Divider(height: 1),
                  ListTile(
                    leading: const Icon(Icons.auto_awesome_rounded),
                    title: Text(
                      '${schedule['ticker']} · \$${(schedule['amount'] as num).toStringAsFixed(2)} '
                      '${_intervalLabels[schedule['interval']] ?? schedule['interval']}',
                    ),
                    subtitle: Text(
                      schedule['last_executed_at'] == null
                          ? 'Never run yet'
                          : 'Last run: ${formatTradeTime(DateTime.parse(schedule['last_executed_at'] as String).toLocal(), use24Hour)}',
                      style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                    ),
                    trailing: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Switch(
                          value: schedule['enabled'] as bool,
                          activeThumbColor: AppTheme.accent,
                          onChanged: (v) => _toggleEnabled(schedule, v),
                        ),
                        IconButton(
                          icon: const Icon(Icons.delete_outline_rounded, color: AppTheme.danger),
                          tooltip: 'Delete schedule',
                          onPressed: () => _deleteSchedule(schedule),
                        ),
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
      ],
    );
  }
}
