import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'firebase_options.dart';
import 'router/app_router.dart';
import 'services/api_service.dart';
import 'services/auth_controller.dart';
import 'services/theme_controller.dart';
import 'theme/app_theme.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    await Firebase.initializeApp(
      options: DefaultFirebaseOptions.currentPlatform,
    );
  } catch (e, st) {
    debugPrint('Firebase init failed (use Demo mode + AUTH_DISABLED backend): $e');
    debugPrint('$st');
  }

  runApp(const BritneyApp());
}

class BritneyApp extends StatefulWidget {
  const BritneyApp({super.key});

  @override
  State<BritneyApp> createState() => _BritneyAppState();
}

class _BritneyAppState extends State<BritneyApp> {
  late final AuthController _auth = AuthController();
  late final ThemeController _theme = ThemeController();
  late final GoRouter _router = createRouter(_auth);

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AuthController>.value(value: _auth),
        ChangeNotifierProvider<ThemeController>.value(value: _theme),
        ProxyProvider<AuthController, ApiService>(
          update: (_, auth, __) => ApiService(getIdToken: auth.getIdToken),
        ),
      ],
      child: AnimatedBuilder(
        animation: _theme,
        builder: (context, _) => MaterialApp.router(
          title: 'britney.ai',
          debugShowCheckedModeBanner: false,
          theme: AppTheme.light(),
          darkTheme: AppTheme.dark(),
          themeMode: _theme.mode,
          routerConfig: _router,
        ),
      ),
    );
  }
}
