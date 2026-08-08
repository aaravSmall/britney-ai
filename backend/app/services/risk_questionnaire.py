"""Canonical risk-questionnaire question bank + scoring.

The 12 questions below (3 each across time_horizon/loss_tolerance/
experience/goals) are the single source of truth for both what the
onboarding wizard renders (GET /onboarding/questions) and how a
submission is scored (POST /onboarding) — the frontend never hardcodes
question text/options/points, and a client can never submit its own
point values (see routes/onboarding.py: only answer_value is accepted
per question; points are looked up server-side from here).

Every question has exactly 5 options carrying points 1-5 (not
necessarily in list order — see GOAL_PRIMARY below), so the total score
always falls in [12, 60] regardless of which/how many questions exist
in each section. bucket_for_score() divides that range into three
roughly-even thirds and maps the result onto User.risk_tolerance's
existing "low"/"medium"/"high" vocabulary — the exact same 3 values
ai/recommendation_engine.py, agent/decision_loop.py, and
ai/chat_engine.py already consume; nothing about their mapping changes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionOption:
    value: str
    label: str
    points: int


@dataclass(frozen=True)
class Question:
    id: str
    section: str
    prompt: str
    options: tuple[QuestionOption, ...]

    @property
    def points_by_value(self) -> dict[str, int]:
        return {o.value: o.points for o in self.options}


# Display order of sections in the wizard; ids match each Question.section.
SECTIONS: tuple[str, ...] = ("time_horizon", "loss_tolerance", "experience", "goals")

SECTION_LABELS: dict[str, str] = {
    "time_horizon": "Time Horizon",
    "loss_tolerance": "Loss Tolerance",
    "experience": "Experience",
    "goals": "Goals",
}


def _opts(*rows: tuple[str, str, int]) -> tuple[QuestionOption, ...]:
    return tuple(QuestionOption(value=v, label=lbl, points=p) for v, lbl, p in rows)


QUESTIONS: tuple[Question, ...] = (
    # --- Time Horizon ---------------------------------------------------
    Question(
        id="horizon_withdraw",
        section="time_horizon",
        prompt="When do you expect to start withdrawing or using this money?",
        options=_opts(
            ("under_1y", "Within 1 year", 1),
            ("1_3y", "1–3 years", 2),
            ("3_5y", "3–5 years", 3),
            ("5_10y", "5–10 years", 4),
            ("10y_plus", "10+ years", 5),
        ),
    ),
    Question(
        id="horizon_contribute",
        section="time_horizon",
        prompt="How long do you plan to keep adding money to this account?",
        options=_opts(
            ("none", "I don't plan to add more", 1),
            ("year_or_two", "A year or two", 2),
            ("few_years", "A few years", 3),
            ("many_years", "Many years", 4),
            ("indefinitely", "Indefinitely", 5),
        ),
    ),
    Question(
        id="horizon_flexibility",
        section="time_horizon",
        prompt="If you needed this money earlier than planned, how big a problem would that be?",
        options=_opts(
            ("major_problem", "A major problem — I can't afford to wait", 1),
            ("real_problem", "A real problem, but manageable", 2),
            ("moderate_inconvenience", "A moderate inconvenience", 3),
            ("minor_inconvenience", "A minor inconvenience", 4),
            ("no_problem", "No problem at all — this money is fully set aside", 5),
        ),
    ),
    # --- Loss Tolerance ---------------------------------------------------
    Question(
        id="loss_reaction",
        section="loss_tolerance",
        prompt="If your portfolio dropped 20% in a few months, what would you most likely do?",
        options=_opts(
            ("sell_all", "Sell everything to stop further losses", 1),
            ("sell_some", "Sell some to reduce risk", 2),
            ("wait", "Do nothing and wait it out", 3),
            ("hold", "Hold and stay the course", 4),
            ("buy_more", "Buy more while prices are down", 5),
        ),
    ),
    Question(
        id="loss_priority",
        section="loss_tolerance",
        prompt="Which matters more to you?",
        options=_opts(
            ("protect_above_all", "Protecting my money above all", 1),
            ("mostly_protect", "Mostly protecting, a little growth", 2),
            ("balanced", "An even balance of protection and growth", 3),
            ("mostly_growth", "Mostly growth, a little protection", 4),
            ("maximize_growth", "Maximizing growth, even with big swings", 5),
        ),
    ),
    Question(
        id="loss_volatility_comfort",
        section="loss_tolerance",
        prompt="How comfortable are you watching your account value swing up and down week to week?",
        options=_opts(
            ("very_uncomfortable", "Very uncomfortable", 1),
            ("somewhat_uncomfortable", "Somewhat uncomfortable", 2),
            ("neutral", "Neutral", 3),
            ("fairly_comfortable", "Fairly comfortable", 4),
            ("very_comfortable", "Very comfortable", 5),
        ),
    ),
    # --- Experience ---------------------------------------------------
    Question(
        id="exp_level",
        section="experience",
        prompt="How would you describe your investing experience?",
        options=_opts(
            ("none", "None — this is my first time", 1),
            ("dabbled", "A little — I've dabbled", 2),
            ("some", "Some — I've invested a few times", 3),
            ("regular", "Comfortable — I invest somewhat regularly", 4),
            ("experienced", "Experienced — I actively manage investments", 5),
        ),
    ),
    Question(
        id="exp_assets",
        section="experience",
        prompt="Which best describes what you've invested in before?",
        options=_opts(
            ("nothing", "Nothing yet", 1),
            ("savings_cd", "A savings account or CD", 2),
            ("index_funds", "Mutual funds or index funds", 3),
            ("stocks", "Individual stocks", 4),
            ("stocks_crypto", "Stocks, crypto, or other higher-risk assets", 5),
        ),
    ),
    Question(
        id="exp_knowledge",
        section="experience",
        prompt="How familiar are you with how investment values can rise and fall (market volatility)?",
        options=_opts(
            ("not_familiar", "Not familiar at all", 1),
            ("heard_of_it", "I've heard of it but don't fully understand it", 2),
            ("basics", "I understand the basics", 3),
            ("well", "I understand it well", 4),
            ("firsthand", "I'm very familiar and have experienced it firsthand", 5),
        ),
    ),
    # --- Goals ---------------------------------------------------
    Question(
        id="goal_primary",
        section="goals",
        prompt="What's the main reason you're investing?",
        # Points here reflect typical time-horizon/risk-capacity implied by
        # each goal, not list order — e.g. a near-term purchase implies a
        # short horizon (1) while retirement decades away implies a long
        # one (5), regardless of where they appear in the option list.
        options=_opts(
            ("major_purchase", "A major purchase in the next few years (home, car, etc.)", 1),
            ("emergency_cushion", "Building an emergency cushion", 2),
            ("other", "Something else", 3),
            ("wealth_building", "General wealth building over time", 4),
            ("retirement", "Retirement, decades away", 5),
        ),
    ),
    Question(
        id="goal_flexibility",
        section="goals",
        prompt="How flexible is your investment plan if life circumstances change?",
        options=_opts(
            ("fixed", "Not flexible — I have a fixed need for this money", 1),
            ("somewhat_fixed", "Somewhat fixed", 2),
            ("moderate", "Moderately flexible", 3),
            ("fairly_flexible", "Fairly flexible", 4),
            ("very_flexible", "Very flexible — I can adjust easily", 5),
        ),
    ),
    Question(
        id="goal_priority_growth",
        section="goals",
        prompt="Which best describes your ideal outcome?",
        options=_opts(
            ("preserve", "Preserve my initial investment above all", 1),
            ("slow_steady", "Slow, steady growth with minimal risk", 2),
            ("balanced_growth", "Balanced growth with moderate risk", 3),
            ("strong_growth", "Strong growth, accepting real risk", 4),
            ("max_growth", "Maximum growth, comfortable with high risk", 5),
        ),
    ),
)

QUESTIONS_BY_ID: dict[str, Question] = {q.id: q for q in QUESTIONS}

MIN_SCORE = sum(min(o.points for o in q.options) for q in QUESTIONS)  # 12
MAX_SCORE = sum(max(o.points for o in q.options) for q in QUESTIONS)  # 60
_RANGE = MAX_SCORE - MIN_SCORE  # 48

# Roughly even thirds of [MIN_SCORE, MAX_SCORE]: [12,28] low, [29,44]
# medium, [45,60] high.
LOW_MAX = MIN_SCORE + _RANGE // 3
HIGH_MIN = MAX_SCORE - _RANGE // 3 + 1


def bucket_for_score(score: int) -> str:
    """Maps a total questionnaire score onto User.risk_tolerance's
    existing "low"/"medium"/"high" vocabulary — unchanged downstream
    consumers (recommendation_engine.py, decision_loop.py,
    chat_engine.py) never need to know a questionnaire exists."""
    if score <= LOW_MAX:
        return "low"
    if score >= HIGH_MIN:
        return "high"
    return "medium"
