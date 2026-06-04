"""GPT JSON → 에이전트 도메인 타입."""

from __future__ import annotations

import importlib
from typing import Any

from agents.llm.json_utils import parse_json_object

_topic_mod = importlib.import_module("agents.01_topic_agent")
_story_mod = importlib.import_module("agents.02_story_agent")

SubTopic = _topic_mod.SubTopic
SubTopicAngle = _topic_mod.SubTopicAngle
TopicAgentInput = _topic_mod.TopicAgentInput
StoryAgentInput = _story_mod.StoryAgentInput
StoryTone = _story_mod.StoryTone
StoryVariant = _story_mod.StoryVariant
StoryCutBeat = _story_mod.StoryCutBeat

_ANGLE_MAP = {
    "relationship": SubTopicAngle.RELATIONSHIP,
    "place": SubTopicAngle.PLACE,
    "emotion": SubTopicAngle.EMOTION,
}

_TONE_MAP = {
    "comic": StoryTone.COMIC,
    "emotional": StoryTone.EMOTIONAL,
    "twist": StoryTone.TWIST,
}

_EXPECTED_SUBTOPIC_IDS = ("subtopic_1", "subtopic_2", "subtopic_3")


def parse_subtopics_from_llm(text: str, *, input_data: TopicAgentInput) -> list[SubTopic]:
    payload = parse_json_object(text)
    raw_list = payload.get("subtopics")
    if not isinstance(raw_list, list):
        raise ValueError("subtopics must be a list")

    by_id: dict[str, SubTopic] = {}
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if sid not in _EXPECTED_SUBTOPIC_IDS:
            continue
        angle_raw = str(item.get("angle") or "emotion").strip().lower()
        angle = _ANGLE_MAP.get(angle_raw, SubTopicAngle.EMOTION)
        title = str(item.get("title") or "").strip() or f"{input_data.main_topic} — {sid}"
        hook = str(item.get("hook") or "").strip() or title[:60]
        pitch = str(item.get("one_line_pitch") or "").strip() or hook
        by_id[sid] = SubTopic(
            id=sid,
            title=title,
            angle=angle,
            hook=hook,
            one_line_pitch=pitch,
        )

    ordered = [by_id[i] for i in _EXPECTED_SUBTOPIC_IDS if i in by_id]
    if len(ordered) != 3:
        raise ValueError(f"expected 3 subtopics with ids {_EXPECTED_SUBTOPIC_IDS}, got {len(ordered)}")
    return ordered


def parse_variants_from_llm(text: str, *, input_data: StoryAgentInput) -> list[StoryVariant]:
    payload = parse_json_object(text)
    raw_list = payload.get("variants")
    if not isinstance(raw_list, list):
        raise ValueError("variants must be a list")

    cut_count = max(1, min(20, input_data.cut_count))
    st = input_data.subtopic
    by_tone: dict[StoryTone, StoryVariant] = {}

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        tone_raw = str(item.get("tone") or "").strip().lower()
        tone = _TONE_MAP.get(tone_raw)
        if tone is None:
            continue

        title = str(item.get("title") or "").strip() or f"{tone.label_ko} · {st.title}"
        summary = str(item.get("summary") or "").strip() or title
        story_arc = str(item.get("story_arc") or "").strip() or tone.label_ko

        beats: list[StoryCutBeat] = []
        raw_flow = item.get("cut_flow")
        if isinstance(raw_flow, list):
            for idx, beat in enumerate(raw_flow[:cut_count], start=1):
                if not isinstance(beat, dict):
                    continue
                scene = str(beat.get("scene") or "").strip() or f"{st.title} — 컷 {idx}"
                emotion = str(beat.get("emotion") or "").strip() or "중립"
                narration = str(beat.get("narration") or scene).strip()[:200]
                subtitle = str(beat.get("subtitle") or narration).strip()[:80]
                cut_no = int(beat.get("cut") or idx)
                beats.append(
                    StoryCutBeat(
                        cut=cut_no,
                        scene=scene,
                        emotion=emotion,
                        narration=narration,
                        subtitle=subtitle,
                    )
                )

        while len(beats) < cut_count:
            n = len(beats) + 1
            beats.append(
                StoryCutBeat(
                    cut=n,
                    scene=f"{st.title} — 컷 {n}",
                    emotion="중립",
                    narration=f"{st.title} 컷 {n}",
                    subtitle=f"컷 {n}",
                )
            )

        by_tone[tone] = StoryVariant(
            id=f"story_{st.id}_{tone.value}",
            tone=tone,
            title=title,
            summary=summary,
            story_arc=story_arc,
            cut_flow=tuple(beats[:cut_count]),
        )

    expected = {StoryTone.COMIC, StoryTone.EMOTIONAL, StoryTone.TWIST}
    if set(by_tone.keys()) != expected:
        missing = expected - set(by_tone.keys())
        raise ValueError(f"missing story tones: {[t.value for t in missing]}")

    return [by_tone[StoryTone.COMIC], by_tone[StoryTone.EMOTIONAL], by_tone[StoryTone.TWIST]]
