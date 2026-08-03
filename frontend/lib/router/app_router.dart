import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../screens/account_screen.dart';
import '../screens/chat_screen.dart';
import '../screens/dashboard_screen.dart';
import '../screens/login_screen.dart';
import '../screens/onboarding_screen.dart';
import '../screens/recommendations_screen.dart';
import '../services/auth_controller.dart';
import '../theme/app_theme.dart';

/// Builds router with [auth] so redirects react to sign-in without BuildContext.
GoRouter createRouter(AuthController auth) {
  return GoRouter(
    refreshListenable: auth,
    initialLocation: '/login',
    redirect: (context, state) {
      final loggedIn = auth.isSignedIn;
      final loc = state.matchedLocation;
      final loggingIn = loc == '/login';
      if (!loggedIn && !loggingIn) return '/login';
      if (loggedIn && loggingIn) return '/home';
      return null;
    },
    routes: [
      GoRoute(
        path: '/login',
        builder: (_, __) => const LoginScreen(),
      ),
      GoRoute(
        path: '/onboarding',
        builder: (_, __) => const OnboardingScreen(),
      ),
      ShellRoute(
        builder: (context, state, child) => _MainShell(child: child),
        routes: [
          GoRoute(
            path: '/home',
            builder: (_, __) => const DashboardScreen(),
          ),
          GoRoute(
            path: '/recommendations',
            builder: (_, __) => const RecommendationsScreen(),
          ),
          GoRoute(
            path: '/chat',
            builder: (_, __) => const ChatScreen(),
          ),
          GoRoute(
            path: '/account',
            builder: (_, __) => const AccountScreen(),
          ),
        ],
      ),
    ],
  );
}

class _MainShell extends StatefulWidget {
  const _MainShell({required this.child});

  final Widget child;

  @override
  State<_MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<_MainShell> {
  int _lastIndex = 0;
  bool _didSyncIndex = false;
  bool _forward = true;

  static int _tabIndex(String path) {
    if (path.startsWith('/recommendations')) return 1;
    if (path.startsWith('/chat')) return 2;
    if (path.startsWith('/account')) return 3;
    return 0;
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_didSyncIndex) return;
    _lastIndex = _tabIndex(GoRouterState.of(context).uri.path);
    _didSyncIndex = true;
  }

  // Kept as a stable method tear-off (not an inline closure) so its identity
  // doesn't change across rebuilds. AnimatedSwitcher re-triggers the
  // transition for in-flight entries whenever `transitionBuilder` changes
  // identity, which was snapping the slide direction mid-animation.
  Widget _buildTransition(Widget child, Animation<double> animation) {
    final curved = CurvedAnimation(
      parent: animation,
      curve: Curves.easeOutCubic,
    );
    final slide = Tween<Offset>(
      begin: _forward ? const Offset(1.0, 0.0) : const Offset(-1.0, 0.0),
      end: Offset.zero,
    ).animate(curved);
    return FadeTransition(
      opacity: curved,
      child: SlideTransition(position: slide, child: child),
    );
  }

  @override
  Widget build(BuildContext context) {
    final loc = GoRouterState.of(context).uri.path;
    final index = _tabIndex(loc);
    if (index != _lastIndex) {
      // Right-of-current tabs slide in from the right; left-of-current tabs
      // slide in from the left. Updated synchronously (no setState/post-frame
      // delay) so the direction is frozen for the whole transition.
      _forward = index > _lastIndex;
      _lastIndex = index;
    }

    final animatedBody = AnimatedSwitcher(
      duration: const Duration(milliseconds: 280),
      switchInCurve: Curves.easeOutCubic,
      switchOutCurve: Curves.easeOutCubic,
      transitionBuilder: _buildTransition,
      child: KeyedSubtree(
        key: ValueKey<String>(loc),
        child: RepaintBoundary(child: widget.child),
      ),
    );

    return Scaffold(
      body: DecoratedBox(
        decoration: BoxDecoration(gradient: AppTheme.scaffoldGradient),
        child: animatedBody,
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) {
          switch (i) {
            case 0:
              context.go('/home');
              break;
            case 1:
              context.go('/recommendations');
              break;
            case 2:
              context.go('/chat');
              break;
            case 3:
              context.go('/account');
              break;
          }
        },
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.pie_chart_outline),
            selectedIcon: Icon(Icons.pie_chart),
            label: 'Portfolio',
          ),
          NavigationDestination(
            icon: Icon(Icons.auto_awesome_outlined),
            selectedIcon: Icon(Icons.auto_awesome),
            label: 'AI picks',
          ),
          NavigationDestination(
            icon: Icon(Icons.chat_bubble_outline),
            selectedIcon: Icon(Icons.chat_bubble),
            label: 'Chat',
          ),
          NavigationDestination(
            icon: Icon(Icons.person_outline),
            selectedIcon: Icon(Icons.person),
            label: 'Account',
          ),
        ],
      ),
    );
  }
}
