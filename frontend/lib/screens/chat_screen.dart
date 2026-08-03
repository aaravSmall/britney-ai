import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../theme/app_theme.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _controller = TextEditingController();
  final _scroll = ScrollController();
  final List<_Msg> _messages = [];

  bool _loading = false;

  @override
  void dispose() {
    _controller.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _loading) return;
    setState(() {
      _messages.add(_Msg.user(text));
      _controller.clear();
      _loading = true;
    });
    _scrollToEnd();

    final api = context.read<ApiService>();
    final body = {
      'messages': [
        for (final m in _messages)
          {'role': m.role, 'content': m.text},
      ],
    };
    final r = await api.post('/chat', body);
    setState(() => _loading = false);
    if (r.statusCode >= 200 && r.statusCode < 300) {
      final map = jsonDecode(r.body) as Map<String, dynamic>;
      setState(() {
        _messages.add(_Msg.assistant(map['reply'] as String? ?? ''));
      });
    } else {
      setState(() {
        _messages.add(_Msg.assistant('Error: ${r.body}'));
      });
    }
    _scrollToEnd();
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 280),
          curve: Curves.easeOutCubic,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Ask britney.ai',
              style: t.titleLarge?.copyWith(fontWeight: FontWeight.w600),
            ),
            Text(
              'Investing Q&A',
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
          ],
        ),
      ),
      body: Column(
        children: [
          Expanded(
            child: _messages.isEmpty
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Container(
                            padding: const EdgeInsets.all(20),
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: AppTheme.accent.withValues(alpha: 0.1),
                              border: Border.all(
                                color: AppTheme.accent.withValues(alpha: 0.25),
                              ),
                            ),
                            child: const Icon(
                              Icons.chat_bubble_outline_rounded,
                              size: 40,
                              color: AppTheme.accent,
                            ),
                          ),
                          const SizedBox(height: 24),
                          Text(
                            'What should we explore?',
                            style: t.titleMedium?.copyWith(
                              fontWeight: FontWeight.w600,
                            ),
                            textAlign: TextAlign.center,
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'Ask about allocations, risk, or how to get started — answers are educational, not financial advice.',
                            style: t.bodyMedium?.copyWith(
                              color: AppTheme.textSecondaryOf(context),
                              height: 1.45,
                            ),
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ),
                    ),
                  )
                : ListView.builder(
                    controller: _scroll,
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
                    itemCount: _messages.length,
                    itemBuilder: (_, i) {
                      final m = _messages[i];
                      final mine = m.role == 'user';
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 12),
                        child: Row(
                          mainAxisAlignment: mine
                              ? MainAxisAlignment.end
                              : MainAxisAlignment.start,
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            if (!mine) ...[
                              CircleAvatar(
                                radius: 16,
                                backgroundColor:
                                    AppTheme.accent.withValues(alpha: 0.15),
                                child: const Icon(
                                  Icons.auto_awesome_rounded,
                                  size: 16,
                                  color: AppTheme.accent,
                                ),
                              ),
                              const SizedBox(width: 8),
                            ],
                            Flexible(
                              child: DecoratedBox(
                                decoration: BoxDecoration(
                                  color: mine
                                      ? AppTheme.accent.withValues(alpha: 0.14)
                                      : AppTheme.surfaceOf(context),
                                  borderRadius: BorderRadius.only(
                                    topLeft: const Radius.circular(18),
                                    topRight: const Radius.circular(18),
                                    bottomLeft: Radius.circular(mine ? 18 : 4),
                                    bottomRight: Radius.circular(mine ? 4 : 18),
                                  ),
                                  border: Border.all(
                                    color: mine
                                        ? AppTheme.accent.withValues(alpha: 0.35)
                                        : AppTheme.borderSubtleOf(context),
                                  ),
                                ),
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 16,
                                    vertical: 12,
                                  ),
                                  child: Text(
                                    m.text,
                                    style: t.bodyMedium?.copyWith(height: 1.4),
                                  ),
                                ),
                              ),
                            ),
                            if (mine) ...[
                              const SizedBox(width: 8),
                              CircleAvatar(
                                radius: 16,
                                backgroundColor: AppTheme.surface2Of(context),
                                child: Icon(
                                  Icons.person_rounded,
                                  size: 16,
                                  color: AppTheme.textSecondaryOf(context),
                                ),
                              ),
                            ],
                          ],
                        ),
                      );
                    },
                  ),
          ),
          if (_loading)
            LinearProgressIndicator(
              minHeight: 2,
              color: AppTheme.accent,
              backgroundColor: AppTheme.surface2Of(context),
            ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: Material(
                      color: AppTheme.surface2Of(context),
                      borderRadius: BorderRadius.circular(16),
                      child: TextField(
                        controller: _controller,
                        minLines: 1,
                        maxLines: 5,
                        style: t.bodyMedium,
                        decoration: const InputDecoration(
                          hintText: 'What should I invest in?',
                          border: InputBorder.none,
                          contentPadding: EdgeInsets.symmetric(
                            horizontal: 16,
                            vertical: 14,
                          ),
                        ),
                        onSubmitted: (_) => _send(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  FilledButton(
                    onPressed: _loading ? null : _send,
                    style: FilledButton.styleFrom(
                      padding: const EdgeInsets.all(16),
                      minimumSize: const Size(52, 52),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(14),
                      ),
                    ),
                    child: const Icon(Icons.send_rounded, size: 22),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Msg {
  _Msg.user(this.text) : role = 'user';
  _Msg.assistant(this.text) : role = 'assistant';

  final String role;
  final String text;
}
