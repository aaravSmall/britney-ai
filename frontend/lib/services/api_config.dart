/// API base URL — override with:
/// flutter run --dart-define=API_BASE=http://10.0.2.2:8000 (Android emulator)
class ApiConfig {
  static const String baseUrl = String.fromEnvironment(
    'API_BASE',
    defaultValue: 'http://localhost:8000',
  );
}
