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
  final tabDirection = _TabDirection();

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
          _tabRoute(
            path: '/home',
            index: 0,
            tabDirection: tabDirection,
            builder: (_, __) => const DashboardScreen(),
          ),
          _tabRoute(
            path: '/recommendations',
            index: 1,
            tabDirection: tabDirection,
            builder: (_, __) => const RecommendationsScreen(),
          ),
          _tabRoute(
            path: '/chat',
            index: 2,
            tabDirection: tabDirection,
            builder: (_, __) => const ChatScreen(),
          ),
          _tabRoute(
            path: '/account',
            index: 3,
            tabDirection: tabDirection,
            builder: (_, __) => const AccountScreen(),
          ),
        ],
      ),
    ],
  );
}

/// Tracks the last-visited bottom-tab index across navigations so a route's
/// [pageBuilder] knows which way to slide when it's built.
class _TabDirection {
  int lastIndex = 0;
}

/// A bottom-tab route that slides in from the right when moving to a tab
/// further right, and from the left when moving to a tab further left.
///
/// This drives the transition through go_router's own nested [Navigator]
/// (which [ShellRoute] creates for its sub-routes) via [CustomTransitionPage],
/// instead of layering a second, independent [AnimatedSwitcher] on top of it.
/// Doing both at once was the cause of the previous stutter: the Navigator's
/// own page-replace transition and the hand-rolled outer one were fighting
/// over the same frames, and the outer one force-recreated the whole nested
/// Navigator on every tab switch (new `ValueKey`), cutting its in-flight
/// transition off mid-animation.
GoRoute _tabRoute({
  required String path,
  required int index,
  required _TabDirection tabDirection,
  required Widget Function(BuildContext, GoRouterState) builder,
}) {
  return GoRoute(
    path: path,
    pageBuilder: (context, state) {
      final forward = index >= tabDirection.lastIndex;
      tabDirection.lastIndex = index;
      return CustomTransitionPage(
        key: state.pageKey,
        transitionDuration: const Duration(milliseconds: 280),
        reverseTransitionDuration: const Duration(milliseconds: 280),
        child: RepaintBoundary(child: builder(context, state)),
        transitionsBuilder: (context, animation, secondaryAnimation, child) {
          final curved = CurvedAnimation(
            parent: animation,
            curve: Curves.easeOutCubic,
          );
          final slide = Tween<Offset>(
            begin: forward ? const Offset(1.0, 0.0) : const Offset(-1.0, 0.0),
            end: Offset.zero,
          ).animate(curved);
          return FadeTransition(
            opacity: curved,
            child: SlideTransition(position: slide, child: child),
          );
        },
      );
    },
  );
}

class _MainShell extends StatelessWidget {
  const _MainShell({required this.child});

  final Widget child;

  static int _tabIndex(String path) {
    if (path.startsWith('/recommendations')) return 1;
    if (path.startsWith('/chat')) return 2;
    if (path.startsWith('/account')) return 3;
    return 0;
  }

  @override
  Widget build(BuildContext context) {
    final loc = GoRouterState.of(context).uri.path;
    final index = _tabIndex(loc);

    return Scaffold(
      body: DecoratedBox(
        decoration:
            BoxDecoration(gradient: AppTheme.scaffoldGradientOf(context)),
        child: child,
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
