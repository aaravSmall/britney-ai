import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../theme/app_theme.dart';

class _Option {
  const _Option({required this.value, required this.label});

  final String value;
  final String label;

  factory _Option.fromJson(Map<String, dynamic> j) =>
      _Option(value: j['value'] as String, label: j['label'] as String);
}

class _Question {
  const _Question({
    required this.id,
    required this.section,
    required this.prompt,
    required this.options,
  });

  final String id;
  final String section;
  final String prompt;
  final List<_Option> options;

  factory _Question.fromJson(Map<String, dynamic> j) => _Question(
        id: j['id'] as String,
        section: j['section'] as String,
        prompt: j['prompt'] as String,
        options: (j['options'] as List)
            .map((o) => _Option.fromJson(o as Map<String, dynamic>))
            .toList(),
      );
}

// Fixed display order/copy for the 4 sections — matches
// app/services/risk_questionnaire.py's SECTIONS/SECTION_LABELS on the
// backend (kept as a small local constant here rather than fetched,
// same call as auto-invest's interval labels: stable, UI-only text).
const List<String> _sectionOrder = ['time_horizon', 'loss_tolerance', 'experience', 'goals'];

const Map<String, String> _sectionLabels = {
  'time_horizon': 'Time Horizon',
  'loss_tolerance': 'Loss Tolerance',
  'experience': 'Experience',
  'goals': 'Goals',
};

const Map<String, String> _sectionSubtitles = {
  'time_horizon': 'How long until you need this money',
  'loss_tolerance': 'How you\'d react to ups and downs',
  'experience': 'Your investing background',
  'goals': 'What you\'re investing for',
};

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  bool _loading = true;
  String? _error;
  List<_Question> _questions = [];
  final Map<String, String> _answers = {};
  final TextEditingController _elaboration = TextEditingController();
  final PageController _pageController = PageController();
  int _sectionIndex = 0;
  bool _submitting = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _elaboration.dispose();
    _pageController.dispose();
    super.dispose();
  }

  /// Fetches the canonical question bank AND the user's previously saved
  /// answers/elaboration together, so a returning user sees their real
  /// state instead of the hardcoded defaults the old single-page
  /// onboarding always started from regardless of prior submissions.
  Future<void> _load() async {
    final api = context.read<ApiService>();
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final responses = await Future.wait([
        api.get('/onboarding/questions'),
        api.get('/onboarding'),
      ]);
      final questionsRes = responses[0];
      final stateRes = responses[1];
      if (questionsRes.statusCode != 200 || stateRes.statusCode != 200) {
        setState(() {
          _error = 'Could not load onboarding (${questionsRes.statusCode}/${stateRes.statusCode}).';
          _loading = false;
        });
        return;
      }
      final questions = (jsonDecode(questionsRes.body) as List)
          .map((e) => _Question.fromJson(e as Map<String, dynamic>))
          .toList();
      final state = jsonDecode(stateRes.body) as Map<String, dynamic>;
      final savedAnswers = (state['answers'] as Map<String, dynamic>)
          .map((k, v) => MapEntry(k, v as String));

      setState(() {
        _questions = questions;
        _answers
          ..clear()
          ..addAll(savedAnswers);
        _elaboration.text = (state['elaboration'] as String?) ?? '';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Network error: $e';
        _loading = false;
      });
    }
  }

  List<_Question> _questionsFor(String section) =>
      _questions.where((q) => q.section == section).toList();

  bool get _currentSectionComplete {
    final section = _sectionOrder[_sectionIndex];
    return _questionsFor(section).every((q) => _answers.containsKey(q.id));
  }

  void _selectAnswer(String questionId, String value) {
    setState(() => _answers[questionId] = value);
  }

  void _goNext() {
    if (!_currentSectionComplete) return;
    if (_sectionIndex == _sectionOrder.length - 1) {
      _submit();
      return;
    }
    setState(() => _sectionIndex++);
    _pageController.nextPage(duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
  }

  void _goBack() {
    if (_sectionIndex == 0) return;
    setState(() => _sectionIndex--);
    _pageController.previousPage(duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
  }

  Future<void> _submit() async {
    final api = context.read<ApiService>();
    setState(() => _submitting = true);
    final answers = [
      for (final q in _questions) {'question_id': q.id, 'answer_value': _answers[q.id]},
    ];
    final elaboration = _elaboration.text.trim();
    final res = await api.post('/onboarding', {
      'answers': answers,
      'elaboration': elaboration.isEmpty ? null : elaboration,
    });
    if (!mounted) return;
    setState(() => _submitting = false);
    if (res.statusCode >= 200 && res.statusCode < 300) {
      context.go('/home');
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Save failed: ${res.body}')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;

    // This route sits outside the ShellRoute's gradient-backed shell, so
    // (unlike the tab screens) it must not be transparent — nothing dark is
    // painted behind it, which was letting the raw white canvas show through.
    if (_loading) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator(color: AppTheme.accent)),
      );
    }
    if (_error != null) {
      return Scaffold(
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(_error!, textAlign: TextAlign.center),
                const SizedBox(height: 12),
                FilledButton(onPressed: _load, child: const Text('Retry')),
              ],
            ),
          ),
        ),
      );
    }

    final section = _sectionOrder[_sectionIndex];
    final isLast = _sectionIndex == _sectionOrder.length - 1;
    final progress = (_sectionIndex + 1) / _sectionOrder.length;

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Tell us about you',
              style: t.titleLarge?.copyWith(fontWeight: FontWeight.w600),
            ),
            Text(
              'Section ${_sectionIndex + 1} of ${_sectionOrder.length} · ${_sectionLabels[section]}',
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
          ],
        ),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(4),
          child: LinearProgressIndicator(
            value: progress,
            minHeight: 4,
            color: AppTheme.accent,
            backgroundColor: AppTheme.borderSubtleOf(context),
          ),
        ),
      ),
      body: Column(
        children: [
          Expanded(
            child: PageView(
              controller: _pageController,
              // Back/Next-driven only — a swipe shouldn't let someone skip
              // past an unanswered section.
              physics: const NeverScrollableScrollPhysics(),
              children: [
                for (final s in _sectionOrder)
                  _SectionPage(
                    section: s,
                    questions: _questionsFor(s),
                    answers: _answers,
                    onSelect: _selectAnswer,
                    elaborationController: s == 'goals' ? _elaboration : null,
                  ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
            child: Row(
              children: [
                if (_sectionIndex > 0) ...[
                  Expanded(
                    child: OutlinedButton(
                      onPressed: _submitting ? null : _goBack,
                      child: const Text('Back'),
                    ),
                  ),
                  const SizedBox(width: 12),
                ],
                Expanded(
                  flex: 2,
                  child: FilledButton(
                    onPressed: (_currentSectionComplete && !_submitting) ? _goNext : null,
                    child: _submitting
                        ? const SizedBox(
                            height: 22,
                            width: 22,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black),
                          )
                        : Text(isLast ? 'Submit' : 'Next'),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _SectionPage extends StatelessWidget {
  const _SectionPage({
    required this.section,
    required this.questions,
    required this.answers,
    required this.onSelect,
    this.elaborationController,
  });

  final String section;
  final List<_Question> questions;
  final Map<String, String> answers;
  final void Function(String questionId, String value) onSelect;
  final TextEditingController? elaborationController;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
      children: [
        Text(
          _sectionLabels[section] ?? section,
          style: t.titleLarge?.copyWith(fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 4),
        Text(
          _sectionSubtitles[section] ?? '',
          style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
        ),
        const SizedBox(height: 20),
        for (final q in questions) ...[
          _QuestionCard(question: q, selected: answers[q.id], onSelect: (v) => onSelect(q.id, v)),
          const SizedBox(height: 16),
        ],
        if (elaborationController != null) ...[
          const SizedBox(height: 4),
          Text(
            'Anything else you want us to know?',
            style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 4),
          Text(
            'Optional',
            style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: elaborationController,
            maxLines: 3,
            decoration: const InputDecoration(
              hintText: 'e.g. saving for my kid\'s college, specific concerns...',
            ),
          ),
        ],
      ],
    );
  }
}

class _QuestionCard extends StatelessWidget {
  const _QuestionCard({
    required this.question,
    required this.selected,
    required this.onSelect,
  });

  final _Question question;
  final String? selected;
  final ValueChanged<String> onSelect;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(question.prompt, style: t.bodyLarge?.copyWith(fontWeight: FontWeight.w600)),
            const SizedBox(height: 12),
            for (final opt in question.options) ...[
              _OptionRow(
                label: opt.label,
                selected: selected == opt.value,
                onTap: () => onSelect(opt.value),
              ),
              if (opt != question.options.last) const SizedBox(height: 8),
            ],
          ],
        ),
      ),
    );
  }
}

class _OptionRow extends StatelessWidget {
  const _OptionRow({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return InkWell(
      borderRadius: BorderRadius.circular(10),
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: selected ? AppTheme.accent.withValues(alpha: 0.12) : Colors.transparent,
          border: Border.all(
            color: selected ? AppTheme.accent : AppTheme.borderSubtleOf(context),
          ),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          children: [
            Icon(
              selected ? Icons.check_circle_rounded : Icons.circle_outlined,
              size: 20,
              color: selected ? AppTheme.accent : AppTheme.textSecondaryOf(context),
            ),
            const SizedBox(width: 10),
            Expanded(child: Text(label, style: t.bodyMedium)),
          ],
        ),
      ),
    );
  }
}
