import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart';
import 'package:google_sign_in/google_sign_in.dart';

/// Firebase Google auth + demo mode when Firebase is not configured.
class AuthController extends ChangeNotifier {
  AuthController() {
    if (_hasFirebase) {
      FirebaseAuth.instance.authStateChanges().listen((_) => notifyListeners());
    }
  }

  final GoogleSignIn _google = GoogleSignIn(scopes: ['email', 'profile']);

  bool get _hasFirebase => Firebase.apps.isNotEmpty;

  /// Dev backend with AUTH_DISABLED=true accepts any bearer.
  static const String demoToken = 'dev-local-token';

  bool demoMode = false;

  User? get firebaseUser => _hasFirebase ? FirebaseAuth.instance.currentUser : null;

  bool get isSignedIn => demoMode || firebaseUser != null;

  String? get displayEmail =>
      demoMode ? 'demo@britney.ai.local' : firebaseUser?.email;

  String get displayName =>
      demoMode ? 'Demo investor' : (firebaseUser?.displayName ?? 'Investor');

  Future<String?> getIdToken() async {
    if (demoMode) return demoToken;
    return firebaseUser?.getIdToken();
  }

  Future<String?> signInWithGoogle() async {
    if (!_hasFirebase) {
      return 'Firebase is not initialized. Use Demo mode or run flutterfire configure.';
    }
    final g = await _google.signIn();
    if (g == null) return null;
    final auth = await g.authentication;
    final cred = GoogleAuthProvider.credential(
      accessToken: auth.accessToken,
      idToken: auth.idToken,
    );
    await FirebaseAuth.instance.signInWithCredential(cred);
    demoMode = false;
    notifyListeners();
    return null;
  }

  void enableDemoMode() {
    demoMode = true;
    notifyListeners();
  }

  Future<void> signOut() async {
    demoMode = false;
    await _google.signOut();
    if (_hasFirebase) await FirebaseAuth.instance.signOut();
    notifyListeners();
  }
}
