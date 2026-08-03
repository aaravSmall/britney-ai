import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'firebase_options.dart';
import 'router/app_router.dart';
import 'services/api_service.dart';
import 'services/auth_controller.dart';
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
  late final GoRouter _router = createRouter(_auth);

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AuthController>.value(value: _auth),
        ProxyProvider<AuthController, ApiService>(
          update: (_, auth, __) => ApiService(getIdToken: auth.getIdToken),
        ),
      ],
      child: MaterialApp.router(
        title: 'britney.ai',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.dark(),
        routerConfig: _router,
      ),
    );
  }
}
