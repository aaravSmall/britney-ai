import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../services/auth_controller.dart';
import '../theme/app_theme.dart';

class LoginScreen extends StatelessWidget {
  const LoginScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final t = Theme.of(context).textTheme;

    return Scaffold(
      body: DecoratedBox(
        decoration: BoxDecoration(gradient: AppTheme.scaffoldGradient),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Column(
              children: [
                const Spacer(flex: 2),
                Container(
                  width: 88,
                  height: 88,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [
                        AppTheme.accent.withValues(alpha: 0.25),
                        AppTheme.accent.withValues(alpha: 0.08),
                      ],
                    ),
                    border: Border.all(
                      color: AppTheme.accent.withValues(alpha: 0.35),
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: AppTheme.accent.withValues(alpha: 0.15),
                        blurRadius: 32,
                        spreadRadius: 0,
                      ),
                    ],
                  ),
                  child: const Icon(
                    Icons.show_chart_rounded,
                    size: 44,
                    color: AppTheme.accent,
                  ),
                ),
                const SizedBox(height: 28),
                Text(
                  'britney.ai',
                  textAlign: TextAlign.center,
                  style: t.headlineMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                    letterSpacing: -0.8,
                  ),
                ),
                const SizedBox(height: 12),
                Text(
                  'Your AI investing copilot — no minimums, no paywalls.',
                  textAlign: TextAlign.center,
                  style: t.bodyLarge?.copyWith(
                    color: AppTheme.textSecondary,
                    height: 1.45,
                  ),
                ),
                const Spacer(flex: 3),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        FilledButton.icon(
                          onPressed: () async {
                            final err = await auth.signInWithGoogle();
                            if (context.mounted && err != null) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(content: Text(err)),
                              );
                            }
                          },
                          icon: const Icon(Icons.login_rounded, size: 22),
                          label: const Text('Continue with Google'),
                        ),
                        const SizedBox(height: 12),
                        OutlinedButton(
                          onPressed: () {
                            auth.enableDemoMode();
                            context.go('/home');
                          },
                          child: const Text('Try demo (local backend)'),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Text(
                  'Demo uses AUTH_DISABLED on the API. No real Google token.',
                  textAlign: TextAlign.center,
                  style: t.bodySmall?.copyWith(
                    color: AppTheme.textSecondary,
                  ),
                ),
                const SizedBox(height: 24),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
