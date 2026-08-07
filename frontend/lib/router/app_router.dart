import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../screens/account_screen.dart';
import '../screens/chat_screen.dart';
import '../screens/dashboard_screen.dart';
import '../screens/login_screen.dart';
import '../screens/onboarding_screen.dart';
import '../screens/recommendations_screen.dart';
import '../screens/stock_detail_screen.dart';
import '../screens/trade_history_screen.dart';
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
      GoRoute(
        // A path param (not `extra`) so this survives a Flutter-web page
        // refresh/deep link, which reconstructs routes from the URL alone
        // and would otherwise crash on a null `extra`.
        path: '/trade-history/:portfolioId',
        builder: (_, state) {
          final id = int.tryParse(state.pathParameters['portfolioId'] ?? '');
          return TradeHistoryScreen(portfolioId: id);
        },
      ),
      GoRoute(
        // Standalone by design (see stock_detail_screen.dart) — search
        // results, the favorites list, and any future entry point all
        // push this same route with just a ticker, same as
        // /trade-history/:portfolioId above.
        path: '/stock/:ticker',
        builder: (_, state) {
          final ticker = state.pathParameters['ticker'] ?? '';
          return StockDetailScreen(ticker: ticker);
        },
      ),
      // StatefulShellRoute.indexedStack keeps each tab's widget tree (and
      // state — dashboard data, scroll position, chat history, etc.) alive
      // in an IndexedStack instead of rebuilding it from scratch on every
      // switch. That rebuild cost — a fresh network fetch plus a full new
      // widget tree, landing right in the middle of the switch animation —
      // was the remaining source of jank after the previous fix (which only
      // addressed a competing-transition bug, not this one).
      StatefulShellRoute.indexedStack(
        builder: (context, state, navigationShell) =>
            _MainShell(navigationShell: navigationShell),
        branches: [
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/home', builder: (_, __) => const DashboardScreen()),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/recommendations',
                builder: (_, __) => const RecommendationsScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/chat', builder: (_, __) => const ChatScreen()),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/account', builder: (_, __) => const AccountScreen()),
            ],
          ),
        ],
      ),
    ],
  );
}

class _MainShell extends StatefulWidget {
  const _MainShell({required this.navigationShell});

  final StatefulNavigationShell navigationShell;

  @override
  State<_MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<_MainShell>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 280),
  )..value = 1.0;
  late final Animation<double> _curved = CurvedAnimation(
    parent: _controller,
    curve: Curves.easeOutCubic,
  );
  bool _forward = true;

  @override
  void didUpdateWidget(covariant _MainShell oldWidget) {
    super.didUpdateWidget(oldWidget);
    final oldIndex = oldWidget.navigationShell.currentIndex;
    final newIndex = widget.navigationShell.currentIndex;
    if (oldIndex != newIndex) {
      // Right-of-current tabs slide in from the right; left-of-current tabs
      // slide in from the left.
      _forward = newIndex > oldIndex;
      _controller.forward(from: 0);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final index = widget.navigationShell.currentIndex;

    return Scaffold(
      body: DecoratedBox(
        decoration:
            BoxDecoration(gradient: AppTheme.scaffoldGradientOf(context)),
        child: AnimatedBuilder(
          animation: _curved,
          builder: (context, child) {
            final dx = (1 - _curved.value) * (_forward ? 1.0 : -1.0);
            return Opacity(
              opacity: _curved.value.clamp(0.0, 1.0),
              child: FractionalTranslation(
                translation: Offset(dx, 0),
                child: child,
              ),
            );
          },
          child: widget.navigationShell,
        ),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => widget.navigationShell.goBranch(
          i,
          initialLocation: i == widget.navigationShell.currentIndex,
        ),
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
