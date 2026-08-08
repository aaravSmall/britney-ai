import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/auth_controller.dart';
import '../services/theme_controller.dart';
import '../services/time_format_controller.dart';
import '../theme/app_theme.dart';
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
