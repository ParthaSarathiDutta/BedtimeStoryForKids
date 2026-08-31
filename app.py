"""Optional Streamlit UI — thin presentation layer over the existing agent system.

Does not change Planner / Storyteller / Judge behavior. Child-facing only:
concept, optional question, and final story. Run with:

    streamlit run app.py

The CLI (`python main.py`) remains the default entry point.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

import config
import feedback_normalizer
import judge
import llm as llm_module
import loop1
import loop2
import planner
import preference_extractor
from llm import LLMClient, LLMError
from models import SessionContext, StoryPlan, UserPreferences

INDEX_PATH = Path("corpus_index.json")


def age_to_band(age: int) -> str:
    if age <= 6:
        return "5-6"
    if age <= 8:
        return "7-8"
    return "9-10"


def _friendly_error(exc: BaseException) -> str:
    if isinstance(exc, LLMError):
        return (
            "I got a bit stuck making that story. "
            "Please try again in a moment, maybe with a simpler idea."
        )
    if isinstance(exc, SystemExit) or "OPENAI_API_KEY" in str(exc):
        return (
            "OPENAI_API_KEY is not set. Copy `.env.example` to `.env`, "
            "add your OpenAI key, and restart: `streamlit run app.py`."
        )
    return "Something unexpected went wrong. Please try starting a new story."


def _load_resources() -> tuple[LLMClient, list]:
    llm_module.load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")
    if not INDEX_PATH.exists():
        raise FileNotFoundError("corpus_index.json is missing")
    import json

    data = json.loads(INDEX_PATH.read_text())
    index = data["stories"] if isinstance(data, dict) else data
    return LLMClient(mock=False), index


def _plan_with_judge(
    preferences: UserPreferences,
    index: list,
    llm: LLMClient,
    prior_plan: StoryPlan | None,
    notes: str | None,
    session: SessionContext,
) -> StoryPlan:
    """Same Planner ↔ Judge internal cycle as loop1, without the child loop."""
    plan = prior_plan
    revision_notes = notes
    for _ in range(config.MAX_INTERNAL_REVISIONS):
        cards = loop1._fetch_inspiration(preferences, index)
        plan = planner.create_plan(
            preferences, cards, llm,
            prior_plan=plan, revision_notes=revision_notes,
        )
        session.log(
            "planner_draft", concept=plan.concept, protagonist=plan.protagonist,
            setting=plan.setting, plot_shape=plan.plot_shape, open_question=plan.open_question,
            child_notice=plan.child_notice,
        )
        verdict = judge.evaluate_plan(plan, preferences, llm)
        session.log(
            "judge_plan_verdict", passed=verdict.passed, scores=verdict.scores,
            reasons=verdict.reasons, deterministic_failures=verdict.deterministic_failures,
            feedback=verdict.feedback,
        )
        if verdict.passed:
            return plan
        revision_notes = verdict.feedback
    return plan  # type: ignore[return-value]


def start_loop1(request: str, age: int, llm: LLMClient, index: list) -> SessionContext:
    known, must_include, dropped = preference_extractor.extract_preferences(request, llm)
    known["reading_band"] = age_to_band(age)
    preferences = UserPreferences(
        initial_request=request, known=known, must_include=must_include,
    )
    session = SessionContext(preferences=preferences)
    session.log("preferences_extracted", known=known, must_include=must_include, dropped=dropped)
    session.plan = _plan_with_judge(preferences, index, llm, None, None, session)
    return session


def revise_plan(session: SessionContext, raw: str, llm: LLMClient, index: list) -> bool:
    """Apply child plan feedback via existing normalizer. Returns True if approved."""
    response = feedback_normalizer.interpret(raw, llm)
    session.preferences.record_plan_feedback(response)
    session.log(
        "child_response", raw_text=raw, approved=response.approved, intent=response.intent,
        extracted_element=response.extracted_element, removed_element=response.removed_element,
    )
    if response.approved:
        return True
    notes = (
        f'The child reacted: "{raw}" (interpreted as {response.intent}). '
        "Revise the plan to address this while keeping everything they already liked."
    )
    session.plan = _plan_with_judge(
        session.preferences, index, llm, session.plan, notes, session,
    )
    return False


def write_story(session: SessionContext, llm: LLMClient) -> None:
    """Existing Loop 2 (incl. ending repair); auto-approve after Judge clears."""
    loop2.run(
        session.plan, session.preferences, llm,
        respond=lambda draft: "yes!",
        session=session,
    )


def revise_story(session: SessionContext, raw: str, llm: LLMClient) -> None:
    """Apply child story feedback using the same normalizer + Loop 2 revision path.

    Mirrors loop2's agent/judge cycle (including calm-ending repair) after the
    child has already seen a draft — without restarting Loop 2 from a blank slate.
    """
    import storyteller

    response = feedback_normalizer.interpret(raw, llm)
    session.preferences.record_story_feedback(response)
    session.log(
        "child_story_response", raw_text=raw, approved=response.approved, intent=response.intent,
        extracted_element=response.extracted_element, removed_element=response.removed_element,
    )
    if response.approved:
        return

    notes = (
        f'The child reacted: "{raw}" (interpreted as {response.intent}). '
        "Revise the story to address this while keeping everything they already liked "
        "and everything from the approved plan."
    )
    prefs = session.preferences
    plan = session.plan
    draft = session.story
    last_verdict = None
    for _ in range(config.MAX_INTERNAL_REVISIONS):
        if (
            draft is not None
            and last_verdict is not None
            and judge.is_primarily_calm_ending_failure(last_verdict)
        ):
            draft = storyteller.revise_ending(draft, prefs, llm, judge_feedback=notes)
            session.log("storyteller_ending_repair", strategy=draft.strategy,
                        word_count=len(draft.text.split()))
        else:
            draft = storyteller.write_story(plan, prefs, llm, revision_notes=notes)
            session.log("storyteller_draft", strategy=draft.strategy,
                        word_count=len(draft.text.split()))
        verdict = judge.evaluate_story(draft, prefs, llm)
        last_verdict = verdict
        session.log(
            "judge_story_verdict", passed=verdict.passed, scores=verdict.scores,
            reasons=verdict.reasons, deterministic_failures=verdict.deterministic_failures,
            feedback=verdict.feedback,
        )
        session.story = draft
        if verdict.passed:
            return
        notes = verdict.feedback


def _reset() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def _init_state() -> None:
    defaults = {
        "stage": "input",  # input | plan | story | error
        "age": 7,
        "request": "",
        "session": None,
        "error_message": "",
        "llm": None,
        "index": None,
        "show_plan_edit": False,
        "show_story_edit": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def main() -> None:
    st.set_page_config(page_title="Bedtime Story Maker", page_icon="🌙", layout="centered")
    # Hide Streamlit chrome that is not part of the child experience:
    # "Deploy", and the "Press Enter to apply" input hint.
    st.markdown(
        """
        <style>
        div[data-testid="stToolbar"] { visibility: hidden; height: 0; }
        div[data-testid="stDecoration"] { display: none; }
        #MainMenu { visibility: hidden; }
        header { visibility: hidden; }
        footer { visibility: hidden; }
        [data-testid="InputInstructions"] { display: none !important; }
        .stDeployButton { display: none !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    _init_state()

    st.title("Bedtime Story Maker")
    st.caption("A cozy story, made just for you.")

    if st.session_state.stage == "error":
        st.error(st.session_state.error_message)
        if st.button("Start a new story", type="primary"):
            _reset()
            st.rerun()
        return

    # Resources (lazy)
    if st.session_state.llm is None:
        try:
            st.session_state.llm, st.session_state.index = _load_resources()
        except Exception as exc:
            st.warning(_friendly_error(exc))
            st.info(
                "Set your key in a local `.env` file:\n\n"
                "```\nOPENAI_API_KEY=sk-...\n```\n\n"
                "Then run `streamlit run app.py` again. "
                "The CLI path is still `python main.py`."
            )
            return

    llm: LLMClient = st.session_state.llm
    index = st.session_state.index

    if st.session_state.stage == "input":
        age = st.selectbox("How old are you?", options=list(range(5, 11)), index=2)
        request = st.text_input("What kind of story would you like?", placeholder="e.g. a funny dragon in space")
        if st.button("Create my story idea", type="primary"):
            if not request.strip():
                st.warning("Tell me a little about the story you'd like!")
            else:
                st.session_state.age = age
                st.session_state.request = request.strip()
                with st.spinner("Thinking of a good idea for you..."):
                    try:
                        st.session_state.session = start_loop1(
                            st.session_state.request, age, llm, index,
                        )
                        st.session_state.stage = "plan"
                        st.session_state.show_plan_edit = False
                    except Exception as exc:
                        st.session_state.error_message = _friendly_error(exc)
                        st.session_state.stage = "error"
                st.rerun()

    elif st.session_state.stage == "plan":
        session: SessionContext = st.session_state.session
        plan = session.plan
        st.subheader("Here's an idea for your story")
        st.write(plan.concept)
        if plan.child_notice:
            st.write(plan.child_notice)
        if plan.open_question:
            st.write(plan.open_question)

        if not st.session_state.show_plan_edit:
            # Step 1: only the two clear choices
            if st.button("Yes, tell me this story!", type="primary"):
                with st.spinner("Writing your bedtime story..."):
                    try:
                        write_story(session, llm)
                        st.session_state.stage = "story"
                        st.session_state.show_story_edit = False
                    except Exception as exc:
                        st.session_state.error_message = _friendly_error(exc)
                        st.session_state.stage = "error"
                st.rerun()
            if st.button("Change my story idea"):
                st.session_state.show_plan_edit = True
                st.rerun()
        else:
            # Step 2: ask what to change, then submit
            change = st.text_input(
                "What would you like to change?",
                placeholder="e.g. add a puppy, make it sillier, no dragon, make it calmer",
                key="plan_change",
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Update my story idea", type="primary"):
                    if not change.strip():
                        st.warning("Tell me what you'd like to change!")
                    else:
                        with st.spinner("Tweaking your story idea..."):
                            try:
                                approved = revise_plan(session, change.strip(), llm, index)
                                if approved:
                                    with st.spinner("Writing your bedtime story..."):
                                        write_story(session, llm)
                                    st.session_state.stage = "story"
                                    st.session_state.show_story_edit = False
                                else:
                                    st.session_state.stage = "plan"
                                    st.session_state.show_plan_edit = False
                            except Exception as exc:
                                st.session_state.error_message = _friendly_error(exc)
                                st.session_state.stage = "error"
                        st.rerun()
            with c2:
                if st.button("Go back"):
                    st.session_state.show_plan_edit = False
                    st.rerun()

        if st.button("Start a new story"):
            _reset()
            st.rerun()

    elif st.session_state.stage == "story":
        session = st.session_state.session
        st.subheader("Your bedtime story")
        if session.story is None:
            st.warning("I couldn't finish a story this time. Let's try again.")
        else:
            st.markdown(session.story.text)

        st.caption("The end. Sweet dreams!")
        st.divider()

        if not st.session_state.show_story_edit:
            if st.button("I want to change something"):
                st.session_state.show_story_edit = True
                st.rerun()
        else:
            story_change = st.text_input(
                "What would you like to change?",
                placeholder="e.g. make the ending calmer, add a kitten",
                key="story_change",
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Update my story", type="primary"):
                    if not story_change.strip():
                        st.warning("Tell me what you'd like to change!")
                    else:
                        with st.spinner("Updating your bedtime story..."):
                            try:
                                revise_story(session, story_change.strip(), llm)
                                st.session_state.show_story_edit = False
                            except Exception as exc:
                                st.session_state.error_message = _friendly_error(exc)
                                st.session_state.stage = "error"
                        st.rerun()
            with c2:
                if st.button("Go back", key="story_go_back"):
                    st.session_state.show_story_edit = False
                    st.rerun()

        if st.button("Start a new story", type="primary"):
            _reset()
            st.rerun()


if __name__ == "__main__":
    main()
