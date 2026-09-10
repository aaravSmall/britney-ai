import 'package:britney_ai/screens/login_screen.dart';
import 'package:britney_ai/services/auth_controller.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

/// AuthController's constructor guards every Firebase call behind
/// `Firebase.apps.isNotEmpty` (see auth_controller.dart), which is false
/// in a widget test (Firebase.initializeApp() is never called here) — so
/// this instantiates cleanly with no Firebase mocking needed.
void main() {
  testWidgets(
    'Try demo button is present, tappable, and actually enables demo mode',
    (tester) async {
      final auth = AuthController();
      final router = GoRouter(
        initialLocation: '/',
        routes: [
          GoRoute(path: '/', builder: (context, state) => const LoginScreen()),
          GoRoute(
            path: '/home',
            builder: (context, state) => const Scaffold(body: Text('HOME_STUB')),
          ),
        ],
      );

      await tester.pumpWidget(
        ChangeNotifierProvider<AuthController>.value(
          value: auth,
          child: MaterialApp.router(routerConfig: router),
        ),
      );

      // Renders the expected entry point.
      expect(find.text('britney.ai'), findsOneWidget);
      final demoButton = find.widgetWithText(OutlinedButton, 'Try demo (local backend)');
      expect(demoButton, findsOneWidget);
      expect(auth.demoMode, isFalse);

      // Tapping it is not cosmetic — it flips real controller state and
      // navigates, both asserted below. A renamed/removed button, a
      // disabled button, or a broken onPressed would fail this.
      await tester.tap(demoButton);
      await tester.pumpAndSettle();

      expect(auth.demoMode, isTrue);
      expect(find.text('HOME_STUB'), findsOneWidget);
    },
  );
}
