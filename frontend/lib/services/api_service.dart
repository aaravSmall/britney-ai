import 'dart:convert';

import 'package:http/http.dart' as http;

import 'api_config.dart';

/// REST client for britney.ai backend. All secrets stay server-side.
class ApiService {
  ApiService({required this.getIdToken});

  final Future<String?> Function() getIdToken;

  Future<Map<String, String>> _headers() async {
    final token = await getIdToken();
    return {
      'Content-Type': 'application/json',
      if (token != null && token.isNotEmpty) 'Authorization': 'Bearer $token',
    };
  }

  Future<http.Response> get(String path) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}$path');
    return http.get(uri, headers: await _headers());
  }

  Future<http.Response> post(String path, Object? body) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}$path');
    return http.post(
      uri,
      headers: await _headers(),
      body: body == null ? null : jsonEncode(body),
    );
  }

  Future<http.Response> patch(String path, Object? body) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}$path');
    return http.patch(
      uri,
      headers: await _headers(),
      body: body == null ? null : jsonEncode(body),
    );
  }

  Future<http.Response> delete(String path) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}$path');
    return http.delete(uri, headers: await _headers());
  }
}
