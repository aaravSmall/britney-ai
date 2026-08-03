import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../theme/app_theme.dart';

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  String _risk = 'medium';
  final _goals = TextEditingController(text: 'Build long-term wealth');
  String _horizon = '1_5_years';
  bool _loading = false;

  @override
  void dispose() {
    _goals.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final api = context.read<ApiService>();
    setState(() => _loading = true);
    final res = await api.post('/onboarding', {
      'risk_tolerance': _risk,
      'investment_goals': _goals.text.trim(),
      'time_horizon': _horizon,
    });
    setState(() => _loading = false);
    if (!mounted) return;
    if (res.statusCode >= 200 && res.statusCode < 300) {
      context.go('/home');
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Save failed: ${res.body}')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Tell us about you',
              style: t.titleLarge?.copyWith(fontWeight: FontWeight.w600),
            ),
            Text(
              'Step 1 of 1',
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondary),
            ),
          ],
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(
                    Icons.info_outline_rounded,
                    color: AppTheme.accent.withValues(alpha: 0.9),
                    size: 22,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      'We use this to tune recommendations — not to sell you anything.',
                      style: t.bodyMedium?.copyWith(
                        color: AppTheme.textSecondary,
                        height: 1.45,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 20),
          Text(
            'Risk tolerance',
            style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 10),
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'low', label: Text('Low')),
              ButtonSegment(value: 'medium', label: Text('Medium')),
              ButtonSegment(value: 'high', label: Text('High')),
            ],
            selected: {_risk},
            onSelectionChanged: (s) => setState(() => _risk = s.first),
          ),
          const SizedBox(height: 24),
          Text(
            'Investment goals',
            style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _goals,
            maxLines: 3,
            decoration: const InputDecoration(
              hintText: 'e.g. retirement, first home, learn investing',
            ),
          ),
          const SizedBox(height: 24),
          Text(
            'Time horizon',
            style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 10),
          DropdownButtonFormField<String>(
            key: ValueKey(_horizon),
            initialValue: _horizon,
            items: const [
              DropdownMenuItem(
                value: 'under_1_year',
                child: Text('Under 1 year'),
              ),
              DropdownMenuItem(
                value: '1_5_years',
                child: Text('1–5 years'),
              ),
              DropdownMenuItem(
                value: '5_plus_years',
                child: Text('5+ years'),
              ),
            ],
            onChanged: (v) => setState(() => _horizon = v ?? _horizon),
          ),
          const SizedBox(height: 36),
          FilledButton(
            onPressed: _loading ? null : _submit,
            child: _loading
                ? const SizedBox(
                    height: 22,
                    width: 22,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.black,
                    ),
                  )
                : const Text('Save & continue'),
          ),
        ],
      ),
    );
  }
}
