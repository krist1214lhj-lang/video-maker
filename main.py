import base64
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from audio_pipeline import (
    BgmGenerator,
    NarrationCutInput,
    NarrationGenerator,
    ProjectVoiceCutInput,
    ProjectVoiceGenerator,
    SubtitleCutInput,
    SubtitleGenerator,
    ProjectSubtitleCueInput,
    ProjectSubtitleGenerator,
)
from audio_pipeline.project_voice_generator import probe_mp3_duration
from image_providers import ImageGenerationRequest, get_image_provider
from image_providers.reference_image import inspect_reference_image_file, SUPPORTED_REFERENCE_EXTENSIONS
from video_editor import probe_video_metadata
from video_editor.project_final_export import (
    FFmpegNotInstalledError,
    FFmpegRunError,
    ProjectFinalExportInput,
    assemble_project_final_export,
    build_synced_narration_tracks,
    concat_narration_tracks,
    is_valid_media_file,
    normalize_export_cut_video,
    pad_narration_track_to_duration,
    probe_audio_metadata,
    stitch_normalized_export_videos,
)
from video_providers import VideoGenerationRequest, get_video_provider
from video_providers.media_paths import resolve_local_image_path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=".env")
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
print(f"REPLICATE_API_TOKEN loaded: {bool(os.getenv('REPLICATE_API_TOKEN'))}")

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    OpenAI = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    Image = None

STATIC_DIR = PROJECT_ROOT / "static"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
REFERENCE_CHARACTERS_DIR = PROJECT_ROOT / "reference_characters"
REFERENCE_CHARACTERS_DIR.mkdir(exist_ok=True)
GENERATED_IMAGE_DIR = PROJECT_ROOT / "generated_images"
GENERATED_IMAGE_DIR.mkdir(exist_ok=True)
VIDEO_JOBS_DIR = PROJECT_ROOT / "video_jobs"
VIDEO_JOBS_DIR.mkdir(exist_ok=True)
GENERATED_CLIPS_DIR = PROJECT_ROOT / "generated_clips"
GENERATED_CLIPS_DIR.mkdir(exist_ok=True)
GENERATED_OUTPUTS_DIR = PROJECT_ROOT / "generated_outputs"
GENERATED_OUTPUTS_DIR.mkdir(exist_ok=True)
LATEST_VIDEOS_DIR = PROJECT_ROOT / "latest_videos"
LATEST_VIDEOS_DIR.mkdir(exist_ok=True)
PROJECTS_DIR = PROJECT_ROOT / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR = PROJECT_ROOT / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)
DEFAULT_PROJECT_SLUG = "bposik_rainy_home"
DEFAULT_ACTIVE_CHARACTER = "bposik_v2"
DEFAULT_CONTINUITY_INHERITANCE_STRENGTH = "high"
LEGACY_ACTIVE_CHARACTER_ALIASES = frozenset({"bposik"})


def normalize_active_character_name(character_name: str | None) -> str:
    normalized = (character_name or "").strip()
    if not normalized or normalized.lower() in LEGACY_ACTIVE_CHARACTER_ALIASES:
        return DEFAULT_ACTIVE_CHARACTER
    return normalized


DEFAULT_PROJECT_DIR = PROJECTS_DIR / DEFAULT_PROJECT_SLUG
PROJECT_SOURCE_IMAGES_DIR = DEFAULT_PROJECT_DIR / "source_images"
PROJECT_GENERATED_CUTS_DIR = DEFAULT_PROJECT_DIR / "generated_cuts"
PROJECT_SELECTED_CUTS_DIR = DEFAULT_PROJECT_DIR / "selected_cuts"
PROJECT_FULL_VIDEO_DIR = DEFAULT_PROJECT_DIR / "full_video"
PROJECT_PROMPTS_DIR = DEFAULT_PROJECT_DIR / "prompts"
for directory in (
    PROJECT_SOURCE_IMAGES_DIR,
    PROJECT_GENERATED_CUTS_DIR,
    PROJECT_SELECTED_CUTS_DIR,
    PROJECT_FULL_VIDEO_DIR,
    PROJECT_PROMPTS_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)
SERVER_PORT = 8011
DEFAULT_MOTION_SHOT_CUTS = {1, 3, 5}
STILL_HOLD_DURATION_SECONDS = 2.5
DEV_RELOAD_WATCH_FILES = (
    Path(__file__).resolve(),
    TEMPLATES_DIR / "index.html",
    STATIC_DIR / "style.css",
)

app = FastAPI(
    title="Video Plan API",
    description="동영상 자동 생성 프로그램의 1단계 영상 구성안 생성 API",
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/reference_characters", StaticFiles(directory=REFERENCE_CHARACTERS_DIR), name="reference_characters")
app.mount("/generated_images", StaticFiles(directory=GENERATED_IMAGE_DIR), name="generated_images")
app.mount("/video_jobs", StaticFiles(directory=VIDEO_JOBS_DIR), name="video_jobs")
app.mount("/generated_clips", StaticFiles(directory=GENERATED_CLIPS_DIR), name="generated_clips")
app.mount("/generated_outputs", StaticFiles(directory=GENERATED_OUTPUTS_DIR), name="generated_outputs")
app.mount("/latest_videos", StaticFiles(directory=LATEST_VIDEOS_DIR), name="latest_videos")

templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    return JSONResponse(status_code=500, content={"detail": str(exc) or "Internal server error"})


class CutPromptItem(BaseModel):
    cut_number: int = Field(..., ge=1, le=5)
    cut_type: str = ""
    scene_description: str = ""
    narration: str = ""
    subtitle: str = ""
    emotion: str = ""
    image_prompt: str = ""


class GenerateCutPromptsRequest(BaseModel):
    topic: str = Field(..., min_length=1, description="영상 주제")
    style: str = Field(default="", description="영상 스타일")
    summary: str = Field(default="", description="Storyline 전체 요약")
    cut_flow: list[Any] = Field(..., min_length=1, description="Storyline 컷 흐름")
    selected_project: str = DEFAULT_PROJECT_SLUG

class GenerateCutPromptsResponse(BaseModel):
    topic: str
    style: str
    summary: str
    cuts: list[CutPromptItem]


class StorylineCutItem(BaseModel):
    cut: int = Field(..., ge=1)
    scene: str = Field(default="")
    emotion: str = Field(default="")
    narration: str = Field(default="")
    subtitle: str = Field(default="")


class StorylineScriptPayload(BaseModel):
    topic: str = ""
    summary: str = ""
    story_arc: str = ""
    cut_flow: list[str] = Field(default_factory=list)
    cut_numbers: list[int] | None = None


class VideoPlanRequest(BaseModel):
    topic: str = Field(..., min_length=1, description="영상 주제")
    style: str = Field(..., min_length=1, description="영상 스타일")
    duration: int = Field(..., ge=5, description="전체 영상 길이(초)")
    character_name: str = DEFAULT_ACTIVE_CHARACTER
    image_provider: str = Field(default="openai", description="Storyboard image provider: openai, replicate, or mock")
    selected_project: str = DEFAULT_PROJECT_SLUG
    continuity_inheritance_strength: str = Field(
        default=DEFAULT_CONTINUITY_INHERITANCE_STRENGTH,
        description="Reference frame inheritance strength: low, medium, or high",
    )
    cut_prompts: list[CutPromptItem] | None = None


class ProjectCreateRequest(BaseModel):
    project: str = Field(..., min_length=1, max_length=80)


class StorylineScriptPayload(BaseModel):
    topic: str = ""
    summary: str = ""
    story_arc: str = ""
    cut_flow: list[str] = Field(default_factory=list)
    cut_numbers: list[int] | None = None


class GenerateScriptRequest(BaseModel):
    selected_project: str = DEFAULT_PROJECT_SLUG
    storyline: StorylineScriptPayload | None = None


class ScenarioCutFlowItem(BaseModel):
    cut: int = Field(..., ge=1)
    scene: str = Field(default="")
    emotion: str = Field(default="")
    narration: str = Field(default="")
    subtitle: str = Field(default="")


class ScenarioOption(BaseModel):
    id: str
    title: str
    summary: str
    tone: str
    cut_flow: list[ScenarioCutFlowItem]


class GenerateScenarioOptionsRequest(BaseModel):
    topic: str = Field(..., min_length=1, description="영상 주제")
    style: str = Field(default="", description="영상 스타일")
    duration: int = Field(default=15, ge=5, description="전체 영상 길이(초)")
    selected_project: str = DEFAULT_PROJECT_SLUG


class GenerateScenarioOptionsResponse(BaseModel):
    ok: bool = True
    topic: str
    style: str
    duration: int
    scenarios: list[ScenarioOption]


class SelectScenarioRequest(BaseModel):
    selected_project: str = DEFAULT_PROJECT_SLUG
    scenario: ScenarioOption


class GenerateStorylineRequest(BaseModel):
    topic: str = Field(..., min_length=1, description="영상 주제")
    style: str = Field(default="", description="영상 스타일 힌트")
    scenario: ScenarioOption | None = None


class GenerateStorylineResponse(BaseModel):
    topic: str
    summary: str
    story_arc: str
    cut_flow: list[StorylineCutItem]


class GenerateVoiceRequest(BaseModel):
    selected_project: str = DEFAULT_PROJECT_SLUG
    selected_cuts: list[int] | None = None


class GenerateSubtitleRequest(BaseModel):
    selected_project: str = DEFAULT_PROJECT_SLUG
    selected_cuts: list[int] | None = None


class FinalExportRequest(BaseModel):
    selected_project: str = DEFAULT_PROJECT_SLUG
    selected_cuts: list[int] | None = None


class UpdateMotionPromptRequest(BaseModel):
    selected_project: str = DEFAULT_PROJECT_SLUG
    cut: int = Field(..., ge=1)
    motion_prompt: str = Field(..., min_length=1, max_length=500)


class CharacterProfile(BaseModel):
    character_name: str
    character_summary: str
    character_prompt: str
    reference_images: list[str]
    memory_file: str = ""


REFERENCE_IMAGE_PRIORITY_NAMES = (
    "main.png",
    "main.jpg",
    "main.jpeg",
    "main.webp",
    "reference.png",
    "reference.jpg",
    "reference.jpeg",
    "reference.webp",
    "bposik_v2.png",
    "bposik_v2.jpg",
    "bposik_v2.jpeg",
    "bposik_v2.webp",
)

CHARACTER_LOCK_SENTENCE = (
    "Reference Character: bposik_v2. Strong Identity Lock: exact same short rounded muzzle, round teddy-bear face, "
    "same small compact body, short legs, toy-sized poodle mix. Preserve cute compact proportions in every cut. "
    "Only change expression, pose, and action."
)

BPOSIK_REFERENCE_CHARACTER = "Reference Character: bposik_v2"

BPOSIK_FACE_SHAPE_LOCK = (
    "exact same short rounded muzzle as bposik_v2; round teddy-bear face, not a long snout; "
    "round head; compact small face; same eye spacing; same small black button nose; same soft floppy ears; "
    "same cream/apricot curly fur; preserve bposik_v2 face identity more than pose variation"
)

BPOSIK_BODY_IDENTITY_LOCK = (
    "same small compact body as bposik_v2; short legs and rounded body proportion; "
    "toy-sized poodle mix, not tall; rounded teddy-bear grooming silhouette; "
    "compact chest and small paws; same soft rounded head-to-body ratio; "
    "preserve cute compact proportions in every cut; not a tall adult poodle; "
    "not a long-legged poodle; not a long-bodied dog"
)

BPOSIK_STRONG_IDENTITY_LOCK = (
    f"Strong Identity Lock: {BPOSIK_FACE_SHAPE_LOCK}. "
    f"Body Identity Lock: {BPOSIK_BODY_IDENTITY_LOCK}. "
    "This is the exact same dog as the bposik_v2 reference image. "
    "Preserve the same face and body identity across all cuts — face structure and body proportions must never change. "
    "Do not change the dog's breed, face shape, muzzle length, body size, fur color, or ear shape. "
    "Do not make a different poodle. Do not generate a new dog design for each cut. "
    "Only change expression, gaze direction, pose, and small body action. "
    "same face identity across all cuts."
)

BPOSIK_IDENTITY_LOCK = BPOSIK_STRONG_IDENTITY_LOCK
BBOSIK_CHARACTER_LOCK = BPOSIK_STRONG_IDENTITY_LOCK

ACTING_IDENTITY_GUARD = "while preserving the exact same bposik_v2 face identity"

ACTING_FACE_SHAPE_GUARD = (
    "while preserving the same short muzzle and round teddy-bear face; "
    "expression changes only, not face structure; pose changes only, not dog identity"
)

ACTING_BODY_SHAPE_GUARD = (
    "while preserving the same small compact bposik_v2 body; "
    "pose changes only, not body proportion; action changes only, not dog identity"
)

EMOTION_EXPRESSION_IDENTITY_SUFFIX = (
    "while preserving the same bposik_v2 face identity; "
    "expression changes only, not face structure; "
    "same short rounded muzzle; same round teddy-bear face; "
    "same eye spacing and black button nose"
)

EMOTION_EXPRESSION_NEGATIVE = (
    "human-like facial expression, exaggerated human smile, angry face, scary expression, "
    "different face identity, changed eye shape, changed muzzle while expressing emotion"
)

COMIC_FACIAL_ACTING_BOOST = (
    "expressive but natural dog face, readable emotion in the eyes, cute animated facial acting, "
    "subtle cartoon-like emotion but realistic dog anatomy, no human-like face, no exaggerated human smile"
)

CHARACTER_NEGATIVE_PROMPT = (
    "different dog, different face, different breed, changed muzzle, long snout, elongated muzzle, "
    "narrow face, long narrow muzzle, large dog body, different muzzle length, different head shape, stretched face, "
    "adult poodle long face, different dog identity, different ear shape, "
    "tall adult poodle, long-legged poodle, long-bodied dog, slim body, elongated body, "
    "mature show poodle, standard poodle proportions, different body size, "
    "different fur color, different body proportion, oversized dog, puppy-like redesign, random poodle, "
    "inconsistent character, new dog identity, changed eye spacing, changed nose shape, "
    "different poodle, redesigned face, altered head shape, wrong fur texture, static neutral pose, "
    "blank expression, motionless"
)

IMAGE_CONSISTENCY_RULE = (
    "identity fixed across all cuts — same face identity, head shape, eye spacing, nose, muzzle, fur, "
    "body proportion, ear shape; only expression, gaze, pose, and small actions may change per cut"
)

POSE_VARIETY_RULE = (
    "Pose Variety Rule: avoid repeating the same paw-lift pose in multiple cuts; "
    "each cut must have a different body action; keep the same dog identity, but vary only the gesture and emotion."
)

CUT_POSE_DIRECTIVES: dict[int, str] = {
    1: (
        "CUT1 pose: curiosity start, slight head tilt, sniffing with nose forward, "
        "only a very tiny front-paw lift allowed at most, all other paws on ground; "
        "round teddy-bear face close to bposik_v2, compact small body, short rounded muzzle, "
        "no long snout, no tall poodle posture, short legs visible"
    ),
    2: (
        "CUT2 pose: one cautious step forward, sneaky glance toward mom, weight on three paws mid-step; "
        "round teddy-bear face close to bposik_v2, compact small body, short rounded muzzle, "
        "no long snout, no tall poodle posture, short legs visible"
    ),
    3: (
        "CUT3 pose: playing innocent, head turned slightly sideways, averted gaze, "
        "no front paw lift, all paws stay on ground"
    ),
    4: (
        "CUT4 pose: low sneaky posture, body lowered toward floor, careful creeping step on all four paws; "
        "low cautious posture, but same round teddy-bear face; short muzzle clearly visible; no elongated face"
    ),
    5: (
        "CUT5 pose: satisfied ending, relaxed compact sitting pose, small rounded body, short legs visible, "
        "soft satisfied eyes, tiny proud smile-like expression, natural small tail wag, all paws on ground, "
        "no tall upright posture, no elongated body, natural dog sitting posture, no human-like gesture"
    ),
}

CUT1_2_BODY_NEGATIVE = (
    "tall poodle posture, long legs, elongated body, adult standard poodle look, mature show poodle body"
)

CUT5_BODY_NEGATIVE = (
    "tall upright posture, elongated body, large mature poodle body, long legs when sitting, "
    "oversized dog in sitting pose, slim elongated torso"
)

CUT4_FACE_LOCK = (
    "low cautious posture, but same round teddy-bear face; short muzzle clearly visible; no elongated face"
)

CUT4_FACE_NEGATIVE = (
    "elongated face when low posture, long snout from foreshortening, stretched muzzle in crouch, narrow face in sneak pose"
)

CUT5_POSE_NEGATIVE = (
    "no human-like waving, no exaggerated arm gesture, no standing like a human, no oversized smile, "
    "keep natural dog posture, no front paw lift, no anthropomorphic pose, no proud standing on hind legs"
)

COMIC_NATURAL_STYLE = (
    "cute and expressive but still natural dog anatomy; same small compact toy-poodle body and short legs; "
    "animated expression, not human-like pose; playful but believable dog movement; avoid exaggerated human gestures"
)

RAIN_TOPIC_KEYWORDS = (
    "rain",
    "rainy",
    "rainfall",
    "downpour",
    "storm",
    "wet",
    "soaked",
    "drenched",
    "umbrella",
    "비",
    "빗",
    "우산",
    "젖",
    "축축",
    "빗방울",
    "장마",
    "소나기",
)

DOG_CAFE_TOPIC_KEYWORDS = (
    "애견카페",
    "애견 카페",
    "카페",
    "dog cafe",
    "pet cafe",
    "dog-cafe",
    "pet-cafe",
)

DOG_CAFE_SCENE_CONTEXT_LOCK = (
    "same indoor dog cafe across every cut; warm cozy pet cafe interior; wooden floor; "
    "soft warm lighting; small dogs playing in the background; dog play area; "
    "cafe tables and chairs in the background; same location continuity; "
    "do not change to home, bedroom, living room, TV room, outdoor street, or park"
)

DOG_CAFE_BACKGROUND_NEGATIVE = (
    "home interior, bedroom, bed, blanket, sofa living room, TV room, television, "
    "apartment room, kitchen, outdoor street, park, different location, "
    "scene change to home, inconsistent background"
)

DOG_CAFE_CUT_POSITIONS: dict[int, str] = {
    1: (
        "inside the same cozy indoor dog cafe near the entrance area; "
        "Bbosik notices other small dogs nearby on the wooden floor"
    ),
    2: (
        "same dog cafe play zone on the wooden floor; "
        "Bbosik cautiously approaches other dogs"
    ),
    3: (
        "same dog cafe central play area; "
        "Bbosik glances at friends while pretending innocence among other small dogs"
    ),
    4: (
        "same dog cafe floor-level play area; "
        "Bbosik in a low playful posture near other dogs on the wooden floor"
    ),
    5: (
        "final moment inside the same cozy dog cafe; "
        "Bbosik sitting near a cafe table or dog play area; "
        "other small dogs softly blurred in the background; no bed, no TV, no living room"
    ),
}

HOME_SCENE_KEYWORDS = (
    "거실",
    "침실",
    "소파",
    "현관",
    "부엌",
    "방 안",
    "아파트",
    "집에서",
    "집 안",
    "tv",
    "티비",
    "television",
    "bedroom",
    "living room",
    "sofa",
    "blanket",
    "bed",
    "kitchen",
    "apartment",
    "home interior",
)


class MasterCharacter(BaseModel):
    character_type: str = "dog"
    breed: str = "small poodle mix dog"
    body_size: str = "small compact companion dog with consistent short legs and rounded body proportions"
    fur_color: str = "cream/apricot curly fur with consistent coat tone across every cut"
    posture: str = "gentle companion posture, readable body language, no sudden posture redesign between cuts"
    emotional_state: str = "consistent emotional baseline carried across the story arc"
    key_visual_traits: list[str] = Field(
        default_factory=lambda: [
            "consistent rounded teddy-bear poodle face",
            "same dark glossy eyes and black button nose",
            "consistent curly fur clumps and grooming silhouette",
            "consistent small body proportions",
            "stable screen direction and subject scale",
        ]
    )
    lock_sentence: str = CHARACTER_LOCK_SENTENCE


class SceneContext(BaseModel):
    weather: str
    location: str
    props: str
    lighting: str
    action: str


class ContinuityState(BaseModel):
    color_temperature: str = "matched cinematic color temperature across all cuts"
    lens_consistency: str = "cinematic prime lens continuity with coherent depth of field and stable filmic sharpness"
    camera_direction: str = "preserve screen direction and camera momentum between cuts"
    emotional_arc: str = "emotional arc continues without reset between cuts"


REFERENCE_CHARACTER_PUBLIC_PATH = "/reference_characters/current_reference.png"
CURRENT_REFERENCE_FILE = REFERENCE_CHARACTERS_DIR / "current_reference.png"

REFERENCE_CHARACTER_IDENTITY_RULES = (
    "same exact bposik_v2 dog identity from the reference image; "
    f"{BPOSIK_FACE_SHAPE_LOCK}; {BPOSIK_BODY_IDENTITY_LOCK}; "
    "do not make a different poodle; only expression, gaze direction, pose, and small body action may change per cut"
)


class ReferenceCharacter(BaseModel):
    path: str = REFERENCE_CHARACTER_PUBLIC_PATH
    source_cut_number: int = 1
    prompt: str = ""


class ReferenceFrame(BaseModel):
    stored_path: str = REFERENCE_CHARACTER_PUBLIC_PATH
    public_path: str = REFERENCE_CHARACTER_PUBLIC_PATH
    source_cut_number: int = 1
    continuity_inheritance_strength: str = DEFAULT_CONTINUITY_INHERITANCE_STRENGTH
    image_provider: str = "openai"
    reference_image_path: str | None = None
    inheritance_applied: bool = False


class CutPlan(BaseModel):
    cut_number: int
    cut_type: str = "EMOTIONAL"
    visual_style_lock: dict[str, str] = Field(default_factory=dict)
    action_state: str = "OBSERVING"
    emotion_state: str = "CALM"
    motion_state: str = "SUBTLE"
    allowed_subject_motion: str = ""
    blocked_actions: list[str] = Field(default_factory=list)
    conflict_warnings: list[str] = Field(default_factory=list)
    character_lock_prompt: str = ""
    scene_context_prompt: str = ""
    reference_character_path: str = REFERENCE_CHARACTER_PUBLIC_PATH
    reference_character_prompt: str = ""
    previous_cut_image_path: str = ""
    reference_frame_path: str = REFERENCE_CHARACTER_PUBLIC_PATH
    continuity_inheritance_strength: str = DEFAULT_CONTINUITY_INHERITANCE_STRENGTH
    reference_frame_applied: bool = False
    reference_frame: dict[str, str | bool | None] = Field(default_factory=dict)
    continuity_constraints: dict[str, str] = Field(default_factory=dict)
    active_character: str = ""
    character_summary: str = ""
    character_prompt: str = ""
    scene_description: str
    narration: str
    subtitle: str
    shot_type: str
    lens: str
    movement: str
    lighting: str
    mood_color: str
    video_prompt: str
    motion_prompt: str
    motion_grammar: dict[str, str] = Field(default_factory=dict)
    start_frame_prompt: str
    end_frame_prompt: str
    camera_path: str
    environmental_motion: str
    subject_motion: str
    motion_intensity: str
    transition_style: str
    continuity_notes: str
    previous_cut_memory: dict[str, str] = Field(default_factory=dict)
    continuity_memory: dict[str, str] = Field(default_factory=dict)
    runway_prompt: str
    kling_prompt: str
    veo_prompt: str
    pika_prompt: str
    previous_shot_relation: str
    next_shot_relation: str
    emotional_transition: str
    camera_transition: str
    spatial_transition: str
    pacing_curve: str
    framing: str
    foreground: str
    midground: str
    background: str
    camera_height: str
    subject_direction: str
    emotional_intensity: str
    visual_focus: str
    composition_rule: str
    start_frame_description: str
    end_frame_description: str
    motion_strength: str
    camera_speed: str
    transition_duration: str
    cinematic_pacing: str
    transition: str
    sound_design: str
    background_music: str
    pacing: str
    image_prompt: str
    acting_layer_prompt: str = ""
    cut_story_beat: str = ""
    image_url: str
    image_status: str
    image_error: str | None = None
    sample_image_url: str
    recommended_duration: int


class VideoPlanResponse(BaseModel):
    topic: str
    style: str
    duration: int
    master_character: MasterCharacter | None = None
    scene_context: SceneContext | None = None
    reference_character: ReferenceCharacter | None = None
    reference_frame: ReferenceFrame | None = None
    continuity_state: ContinuityState | None = None
    active_character: CharacterProfile | None = None
    cuts: list[CutPlan]


class SetReferenceCharacterRequest(BaseModel):
    cut_number: int = Field(..., ge=1, le=5)
    image_url: str = Field(..., min_length=1)


class SetReferenceCharacterResponse(BaseModel):
    reference_character: ReferenceCharacter
    reference_character_path: str
    message: str


class VideoJobRequest(BaseModel):
    plan: VideoPlanResponse
    selected_cut: int | None = Field(default=None, ge=1)
    motion_selection: dict[str, bool] | None = None
    selected_project: str = DEFAULT_PROJECT_SLUG


class VideoJobCut(BaseModel):
    cut_number: int
    status: str
    video_status: str = "queued"
    render_progress: int = 0
    storyboard_ref: str
    timeline_ref: str
    prompt_file: str
    clip_json_file: str = ""
    prompt_txt_file: str = ""
    continuity_notes_file: str = ""
    video_file: str
    video_url: str
    clip_duration: int = 0
    estimated_render_time: int = 0
    duration: int
    generator: str
    motion_enabled: bool = True
    timeline_type: str = "motion"
    still_hold_duration: float = STILL_HOLD_DURATION_SECONDS


class VideoJobResponse(BaseModel):
    job_id: str
    status: str
    selected_cut: int | None = None
    job_dir: str
    manifest_file: str
    clips_dir: str = ""
    render_manifest_file: str = ""
    render_manifest_url: str = ""
    storyboard_export_file: str = ""
    storyboard_export_url: str = ""
    timeline_export_file: str = ""
    timeline_export_url: str = ""
    export_package_file: str = ""
    export_package_url: str = ""
    export_status: str = "pending"
    created_at: str
    cuts: list[VideoJobCut]


class GenerateVideoClipRequest(BaseModel):
    cut_number: int = Field(..., ge=1)
    cut_id: int | None = Field(default=None, ge=1)
    selected_cut: int | None = Field(default=None, ge=1)
    cut_type: str = "UNKNOWN"
    visual_style_lock: dict[str, str] = Field(default_factory=dict)
    action_state: str = "OBSERVING"
    emotion_state: str = "CALM"
    motion_state: str = "SUBTLE"
    allowed_subject_motion: str = ""
    blocked_actions: list[str] = Field(default_factory=list)
    character_lock_prompt: str = ""
    scene_context_prompt: str = ""
    continuity_constraints: dict[str, str] = Field(default_factory=dict)
    motion_grammar: dict[str, str] = Field(default_factory=dict)
    active_character: str = ""
    image_path: str = Field(..., min_length=1)
    image_prompt: str = ""
    motion_prompt: str = Field(default="", min_length=0)
    duration: float = Field(..., ge=1)
    cut_index: int | None = Field(default=None, ge=0)
    per_cut_duration: float | None = Field(default=None, ge=1)
    total_duration: float | None = Field(default=None, ge=1)
    selected_cuts: list[int] = Field(default_factory=list)
    provider: str = "mock"
    disallow_mock_provider: bool = False
    job_id: str | None = None
    selected_project: str = DEFAULT_PROJECT_SLUG
    motion_enabled: bool = True
    still_hold_duration: float = Field(default=STILL_HOLD_DURATION_SECONDS, ge=2.0, le=3.0)


class GenerateSequentiallyRequest(BaseModel):
    plan: VideoPlanResponse
    selected_cut: int = Field(..., ge=1)
    provider: str = "replicate"
    selected_project: str = DEFAULT_PROJECT_SLUG


class GenerateVideoClipResponse(BaseModel):
    job_id: str
    status: str
    provider_status: str = ""
    selected_cut: int | None = None
    clip_path: str
    provider: str
    message: str
    manifest_file: str = ""
    render_manifest_file: str = ""
    render_manifest_url: str = ""
    clip_url: str = ""
    video_url: str = ""
    latest_video_url: str = ""
    prompt_txt_file: str = ""
    clip_manifest_file: str = ""
    motion_enabled: bool = True
    timeline_type: str = "motion"
    still_hold_duration: float = STILL_HOLD_DURATION_SECONDS


class MotionSelectionRequest(BaseModel):
    job_id: str = Field(..., min_length=1)
    motion_enabled: dict[str, bool] = Field(default_factory=dict)
    skipped_still_cuts: list[int] = Field(default_factory=list)
    selected_project: str = DEFAULT_PROJECT_SLUG


class DirectorTimelineCut(BaseModel):
    cut_number: int = Field(..., ge=1)
    selected: bool = True
    best_shot: bool = False
    transition_type: str = "cut"
    transition_duration: float = Field(default=0.5, ge=0, le=3)
    duration: float = Field(..., gt=0)
    video_file: str = ""
    previous_cut_memory: dict[str, str] = Field(default_factory=dict)
    continuity_memory: dict[str, str] = Field(default_factory=dict)


class DirectorTimelineStitchRequest(BaseModel):
    cuts: list[DirectorTimelineCut]
    selected_project: str = DEFAULT_PROJECT_SLUG


class BuildFullVideoRequest(BaseModel):
    selected_project: str = DEFAULT_PROJECT_SLUG


class ProjectRestoreResponse(BaseModel):
    project: str
    restored: bool
    plan_source: str = "none"
    plan: VideoPlanResponse | None = None
    script: dict | None = None
    motion_selection: dict[str, bool] = Field(default_factory=dict)
    current_run: dict = Field(default_factory=dict)
    video_cuts: list[dict] = Field(default_factory=list)
    job_id: str | None = None
    scenario_options: list[ScenarioOption] = Field(default_factory=list)
    current_scenario: ScenarioOption | None = None


class CleanupArchiveRequest(BaseModel):
    confirm: bool = False


class AudioPipelineCutInput(BaseModel):
    cut_number: int = Field(..., ge=1)
    narration: str = ""
    subtitle: str = ""
    duration: int = Field(default=4, ge=1)


class AudioPipelineJobRequest(BaseModel):
    job_id: str = Field(..., min_length=1)
    topic: str = ""
    style: str = ""
    cuts: list[AudioPipelineCutInput] = Field(default_factory=list)
    use_mock: bool = True
    selected_project: str = DEFAULT_PROJECT_SLUG


class GenerateNarrationResponse(BaseModel):
    job_id: str
    status: str
    output_dir: str
    output_url: str
    narration: dict
    bgm: dict
    audio_pipeline: dict
    render_manifest_url: str = ""
    message: str


class GenerateSubtitlesResponse(BaseModel):
    job_id: str
    status: str
    subtitles: dict
    audio_pipeline: dict
    render_manifest_url: str = ""
    message: str


class AssembleFinalVideoResponse(BaseModel):
    job_id: str
    status: str
    final_export: dict
    audio_pipeline: dict
    render_manifest_url: str = ""
    message: str


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/dev-reload-version")
def get_dev_reload_version():
    latest_mtime = max(path.stat().st_mtime for path in DEV_RELOAD_WATCH_FILES if path.exists())
    return {"version": str(latest_mtime)}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/health")
def health_check():
    return {"status": "ok", "server_port": SERVER_PORT}


def normalize_dashboard_state(status: str | None) -> str:
    normalized = (status or "pending").strip().lower()
    if normalized in {"completed", "mock_completed", "ready", "generated", "ok"}:
        return "completed"
    if normalized in {"rendering", "working", "processing", "in_progress"}:
        return "rendering"
    if normalized in {"failed", "error"}:
        return "failed"
    if normalized in {"queued"}:
        return "waiting"
    return "waiting"


def detect_ngrok_status() -> dict:
    public_url = (os.getenv("PUBLIC_BASE_URL") or os.getenv("NGROK_URL") or "").strip()
    online = public_url.startswith(("http://", "https://"))
    return {
        "online": online,
        "state": "completed" if online else "waiting",
        "url": public_url,
    }


def build_provider_status_snapshot() -> dict:
    replicate_token = bool(os.getenv("REPLICATE_API_TOKEN"))
    openai_token = bool(os.getenv("OPENAI_API_KEY"))
    public_url = (os.getenv("PUBLIC_BASE_URL") or os.getenv("NGROK_URL") or "").strip()

    replicate_video = "ready" if replicate_token and public_url else "waiting"
    if replicate_token and not public_url:
        replicate_video = "waiting"
    if not replicate_token:
        replicate_video = "failed"

    replicate_image = replicate_video
    openai_image = "ready" if openai_token else "failed"

    return {
        "mock": {
            "video": {"status": "ready", "state": "completed"},
            "image": {"status": "ready", "state": "completed"},
        },
        "replicate": {
            "video": {"status": replicate_video, "state": normalize_dashboard_state(replicate_video)},
            "image": {"status": replicate_image, "state": normalize_dashboard_state(replicate_image)},
        },
        "openai": {
            "image": {"status": openai_image, "state": normalize_dashboard_state(openai_image)},
        },
    }


def resolve_latest_job_id() -> str | None:
    candidates = [
        path
        for path in GENERATED_CLIPS_DIR.iterdir()
        if path.is_dir() and path.name.startswith("job_")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0].name


def count_generated_images() -> int:
    if not GENERATED_IMAGE_DIR.exists():
        return 0
    return len(list(GENERATED_IMAGE_DIR.glob("cut_*.png")))


def count_generated_clips(job_id: str | None) -> dict:
    if not GENERATED_CLIPS_DIR.exists():
        return {"jobs": 0, "clips": 0}

    job_dirs = [path for path in GENERATED_CLIPS_DIR.iterdir() if path.is_dir() and path.name.startswith("job_")]
    clip_count = 0
    for job_dir in job_dirs:
        if job_id and job_dir.name != job_id:
            continue
        clip_count += len([path for path in job_dir.rglob("*.mp4") if is_usable_mp4(path)])

    return {"jobs": len(job_dirs), "clips": clip_count}


def is_motion_queue_row(row: dict) -> bool:
    cut_number = int(row.get("cut_number", 0))
    if cut_number <= 0:
        return False
    return bool(row.get("motion_enabled", default_motion_enabled_for_cut(cut_number)))


def summarize_video_generation(manifest: dict, job_id: str | None) -> dict:
    clip_rows = manifest.get("clip_metadata") or manifest.get("cuts") or []
    if not clip_rows and job_id:
        clip_rows = [
            {
                "video_status": "completed" if is_usable_mp4(path) else "waiting",
                "cut_number": find_cut_number(path),
                "motion_enabled": default_motion_enabled_for_cut(find_cut_number(path)),
            }
            for path in collect_cut_video_paths(job_id)
        ]

    motion_rows = [row for row in clip_rows if is_motion_queue_row(row)]
    skipped_still_cuts = list(manifest.get("skipped_still_cuts") or [])
    if not skipped_still_cuts:
        skipped_still_cuts = [
            int(row.get("cut_number", 0))
            for row in clip_rows
            if int(row.get("cut_number", 0)) > 0 and not is_motion_queue_row(row)
        ]

    queued = rendering = completed = failed = 0
    for row in motion_rows:
        status = normalize_dashboard_state(row.get("video_status") or row.get("status"))
        if status == "completed":
            completed += 1
        elif status == "rendering":
            rendering += 1
        elif status == "failed":
            failed += 1
        else:
            queued += 1

    overall = "waiting"
    if failed:
        overall = "failed"
    elif rendering:
        overall = "rendering"
    elif motion_rows and completed and queued == 0:
        overall = "completed"
    elif completed:
        overall = "rendering"

    return {
        "state": overall,
        "queued": queued,
        "rendering": rendering,
        "completed": completed,
        "failed": failed,
        "total": len(motion_rows),
        "skipped_still": len(skipped_still_cuts),
        "skipped_still_cuts": skipped_still_cuts,
    }


def count_project_ready_audio(project_dir: Path) -> int:
    script = read_json_file(project_dir / "script.json") or {}
    cut_order = script_export_cut_order(script)
    if not cut_order:
        return len(collect_project_narration_tracks(project_dir))

    tracks = collect_project_narration_tracks_for_script(project_dir, script)
    if len(tracks) < len(cut_order):
        return len(tracks)
    if not script_timing_sync_valid(project_dir, script):
        return 0
    return len(tracks)


def build_files_status(
    job_id: str | None,
    project_dir: Path | None = None,
    current_run: dict | None = None,
) -> dict:
    images = count_generated_images()
    clips = count_generated_clips(job_id)
    selected_cuts = list((current_run or {}).get("selected_cuts") or [])
    audio_count = len([cut for cut in (current_run or {}).get("audio_done", []) if cut in selected_cuts])
    subtitle_count = 0
    manifest_count = 0
    final_video_exists = False
    final_video_path = ""
    final_video_url = ""

    if job_id:
        output_dir = GENERATED_OUTPUTS_DIR / job_id
        subtitles_dir = output_dir / "audio" / "subtitles"
        if subtitles_dir.exists():
            subtitle_count = len(list(subtitles_dir.glob("*.srt")))
        manifest_candidates = [
            GENERATED_CLIPS_DIR / job_id / "render_manifest.json",
            output_dir / "manifest" / "render_manifest.json",
            output_dir / "manifest" / "audio_pipeline.json",
        ]
        manifest_count = len([path for path in manifest_candidates if path.exists()])
        final_candidate = output_dir / "final" / "final_video.mp4"
        if final_candidate.exists() and final_candidate.stat().st_size > 0:
            final_video_exists = True
            final_video_path = str(final_candidate)
            final_video_url = generated_output_url_for_path(final_candidate)

    if current_run:
        final_video_exists = False
        final_video_path = ""
        final_video_url = ""
        subtitle_count = len([cut for cut in current_run.get("subtitle_done", []) if cut in selected_cuts])
        final_export_path = str(current_run.get("final_export_path") or "")
        final_export_selected_cuts = current_run.get("final_export_selected_cuts") or []
        project_final_video = Path(final_export_path) if final_export_path else None
        if (
            project_final_video
            and selected_cuts_match(final_export_selected_cuts, selected_cuts)
            and project_final_video.exists()
            and project_final_video.stat().st_size > 0
        ):
            final_video_exists = True
            final_video_path = str(project_final_video)
            final_video_url = str(current_run.get("final_export_url") or "")
            if not final_video_url and project_dir:
                final_video_url = project_url_for_path(project_final_video)

    return {
        "state": "completed" if final_video_exists else "waiting",
        "generated_images": images,
        "generated_clips_jobs": clips["jobs"],
        "generated_clips": clips["clips"],
        "audio": audio_count,
        "subtitles": subtitle_count,
        "manifests": manifest_count,
        "final_video_exists": final_video_exists,
        "final_video_path": final_video_path,
        "final_video_url": final_video_url,
    }


def build_dashboard_snapshot(job_id: str | None = None, selected_project: str | None = None) -> dict:
    resolved_job_id = job_id or resolve_latest_job_id()
    project_dir: Path | None = None
    if selected_project:
        try:
            project_slug_for_run, project_dir = resolve_project_dir(selected_project)
        except HTTPException:
            project_dir = None
    current_run = load_current_run(project_dir) if project_dir else {}
    if project_dir:
        motion_selection = load_project_motion_selection(project_slug_for_run, project_dir)
        selected_cuts = selected_cuts_from_motion_selection(motion_selection)
        if selected_cuts:
            metadata = load_project_metadata(project_slug_for_run, project_dir)
            current_run = ensure_current_run(
                project_dir,
                selected_cuts,
                target_duration=metadata.get("duration"),
                project=project_slug_for_run,
                topic=metadata.get("topic") or metadata.get("last_topic"),
                style=metadata.get("style"),
                reset_if_changed=True,
            )
    render_manifest_path = find_render_manifest_path(resolved_job_id) if resolved_job_id else None
    manifest = read_json_file(render_manifest_path) if render_manifest_path else {}
    job_metadata = manifest.get("job_metadata", {})
    audio_pipeline: dict = manifest.get("audio_pipeline", {})
    if not audio_pipeline and resolved_job_id:
        audio_pipeline = read_json_file(GENERATED_OUTPUTS_DIR / resolved_job_id / "manifest" / "audio_pipeline.json")
        if not audio_pipeline:
            audio_pipeline = load_or_init_audio_pipeline(resolved_job_id)

    reference_character = manifest.get("reference_character") or job_metadata.get("reference_character") or {}
    reference_frame = manifest.get("reference_frame") or job_metadata.get("reference_frame") or {}
    continuity_strength = (
        reference_frame.get("continuity_inheritance_strength")
        or job_metadata.get("continuity_inheritance_strength")
        or "medium"
    )

    storyboard_export_exists = False
    if resolved_job_id:
        storyboard_export_exists = (GENERATED_CLIPS_DIR / resolved_job_id / "storyboard_export.json").exists()

    video_summary = summarize_video_generation(manifest, resolved_job_id)
    selected_run_cuts = list(current_run.get("selected_cuts") or [])
    if selected_run_cuts:
        video_done = [cut for cut in current_run.get("video_done", []) if cut in selected_run_cuts]
        completed = len(video_done)
        total = len(selected_run_cuts)
        video_summary = {
            **video_summary,
            "completed": completed,
            "queued": max(total - completed, 0),
            "rendering": 0,
            "failed": 0,
            "total": total,
            "state": "completed" if total and completed >= total else ("rendering" if completed else "waiting"),
        }

    def stage_from_audio(key: str) -> dict:
        stage = audio_pipeline.get(key, {})
        raw_status = stage.get("status", "pending")
        return {
            "status": raw_status,
            "state": normalize_dashboard_state(raw_status),
            "audio_count": stage.get("audio_count") or stage.get("inputs", {}).get("audio_count") or 0,
            "cuts": stage.get("cuts") or [],
            "inputs": stage.get("inputs") or {},
            "detail": stage.get("url") or stage.get("path") or stage.get("master_srt_url") or "",
        }

    ngrok = detect_ngrok_status()
    providers = build_provider_status_snapshot()
    files_status = build_files_status(resolved_job_id, project_dir, current_run)
    audio_pipeline_status = {
        "state": normalize_dashboard_state(audio_pipeline.get("final_export", {}).get("status", "pending")),
        "narration": stage_from_audio("narration"),
        "bgm": stage_from_audio("bgm"),
        "subtitles": stage_from_audio("subtitles"),
        "final_export": stage_from_audio("final_export"),
    }
    if selected_run_cuts:
        audio_done = len([cut for cut in current_run.get("audio_done", []) if cut in selected_run_cuts])
        subtitle_done = len([cut for cut in current_run.get("subtitle_done", []) if cut in selected_run_cuts])
        timing_synced = script_timing_sync_valid(project_dir) if project_dir else False
        audio_pipeline_status["narration"]["state"] = (
            "completed"
            if audio_done >= len(selected_run_cuts) and timing_synced
            else "waiting"
        )
        audio_pipeline_status["subtitles"]["state"] = (
            "completed"
            if subtitle_done >= len(selected_run_cuts) and timing_synced
            else "waiting"
        )
        audio_pipeline_status["final_export"]["state"] = "completed" if files_status["final_video_exists"] else "waiting"
        narration_complete = audio_pipeline_status["narration"]["state"] == "completed"
        subtitle_complete = audio_pipeline_status["subtitles"]["state"] == "completed"
        if narration_complete and subtitle_complete:
            audio_pipeline_status["state"] = "completed"
        elif narration_complete or subtitle_complete:
            audio_pipeline_status["state"] = "rendering"
        else:
            audio_pipeline_status["state"] = "waiting"

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "job_id": resolved_job_id or "",
        "render_manifest_url": (
            f"/generated_clips/{resolved_job_id}/render_manifest.json" if render_manifest_path else ""
        ),
        "project": {
            "topic": job_metadata.get("topic", ""),
            "style": job_metadata.get("style", ""),
            "duration": job_metadata.get("duration", 0),
            "image_provider": reference_frame.get("image_provider", "openai"),
            "video_provider": job_metadata.get("provider", manifest.get("provider", "mock")),
            "active_character": DEFAULT_ACTIVE_CHARACTER,
            "state": "completed" if job_metadata.get("topic") else "waiting",
        },
        "storyboard": {
            "generated": storyboard_export_exists or bool(manifest.get("cuts")),
            "cut_count": job_metadata.get("cut_count", len(manifest.get("cuts", []))),
            "continuity_strength": continuity_strength,
            "reference_character": {
                "path": reference_character.get("path", REFERENCE_CHARACTER_PUBLIC_PATH),
                "source_cut_number": reference_character.get("source_cut_number", 0),
                "state": "completed" if reference_character.get("path") else "waiting",
            },
            "state": "completed" if storyboard_export_exists or manifest.get("cuts") else "waiting",
        },
        "video_generation": video_summary,
        "audio_pipeline": audio_pipeline_status,
        "files": files_status,
        "current_run": current_run,
        "system": {
            "server": {"online": True, "state": "completed", "port": SERVER_PORT},
            "ngrok": ngrok,
            "providers": providers,
            "state": "completed",
        },
        "motion_shots": build_motion_shots_summary(manifest),
        "timeline": {
            "duration": job_metadata.get("duration", 0),
            "cuts": [
                {
                    "cut_number": row.get("cut_number", index + 1),
                    "video_status": row.get("video_status") or row.get("status", "waiting"),
                    "state": normalize_dashboard_state(row.get("video_status") or row.get("status")),
                    "motion_enabled": bool(
                        row.get("motion_enabled", default_motion_enabled_for_cut(int(row.get("cut_number", 0))))
                    ),
                    "timeline_type": row.get("timeline_type")
                    or ("motion" if row.get("motion_enabled", True) else "still"),
                }
                for index, row in enumerate(manifest.get("clip_metadata") or [])
            ],
        },
    }


def build_motion_shots_summary(manifest: dict) -> dict:
    clip_rows = manifest.get("clip_metadata") or manifest.get("cuts") or []
    motion_enabled_map = manifest.get("motion_enabled") or {}
    selected: list[int] = []
    still: list[int] = []
    for row in clip_rows:
        cut_number = int(row.get("cut_number", 0))
        if cut_number <= 0:
            continue
        enabled = bool(
            row.get("motion_enabled", motion_enabled_map.get(str(cut_number), default_motion_enabled_for_cut(cut_number)))
        )
        if enabled:
            selected.append(cut_number)
        else:
            still.append(cut_number)
    return {
        "selected_cuts": selected,
        "still_cuts": still,
        "selected_label": ", ".join(f"CUT {number}" for number in selected) or "None",
        "still_label": ", ".join(f"CUT {number}" for number in still) or "None",
        "motion_count": len(selected),
        "still_count": len(still),
    }


@app.get("/dashboard-status")
def get_dashboard_status(
    job_id: str | None = Query(default=None),
    selected_project: str | None = Query(default=None),
):
    return build_dashboard_snapshot(job_id, selected_project)


def clip_url_for_path(path: Path) -> str:
    return f"/generated_clips/{path.relative_to(GENERATED_CLIPS_DIR)}"


def generated_output_url_for_path(path: Path) -> str:
    return f"/generated_outputs/{path.relative_to(GENERATED_OUTPUTS_DIR)}"


def ensure_job_output_layout(job_id: str) -> Path:
    job_dir = GENERATED_OUTPUTS_DIR / job_id
    for relative in (
        "audio/narration",
        "audio/bgm",
        "audio/subtitles",
        "final",
        "manifest",
    ):
        (job_dir / relative).mkdir(parents=True, exist_ok=True)
    return job_dir


def build_default_audio_pipeline_state(job_id: str, output_dir: Path) -> dict:
    return {
        "job_id": job_id,
        "output_dir": str(output_dir),
        "output_url": generated_output_url_for_path(output_dir),
        "video": {"status": "pending"},
        "narration": {
            "status": "pending",
            "provider": "mock",
            "cuts": [],
        },
        "bgm": {
            "status": "pending",
            "provider": "mock",
            "mood": {},
            "path": "",
            "url": "",
        },
        "subtitles": {
            "status": "pending",
            "provider": "mock",
            "master_srt_path": "",
            "master_srt_url": "",
            "cuts": [],
        },
        "final_export": {
            "status": "pending",
            "mode": "mock",
            "path": "",
            "url": "",
        },
    }


def find_render_manifest_path(job_id: str) -> Path | None:
    candidate = GENERATED_CLIPS_DIR / job_id / "render_manifest.json"
    if candidate.exists():
        return candidate
    return None


def sync_audio_pipeline_to_render_manifest(job_id: str, audio_pipeline: dict) -> str:
    render_manifest_path = find_render_manifest_path(job_id)
    if not render_manifest_path:
        fallback_manifest = GENERATED_OUTPUTS_DIR / job_id / "manifest" / "render_manifest.json"
        fallback_manifest.parent.mkdir(parents=True, exist_ok=True)
        render_manifest_path = fallback_manifest

    manifest = read_json_file(render_manifest_path)
    manifest["audio_pipeline"] = audio_pipeline
    pipeline_status = manifest.get("pipeline_status", {})
    pipeline_status["audio"] = audio_pipeline
    pipeline_status["video"] = pipeline_status.get("video", {"status": manifest.get("status", "pending")})
    pipeline_status["subtitles"] = audio_pipeline.get("subtitles", {})
    pipeline_status["final_export"] = audio_pipeline.get("final_export", {})
    manifest["pipeline_status"] = pipeline_status
    write_json(render_manifest_path, manifest)
    write_json(GENERATED_OUTPUTS_DIR / job_id / "manifest" / "audio_pipeline.json", audio_pipeline)

    if render_manifest_path.parent == GENERATED_CLIPS_DIR / job_id:
        return f"/generated_clips/{job_id}/render_manifest.json"
    return generated_output_url_for_path(render_manifest_path)


def default_motion_enabled_for_cut(cut_number: int) -> bool:
    return cut_number in DEFAULT_MOTION_SHOT_CUTS


def resolve_motion_enabled_for_cut(cut_number: int, motion_selection: dict | None) -> bool:
    if not motion_selection:
        return default_motion_enabled_for_cut(cut_number)
    if str(cut_number) in motion_selection:
        return bool(motion_selection[str(cut_number)])
    if cut_number in motion_selection:
        return bool(motion_selection[cut_number])
    return default_motion_enabled_for_cut(cut_number)


def create_still_hold_clip(
    *,
    job_id: str,
    cut_number: int,
    image_path: str,
    still_hold_duration: float,
    image_prompt: str = "",
) -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    cut_name = f"cut_{cut_number:02d}"
    clip_dir = GENERATED_CLIPS_DIR / job_id / cut_name
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip_path = clip_dir / f"{cut_name}.mp4"
    prompt_path = clip_dir / f"{cut_name}_still_hold_prompt.txt"
    manifest_path = clip_dir / f"{cut_name}_clip_manifest.json"
    hold_duration = max(2.0, min(3.0, float(still_hold_duration or STILL_HOLD_DURATION_SECONDS)))

    prompt_path.write_text(
        "\n\n".join(
            [
                f"JOB: {job_id}",
                f"CUT: {cut_number}",
                "TIMELINE TYPE: still",
                f"STILL HOLD DURATION: {hold_duration}s",
                f"IMAGE PATH: {image_path}",
                "IMAGE PROMPT:",
                image_prompt,
            ]
        ),
        encoding="utf-8",
    )

    resolved_image = resolve_local_image_path(image_path)
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path and resolved_image and resolved_image.exists():
        command = [
            ffmpeg_path,
            "-y",
            "-loop",
            "1",
            "-i",
            str(resolved_image),
            "-t",
            str(hold_duration),
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black",
            "-r",
            "24",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(clip_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0 or not is_usable_mp4(clip_path):
            ffmpeg_path = None

    if not ffmpeg_path or not is_usable_mp4(clip_path):
        lavfi_duration = hold_duration
        command = [
            shutil.which("ffmpeg") or "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=#101820:s=1280x720:r=24:d={lavfi_duration}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(clip_path),
        ]
        if shutil.which("ffmpeg"):
            subprocess.run(command, capture_output=True, text=True, check=False)

    if not is_usable_mp4(clip_path):
        placeholder = (
            f"MOCK_STILL_HOLD cut={cut_number}\n"
            f"duration={hold_duration}\n"
            f"image={image_path}\n"
        )
        clip_path.write_bytes(placeholder.encode("utf-8"))

    manifest = {
        "job_id": job_id,
        "provider": "still_hold",
        "status": "mock_completed",
        "created_at": created_at,
        "cut_number": cut_number,
        "motion_enabled": False,
        "timeline_type": "still",
        "still_hold_duration": hold_duration,
        "image_path": image_path,
        "image_prompt": image_prompt,
        "duration": hold_duration,
        "clip_path": str(clip_path),
        "video_prompt_txt": str(prompt_path),
        "message": "Still image hold clip created for timeline export.",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "job_id": job_id,
        "status": "mock_completed",
        "provider": "still_hold",
        "clip_path": str(clip_path),
        "manifest_path": str(manifest_path),
        "prompt_path": str(prompt_path),
        "message": manifest["message"],
        "motion_enabled": False,
        "timeline_type": "still",
        "still_hold_duration": hold_duration,
    }


def find_cut_video_path(job_id: str, cut_number: int) -> Path | None:
    clips_dir = GENERATED_CLIPS_DIR / job_id
    if not clips_dir.exists():
        return None

    cut_name = f"cut_{cut_number:02d}"
    preferred = [
        clips_dir / cut_name / f"{cut_name}.mp4",
        clips_dir / f"{cut_name}.mp4",
        clips_dir / f"{cut_name}_mock.mp4",
    ]
    for candidate in preferred:
        if is_usable_mp4(candidate):
            return candidate

    for video_path in clips_dir.rglob("*.mp4"):
        if find_cut_number(video_path) == cut_number and is_usable_mp4(video_path):
            return video_path
    return None


def load_storyboard_cut_image(job_id: str, cut_number: int) -> tuple[str, str]:
    export_path = GENERATED_CLIPS_DIR / job_id / "storyboard_export.json"
    plan = read_json_file(export_path)
    for cut in plan.get("cuts", []):
        if int(cut.get("cut_number", 0)) == cut_number:
            return cut.get("image_url") or cut.get("sample_image_url") or "", cut.get("image_prompt") or ""
    return "", ""


def ensure_still_hold_segments_for_export(job_id: str) -> None:
    render_manifest_path = find_render_manifest_path(job_id)
    manifest = read_json_file(render_manifest_path) if render_manifest_path else {}
    clip_rows = manifest.get("clip_metadata") or []
    skipped_still_cuts = list(manifest.get("skipped_still_cuts") or [])
    if not skipped_still_cuts:
        skipped_still_cuts = [
            int(row.get("cut_number", 0))
            for row in clip_rows
            if int(row.get("cut_number", 0)) > 0 and not is_motion_queue_row(row)
        ]

    for cut_number in sorted(skipped_still_cuts):
        if find_cut_video_path(job_id, cut_number):
            continue
        image_path, image_prompt = load_storyboard_cut_image(job_id, cut_number)
        if not image_path:
            continue
        still_hold_duration = STILL_HOLD_DURATION_SECONDS
        for row in clip_rows:
            if int(row.get("cut_number", 0)) == cut_number:
                still_hold_duration = float(row.get("still_hold_duration") or STILL_HOLD_DURATION_SECONDS)
                break
        create_still_hold_clip(
            job_id=job_id,
            cut_number=cut_number,
            image_path=image_path,
            still_hold_duration=still_hold_duration,
            image_prompt=image_prompt,
        )
        upsert_render_manifest_clip(
            job_id,
            {
                "cut_number": cut_number,
                "video_status": "still_hold",
                "status": "still_hold",
                "motion_enabled": False,
                "timeline_type": "still",
                "still_hold_duration": still_hold_duration,
                "clip_duration": int(round(still_hold_duration)),
                "provider": "still_hold",
                "video_file": str(GENERATED_CLIPS_DIR / job_id / f"cut_{cut_number:02d}" / f"cut_{cut_number:02d}.mp4"),
                "render_progress": 100,
            },
        )


def collect_timeline_segments(job_id: str) -> list[dict]:
    ensure_still_hold_segments_for_export(job_id)
    render_manifest_path = find_render_manifest_path(job_id)
    manifest = read_json_file(render_manifest_path) if render_manifest_path else {}
    clip_rows = manifest.get("clip_metadata") or manifest.get("cuts") or []
    if not clip_rows:
        for cut_number in sorted({find_cut_number(path) for path in (GENERATED_CLIPS_DIR / job_id).rglob("*.mp4")}):
            if cut_number:
                clip_rows.append({"cut_number": cut_number})

    segments: list[dict] = []
    for row in sorted(clip_rows, key=lambda item: int(item.get("cut_number", 0))):
        cut_number = int(row.get("cut_number", 0))
        if cut_number <= 0:
            continue
        motion_enabled = bool(row.get("motion_enabled", default_motion_enabled_for_cut(cut_number)))
        timeline_type = row.get("timeline_type") or ("motion" if motion_enabled else "still")
        video_path = find_cut_video_path(job_id, cut_number)
        if not video_path:
            continue
        segments.append(
            {
                "cut_number": cut_number,
                "path": video_path,
                "timeline_type": timeline_type,
                "motion_enabled": motion_enabled,
                "still_hold_duration": float(
                    row.get("still_hold_duration") or STILL_HOLD_DURATION_SECONDS
                ),
                "duration": float(
                    row.get("still_hold_duration")
                    if timeline_type == "still"
                    else row.get("clip_duration") or row.get("duration") or STILL_HOLD_DURATION_SECONDS
                ),
            }
        )
    return segments


def collect_cut_video_paths(job_id: str) -> list[Path]:
    segments = collect_timeline_segments(job_id)
    if segments:
        return [segment["path"] for segment in segments]

    clips_dir = GENERATED_CLIPS_DIR / job_id
    if not clips_dir.exists():
        return []

    discovered: list[tuple[int, Path]] = []
    for video_path in sorted(clips_dir.rglob("*.mp4")):
        if not is_usable_mp4(video_path):
            continue
        discovered.append((find_cut_number(video_path), video_path))

    discovered.sort(key=lambda item: item[0])
    return [path for _, path in discovered]


def upsert_render_manifest_clip(job_id: str, clip_payload: dict) -> str:
    render_manifest_path = find_render_manifest_path(job_id)
    if not render_manifest_path:
        render_manifest_path = GENERATED_CLIPS_DIR / job_id / "render_manifest.json"
        render_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest: dict = {"job_id": job_id, "clip_metadata": []}
    else:
        manifest = read_json_file(render_manifest_path)

    clip_metadata = list(manifest.get("clip_metadata") or [])
    cut_number = int(clip_payload.get("cut_number", 0))
    replaced = False
    for index, row in enumerate(clip_metadata):
        if int(row.get("cut_number", 0)) == cut_number:
            clip_metadata[index] = {**row, **clip_payload}
            replaced = True
            break
    if not replaced:
        clip_metadata.append(clip_payload)
    clip_metadata.sort(key=lambda row: int(row.get("cut_number", 0)))
    manifest["clip_metadata"] = clip_metadata
    manifest["motion_enabled"] = {
        str(row.get("cut_number")): bool(row.get("motion_enabled", True)) for row in clip_metadata
    }
    if "skipped_still_cuts" not in manifest:
        manifest["skipped_still_cuts"] = [
            int(row.get("cut_number", 0))
            for row in clip_metadata
            if int(row.get("cut_number", 0)) > 0 and not row.get("motion_enabled", True)
        ]
    write_json(render_manifest_path, manifest)
    return f"/generated_clips/{job_id}/render_manifest.json"


def load_or_init_audio_pipeline(job_id: str) -> dict:
    output_dir = ensure_job_output_layout(job_id)
    saved = read_json_file(output_dir / "manifest" / "audio_pipeline.json")
    if saved:
        return saved
    return build_default_audio_pipeline_state(job_id, output_dir)


def to_narration_cut_inputs(cuts: list[AudioPipelineCutInput]) -> list[NarrationCutInput]:
    return [
        NarrationCutInput(
            cut_number=cut.cut_number,
            narration=cut.narration,
            subtitle=cut.subtitle,
            duration=cut.duration,
        )
        for cut in cuts
    ]


def to_subtitle_cut_inputs(cuts: list[AudioPipelineCutInput]) -> list[SubtitleCutInput]:
    return [
        SubtitleCutInput(
            cut_number=cut.cut_number,
            narration=cut.narration,
            subtitle=cut.subtitle,
            duration=cut.duration,
        )
        for cut in cuts
    ]


def latest_video_url_for_path(path: Path) -> str:
    return f"/latest_videos/{path.relative_to(LATEST_VIDEOS_DIR)}"


def project_url_for_path(path: Path) -> str:
    resolved_path = path.resolve()
    projects_root = PROJECTS_DIR.resolve()
    return f"/projects/{resolved_path.relative_to(projects_root)}"


def list_project_entries() -> list[dict]:
    projects = [
        {
            "slug": path.name,
            "name": path.name,
            "path": str(path),
            "is_default": path.name == DEFAULT_PROJECT_SLUG,
        }
        for path in sorted(PROJECTS_DIR.iterdir(), key=lambda item: item.name)
        if path.is_dir()
    ]
    return sorted(projects, key=lambda item: (not item["is_default"], item["slug"]))


def resolve_project_dir(project_slug: str | None = None) -> tuple[str, Path]:
    slug = project_slug or DEFAULT_PROJECT_SLUG
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        raise HTTPException(status_code=400, detail="Invalid project slug.")

    project_dir = PROJECTS_DIR / slug
    try:
        project_dir.resolve().relative_to(PROJECTS_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid project path.") from exc

    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail="Project not found.")

    return slug, project_dir


def create_project_dir(project_slug: str) -> tuple[str, Path]:
    slug = project_slug.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        raise HTTPException(status_code=400, detail="Project name must use only letters, numbers, hyphens, and underscores.")

    project_dir = PROJECTS_DIR / slug
    try:
        project_dir.resolve().relative_to(PROJECTS_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid project path.") from exc

    project_dir.mkdir(parents=False, exist_ok=True)
    for directory_name in ("images", "clips", "final", "audio", "subtitles", "exports"):
        (project_dir / directory_name).mkdir(exist_ok=True)

    metadata_path = project_dir / "project.json"
    if not metadata_path.exists():
        write_json(
            metadata_path,
            {
                "project": slug,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "directories": ["images", "clips", "final", "audio", "subtitles", "exports"],
            },
        )

    return slug, project_dir


def project_asset_dirs(project_dir: Path) -> dict[str, Path]:
    dirs = {
        "images": project_dir / "images",
        "clips": project_dir / "clips",
        "final": project_dir / "final",
        "audio": project_dir / "audio",
        "subtitles": project_dir / "subtitles",
        "exports": project_dir / "exports",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    return dirs


def project_json_path(project_dir: Path) -> Path:
    return project_dir / "project.json"


def is_placeholder_image_url(url: str | None) -> bool:
    normalized = (url or "").split("?", 1)[0].strip()
    return normalized.startswith("/static/placeholders/")


def clear_project_storyboard_image_cache(project_slug: str, project_dir: Path) -> None:
    image_dir = project_dir / "images"
    if image_dir.is_dir():
        for path in image_dir.glob("cut_*.png"):
            try:
                path.unlink()
            except OSError:
                pass

    metadata = load_project_metadata(project_slug, project_dir)
    metadata["images"] = []
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project_json_path(project_dir), metadata)


def load_project_metadata(project_slug: str, project_dir: Path) -> dict:
    metadata = read_json_file(project_json_path(project_dir))
    if not metadata:
        metadata = {
            "project": project_slug,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "directories": ["images", "clips", "final"],
        }
    metadata.setdefault("project", project_slug)
    metadata.setdefault("images", [])
    metadata.setdefault("clips", [])
    metadata.setdefault("final_video", "")
    return metadata


def append_unique_project_item(items: list, item: dict, key: str) -> list:
    value = item.get(key)
    filtered = [existing for existing in items if existing.get(key) != value]
    filtered.append(item)
    return filtered


def update_project_metadata(
    project_slug: str,
    project_dir: Path,
    *,
    topic: str | None = None,
    duration: int | None = None,
    image: dict | None = None,
    clip: dict | None = None,
    final_video: str | None = None,
) -> dict:
    metadata = load_project_metadata(project_slug, project_dir)
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    if topic:
        metadata["topic"] = topic
        metadata["last_topic"] = topic
        metadata["last_generated_at"] = metadata["updated_at"]
    if duration is not None:
        metadata["duration"] = duration
    if image:
        metadata["images"] = append_unique_project_item(list(metadata.get("images") or []), image, "path")
    if clip:
        metadata["clips"] = append_unique_project_item(list(metadata.get("clips") or []), clip, "path")
    if final_video is not None:
        metadata["final_video"] = final_video
    write_json(project_json_path(project_dir), metadata)
    return metadata


def project_storyboard_path(project_dir: Path) -> Path:
    return project_dir / "storyboard.json"


def build_project_storyboard_payload(topic: str, style: str, cuts: list[dict]) -> dict:
    return {
        "topic": topic,
        "style": style,
        "cuts": cuts,
    }


def load_project_storyboard(project_dir: Path) -> dict:
    return read_json_file(project_storyboard_path(project_dir))


def project_plan_path(project_dir: Path) -> Path:
    return project_dir / "plan.json"


def find_project_storyboard_export(project_slug: str) -> Path | None:
    needle = f"/projects/{project_slug}/"
    candidates: list[Path] = []
    if not GENERATED_CLIPS_DIR.is_dir():
        return None

    for export_path in GENERATED_CLIPS_DIR.glob("*/storyboard_export.json"):
        if not export_path.is_file():
            continue
        try:
            if needle in export_path.read_text(encoding="utf-8"):
                candidates.append(export_path)
        except OSError:
            continue

    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def rebuild_plan_from_storyboard(project_slug: str, project_dir: Path) -> dict | None:
    storyboard = load_project_storyboard(project_dir)
    storyboard_cuts = [
        item for item in storyboard.get("cuts", []) if isinstance(item, dict) and int(item.get("cut") or 0) > 0
    ]
    if not storyboard_cuts:
        return None

    metadata = load_project_metadata(project_slug, project_dir)
    topic = storyboard.get("topic") or metadata.get("topic") or metadata.get("last_topic") or ""
    style = storyboard.get("style") or metadata.get("style") or ""
    duration = int(metadata.get("duration") or max(len(storyboard_cuts) * 2, 10))
    per_cut_duration = max(1, duration // max(len(storyboard_cuts), 1))
    images_by_cut = {
        int(item.get("cut_number") or 0): item for item in metadata.get("images", []) if isinstance(item, dict)
    }

    cuts: list[dict] = []
    for item in sorted(storyboard_cuts, key=lambda row: int(row.get("cut") or 0)):
        cut_number = int(item.get("cut") or 0)
        image_meta = images_by_cut.get(cut_number, {})
        image_path = project_dir / "images" / f"cut_{cut_number}.png"
        image_url = (image_meta.get("url") or "").split("?", 1)[0]
        if not image_url and image_path.exists():
            image_url = project_url_for_path(image_path)
        if image_url and image_path.exists():
            image_url = f"{image_url}?v={int(image_path.stat().st_mtime)}"
        image_status = "generated" if image_path.exists() else "pending"
        description = item.get("description") or item.get("title") or f"CUT {cut_number}"
        motion_prompt = item.get("motion_prompt") or ""
        provider_prompts = item.get("provider_prompts") or {}
        cuts.append(
            {
                "cut_number": cut_number,
                "scene_description": description,
                "narration": description,
                "subtitle": item.get("title") or f"CUT {cut_number}",
                "motion_prompt": motion_prompt,
                "video_prompt": motion_prompt,
                "runway_prompt": provider_prompts.get("runway", motion_prompt),
                "kling_prompt": provider_prompts.get("kling", motion_prompt),
                "veo_prompt": provider_prompts.get("veo", motion_prompt),
                "pika_prompt": provider_prompts.get("replicate", motion_prompt),
                "shot_type": "Cinematic",
                "lens": "35mm prime",
                "movement": motion_prompt or "slow push in",
                "lighting": "cinematic lighting",
                "mood_color": "#8a7765",
                "start_frame_prompt": description,
                "end_frame_prompt": description,
                "camera_path": motion_prompt or "slow push in",
                "environmental_motion": "ambient motion",
                "subject_motion": "natural movement",
                "motion_intensity": "Low",
                "transition_style": "cut",
                "continuity_notes": "",
                "previous_shot_relation": "",
                "next_shot_relation": "",
                "emotional_transition": "",
                "camera_transition": "",
                "spatial_transition": "",
                "pacing_curve": "steady",
                "framing": "16:9",
                "foreground": "",
                "midground": "",
                "background": "",
                "camera_height": "eye level",
                "subject_direction": "",
                "emotional_intensity": "medium",
                "visual_focus": "subject",
                "composition_rule": "rule of thirds",
                "start_frame_description": description,
                "end_frame_description": description,
                "motion_strength": "Low",
                "camera_speed": "slow",
                "transition_duration": "0.5s",
                "cinematic_pacing": "calm",
                "transition": "cut",
                "sound_design": "",
                "background_music": "",
                "pacing": "steady",
                "image_prompt": description,
                "image_url": image_url,
                "sample_image_url": image_url.split("?", 1)[0] if image_url else f"/generated_images/cut_{cut_number}.png",
                "image_status": image_status,
                "image_error": None,
                "recommended_duration": per_cut_duration,
                "active_character": DEFAULT_ACTIVE_CHARACTER,
            }
        )

    if not cuts:
        return None

    return {
        "topic": topic,
        "style": style,
        "duration": duration,
        "cuts": cuts,
    }


def load_saved_project_plan(project_slug: str, project_dir: Path) -> tuple[dict | None, str]:
    plan_path = project_plan_path(project_dir)
    plan = read_json_file(plan_path)
    if plan.get("cuts"):
        return plan, "plan.json"

    export_path = find_project_storyboard_export(project_slug)
    if export_path:
        plan = read_json_file(export_path)
        if plan.get("cuts"):
            write_json(plan_path, plan)
            return plan, "storyboard_export"

    plan = rebuild_plan_from_storyboard(project_slug, project_dir)
    if plan:
        write_json(plan_path, plan)
        return plan, "storyboard.json"

    return None, "none"


def merge_plan_with_project_assets(plan: dict, project_slug: str, project_dir: Path) -> dict:
    metadata = load_project_metadata(project_slug, project_dir)
    storyboard = load_project_storyboard(project_dir)
    storyboard_cuts = {
        int(item.get("cut") or 0): item
        for item in storyboard.get("cuts", [])
        if isinstance(item, dict) and int(item.get("cut") or 0) > 0
    }
    images_by_cut = {
        int(item.get("cut_number") or 0): item
        for item in scan_project_image_library(project_dir)
        if int(item.get("cut_number") or 0) > 0
    }

    for cut in plan.get("cuts") or []:
        if not isinstance(cut, dict):
            continue
        cut_number = int(cut.get("cut_number") or 0)
        if cut_number <= 0:
            continue

        storyboard_cut = storyboard_cuts.get(cut_number, {})
        if storyboard_cut.get("motion_prompt"):
            cut["motion_prompt"] = storyboard_cut["motion_prompt"]
            cut["video_prompt"] = storyboard_cut["motion_prompt"]

        image = images_by_cut.get(cut_number)
        image_path = project_dir / "images" / f"cut_{cut_number}.png"
        image_url = (image or {}).get("image_url") or ""
        if not image_url and image_path.exists():
            image_url = project_url_for_path(image_path)
        if image_url:
            base_url = image_url.split("?", 1)[0]
            stamp = int(image_path.stat().st_mtime) if image_path.exists() else int(time.time())
            cut["image_url"] = f"{base_url}?v={stamp}"
            cut["sample_image_url"] = base_url
            if is_placeholder_image_url(base_url):
                if cut.get("image_status") not in ("failed", "mock"):
                    cut["image_status"] = cut.get("image_status") or "fallback"
            elif image_path.exists() or base_url.startswith(("/projects/", "/generated_images/")):
                if cut.get("image_status") != "failed":
                    cut["image_status"] = "generated"
                    cut["image_error"] = None
            else:
                cut["image_status"] = cut.get("image_status") or "fallback"

    plan["topic"] = plan.get("topic") or storyboard.get("topic") or metadata.get("topic") or metadata.get("last_topic") or ""
    plan["style"] = plan.get("style") or storyboard.get("style") or metadata.get("style") or ""
    plan["duration"] = int(plan.get("duration") or metadata.get("duration") or 10)
    return plan


def load_project_script_optional(project_dir: Path) -> dict | None:
    script = read_json_file(project_dir / "script.json")
    if not script:
        return None
    if script.get("descriptions") or script.get("subtitles") or script.get("narration"):
        return script
    return None


def load_project_motion_selection(project_slug: str, project_dir: Path) -> dict[str, bool]:
    metadata = load_project_metadata(project_slug, project_dir)
    raw = metadata.get("motion_selection") or {}
    if not isinstance(raw, dict):
        return {}

    selection: dict[str, bool] = {}
    for key, value in raw.items():
        try:
            selection[str(int(key))] = bool(value)
        except (TypeError, ValueError):
            continue
    return selection


def current_run_path(project_dir: Path) -> Path:
    return project_dir / "current_run.json"


def selected_cuts_from_motion_selection(motion_selection: dict[str, bool] | None) -> list[int]:
    if not motion_selection:
        return []
    selected: list[int] = []
    for key, enabled in motion_selection.items():
        try:
            cut_number = int(key)
        except (TypeError, ValueError):
            continue
        if cut_number > 0 and enabled:
            selected.append(cut_number)
    return sorted(set(selected))


def selected_cuts_match(left: list[int] | None, right: list[int] | None) -> bool:
    return sorted(int(item) for item in (left or [])) == sorted(int(item) for item in (right or []))


def load_current_run(project_dir: Path) -> dict:
    return read_json_file(current_run_path(project_dir)) or {}


def write_current_run(project_dir: Path, run: dict) -> dict:
    run["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(current_run_path(project_dir), run)
    return run


def normalized_run_text(value: object) -> str:
    return str(value or "").strip()


def current_storyboard_id(project_dir: Path) -> str:
    storyboard_path = project_storyboard_path(project_dir)
    if not storyboard_path.exists():
        return ""
    try:
        return f"{storyboard_path.stat().st_mtime_ns}:{storyboard_path.stat().st_size}"
    except OSError:
        return ""


def ensure_current_run(
    project_dir: Path,
    selected_cuts: list[int],
    *,
    target_duration: float | None = None,
    project: str | None = None,
    topic: str | None = None,
    style: str | None = None,
    storyboard_id: str | None = None,
    reset_if_changed: bool = True,
) -> dict:
    selected_cuts = sorted(set(int(cut) for cut in selected_cuts if int(cut) > 0))
    metadata = load_project_metadata(project_dir.name, project_dir)
    project_value = normalized_run_text(project or project_dir.name)
    topic_value = normalized_run_text(topic if topic is not None else metadata.get("topic") or metadata.get("last_topic"))
    style_value = normalized_run_text(style if style is not None else metadata.get("style"))
    storyboard_value = normalized_run_text(storyboard_id if storyboard_id is not None else current_storyboard_id(project_dir))
    run = load_current_run(project_dir)
    target = float(target_duration or run.get("target_duration") or 0)
    target_changed = bool(target_duration is not None and float(run.get("target_duration") or 0) != target)
    identity_changed = any(
        [
            normalized_run_text(run.get("project")) != project_value,
            normalized_run_text(run.get("topic")) != topic_value,
            normalized_run_text(run.get("style")) != style_value,
            bool(storyboard_value and normalized_run_text(run.get("storyboard_id")) != storyboard_value),
        ]
    )
    should_reset = reset_if_changed and (
        not selected_cuts_match(run.get("selected_cuts"), selected_cuts) or target_changed or identity_changed
    )
    if not run or should_reset:
        run = {
            "run_id": uuid4().hex,
            "project": project_value,
            "topic": topic_value,
            "style": style_value,
            "storyboard_id": storyboard_value,
            "selected_cuts": selected_cuts,
            "target_duration": target,
            "per_cut_duration": round(target / len(selected_cuts), 3) if selected_cuts and target else 0,
            "video_done": [],
            "audio_done": [],
            "subtitle_done": [],
            "final_export_path": "",
            "final_export_url": "",
            "final_export_selected_cuts": [],
            "final_duration": 0,
            "job_id": "",
        }
    else:
        run["project"] = project_value
        run["topic"] = topic_value
        run["style"] = style_value
        run["storyboard_id"] = storyboard_value
        run["selected_cuts"] = selected_cuts
        if target_duration is not None:
            run["target_duration"] = target
        target = float(run.get("target_duration") or 0)
        run["per_cut_duration"] = round(target / len(selected_cuts), 3) if selected_cuts and target else 0
    return write_current_run(project_dir, run)


def update_current_run_done(project_dir: Path, selected_cuts: list[int], key: str, done_cuts: list[int]) -> dict:
    metadata = load_project_metadata(project_dir.name, project_dir)
    run = ensure_current_run(project_dir, selected_cuts, target_duration=metadata.get("duration"))
    allowed = set(run.get("selected_cuts") or [])
    existing = {int(cut) for cut in run.get(key, []) if int(cut) in allowed}
    existing.update(int(cut) for cut in done_cuts if int(cut) in allowed)
    run[key] = sorted(existing)
    return write_current_run(project_dir, run)


def build_restored_video_cuts(
    project_dir: Path,
    plan: dict,
    motion_selection: dict[str, bool] | None,
    current_run: dict | None = None,
) -> tuple[list[dict], str | None]:
    completed_by_cut: dict[int, dict] = {}
    job_ids: set[str] = set()
    run_video_done = set(int(cut) for cut in (current_run or {}).get("video_done", []))
    clips_dir = project_dir / "clips"
    if clips_dir.is_dir():
        for json_path in sorted(clips_dir.glob("cut_*_*.json")):
            data = read_json_file(json_path)
            video_url = data.get("video_url") or data.get("latest_video_url") or ""
            if not video_url:
                continue
            cut_number = int(data.get("cut_number") or find_cut_number(json_path))
            if cut_number <= 0:
                continue
            provider = data.get("provider") or "unknown"
            if provider == "replicate":
                status = "replicate_completed"
            elif provider == "mock":
                status = "mock_completed"
            else:
                status = "completed"
            completed_by_cut[cut_number] = {
                "cut_number": cut_number,
                "video_status": status,
                "status": "completed",
                "render_progress": 100,
                "provider": provider,
                "generator": provider,
                "video_file": video_url,
                "video_url": video_url,
                "clip_duration": int(data.get("duration") or 2),
                "estimated_render_time": max(18, int(data.get("duration") or 2) * 8),
                "motion_enabled": resolve_motion_enabled_for_cut(cut_number, motion_selection),
                "timeline_type": "motion"
                if resolve_motion_enabled_for_cut(cut_number, motion_selection)
                else "still",
                "still_hold_duration": STILL_HOLD_DURATION_SECONDS,
                "job_id": data.get("source_job_id") or "",
            }
            if data.get("source_job_id"):
                job_ids.add(str(data["source_job_id"]))

    restored: list[dict] = []
    for cut in plan.get("cuts") or []:
        if not isinstance(cut, dict):
            continue
        cut_number = int(cut.get("cut_number") or 0)
        if cut_number <= 0:
            continue
        motion_enabled = resolve_motion_enabled_for_cut(cut_number, motion_selection)
        if cut_number in completed_by_cut and cut_number in run_video_done:
            row = completed_by_cut[cut_number]
            row["motion_enabled"] = motion_enabled
            row["timeline_type"] = "motion" if motion_enabled else "still"
            restored.append(row)
            continue
        if not motion_enabled:
            restored.append(
                {
                    "cut_number": cut_number,
                    "video_status": "still_skipped",
                    "status": "still_skipped",
                    "render_progress": 100,
                    "provider": "still",
                    "generator": "still",
                    "video_file": "",
                    "video_url": "",
                    "clip_duration": int(round(STILL_HOLD_DURATION_SECONDS)),
                    "estimated_render_time": 0,
                    "motion_enabled": False,
                    "timeline_type": "still",
                    "still_hold_duration": STILL_HOLD_DURATION_SECONDS,
                    "job_id": "",
                }
            )
            continue
        restored.append(
            {
                "cut_number": cut_number,
                "video_status": "queued",
                "status": "queued",
                "render_progress": 0,
                "provider": "pending",
                "generator": "pending",
                "video_file": "",
                "video_url": "",
                "clip_duration": int(cut.get("recommended_duration") or 2),
                "estimated_render_time": max(18, int(cut.get("recommended_duration") or 2) * 8),
                "motion_enabled": True,
                "timeline_type": "motion",
                "still_hold_duration": STILL_HOLD_DURATION_SECONDS,
                "job_id": "",
            }
        )

    restored.sort(key=lambda item: item["cut_number"])
    job_id = sorted(job_ids)[-1] if job_ids else None
    return restored, job_id


def build_project_restore_payload(project_slug: str) -> ProjectRestoreResponse:
    project_slug, project_dir = resolve_project_dir(project_slug)
    raw_plan, plan_source = load_saved_project_plan(project_slug, project_dir)
    if not raw_plan:
        return ProjectRestoreResponse(project=project_slug, restored=False, plan_source="none")

    merged_plan = merge_plan_with_project_assets(raw_plan, project_slug, project_dir)
    merged_plan = apply_active_character_defaults(merged_plan)
    plan = VideoPlanResponse.model_validate(merged_plan)
    motion_selection = load_project_motion_selection(project_slug, project_dir)
    if not motion_selection:
        motion_selection = {
            str(cut.cut_number): resolve_motion_enabled_for_cut(cut.cut_number, None) for cut in plan.cuts
        }
    selected_cuts = selected_cuts_from_motion_selection(motion_selection)
    metadata = load_project_metadata(project_slug, project_dir)
    current_run = ensure_current_run(
        project_dir,
        selected_cuts,
        target_duration=metadata.get("duration"),
        reset_if_changed=False,
    )
    if not selected_cuts_match(current_run.get("selected_cuts"), selected_cuts):
        current_run = ensure_current_run(
            project_dir,
            selected_cuts,
            target_duration=metadata.get("duration"),
            reset_if_changed=True,
        )
    video_cuts, job_id = build_restored_video_cuts(project_dir, merged_plan, motion_selection, current_run)
    script = load_project_script_optional(project_dir)
    scenario_options_payload = read_json_file(project_dir / "scenario_options.json")
    current_scenario_payload = read_json_file(project_dir / "current_scenario.json")
    scenario_options = [
        ScenarioOption.model_validate(item)
        for item in (scenario_options_payload.get("scenarios") or [])
        if isinstance(item, dict)
    ]
    current_scenario = None
    if current_scenario_payload.get("scenario"):
        current_scenario = ScenarioOption.model_validate(current_scenario_payload["scenario"])
    return ProjectRestoreResponse(
        project=project_slug,
        restored=True,
        plan_source=plan_source,
        plan=plan,
        script=script,
        motion_selection=motion_selection,
        current_run=current_run,
        video_cuts=video_cuts,
        job_id=job_id,
        scenario_options=scenario_options,
        current_scenario=current_scenario,
    )


def update_project_motion_prompt(project_slug: str, project_dir: Path, cut_number: int, motion_prompt: str) -> dict:
    storyboard = load_project_storyboard(project_dir)
    cuts = storyboard.get("cuts", [])
    updated = False
    for cut in cuts:
        if int(cut.get("cut") or 0) == cut_number:
            cut["motion_prompt"] = motion_prompt.strip()
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail=f"CUT {cut_number} not found in storyboard.")

    write_json(project_storyboard_path(project_dir), storyboard)
    metadata = load_project_metadata(project_slug, project_dir)
    motion_prompts = [
        item
        for item in metadata.get("motion_prompts", [])
        if int(item.get("cut") or 0) != cut_number
    ]
    motion_prompts.append({"cut": cut_number, "motion_prompt": motion_prompt.strip()})
    metadata["motion_prompts"] = sorted(motion_prompts, key=lambda item: int(item.get("cut") or 0))
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project_json_path(project_dir), metadata)
    return storyboard


def prompt_summary_from_file(path: Path) -> str:
    if not path.exists():
        return ""

    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:240]


def find_cut_number(path: Path) -> int:
    match = re.search(r"cut[_-]?0*(\d+)", path.stem, re.IGNORECASE)
    if match:
        return int(match.group(1))

    match = re.search(r"cut[_-]?0*(\d+)", str(path.parent), re.IGNORECASE)
    return int(match.group(1)) if match else 0


def is_usable_mp4(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".mp4" and path.stat().st_size > 1024


def scan_clip_job(job_dir: Path) -> dict:
    job_manifest = read_json_file(job_dir / "job_manifest.json")
    render_manifest = read_json_file(job_dir / "render_manifest.json")
    clips = []

    for video_path in sorted(job_dir.rglob("*.mp4")):
        if not is_usable_mp4(video_path):
            continue

        cut_number = find_cut_number(video_path)
        clip_json = read_json_file(video_path.with_suffix(".json"))
        clip_manifest = read_json_file(job_dir / f"cut_{cut_number}_clip_manifest.json")
        if not clip_manifest:
            clip_manifest = read_json_file(video_path.parent / f"cut_{cut_number}_clip_manifest.json")

        metadata = clip_json.get("clip_metadata", {}) if clip_json else {}
        storyboard = clip_json.get("storyboard", {}) if clip_json else {}
        prompt_payload = clip_json.get("prompt", {}) if clip_json else {}
        prompt_path = video_path.parent / "prompt.txt"
        if not prompt_path.exists():
            prompt_path = job_dir / f"cut_{cut_number}_video_prompt.txt"

        clips.append(
            {
                "cut_number": cut_number,
                "video_file": str(video_path),
                "video_url": clip_url_for_path(video_path),
                "provider": clip_manifest.get("provider") or job_manifest.get("provider") or metadata.get("generator") or "unknown",
                "duration": clip_manifest.get("duration") or metadata.get("clip_duration") or 0,
                "cut_type": storyboard.get("cut_type") or clip_manifest.get("cut_type") or job_manifest.get("cut_type") or "UNKNOWN",
                "motion_grammar": prompt_payload.get("motion_grammar") or clip_manifest.get("motion_grammar") or job_manifest.get("motion_grammar") or {},
                "active_character": storyboard.get("active_character") or clip_manifest.get("active_character") or job_manifest.get("active_character") or "",
                "prompt_summary": prompt_summary_from_file(prompt_path),
                "prompt_file": str(prompt_path) if prompt_path.exists() else "",
                "size_bytes": video_path.stat().st_size,
                "modified_at": datetime.fromtimestamp(video_path.stat().st_mtime, timezone.utc).isoformat(),
            }
        )

    clips.sort(key=lambda item: item["cut_number"])
    latest_mtime = max((Path(clip["video_file"]).stat().st_mtime for clip in clips), default=job_dir.stat().st_mtime)
    return {
        "job_id": job_dir.name,
        "job_dir": str(job_dir),
        "created_at": datetime.fromtimestamp(job_dir.stat().st_mtime, timezone.utc).isoformat(),
        "modified_at": datetime.fromtimestamp(latest_mtime, timezone.utc).isoformat(),
        "topic": render_manifest.get("job_metadata", {}).get("topic", ""),
        "style": render_manifest.get("job_metadata", {}).get("style", ""),
        "clip_count": len(clips),
        "clips": clips,
    }


def list_clip_jobs() -> list[dict]:
    jobs = []
    for job_dir in GENERATED_CLIPS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        job = scan_clip_job(job_dir)
        if job["clip_count"]:
            jobs.append(job)

    return sorted(jobs, key=lambda item: item["modified_at"], reverse=True)


@app.get("/clip-gallery/jobs")
def get_clip_gallery_jobs():
    jobs = list_clip_jobs()
    return {
        "latest_job_id": jobs[0]["job_id"] if jobs else "",
        "jobs": [
            {
                "job_id": job["job_id"],
                "modified_at": job["modified_at"],
                "clip_count": job["clip_count"],
                "topic": job["topic"],
                "style": job["style"],
            }
            for job in jobs
        ],
    }


@app.get("/clip-gallery/jobs/{job_id}")
def get_clip_gallery_job(job_id: str):
    job_dir = GENERATED_CLIPS_DIR / job_id
    if not job_dir.exists() or not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Clip gallery job not found")

    job = scan_clip_job(job_dir)
    if not job["clip_count"]:
        raise HTTPException(status_code=404, detail="No generated mp4 clips found for this job")

    return job


def sync_latest_cut_video(
    *,
    clip_path: Path,
    source_job_id: str,
    request: GenerateVideoClipRequest,
    clip_url: str,
    message: str,
) -> str:
    if not is_usable_mp4(clip_path):
        return ""

    selected_project, selected_project_dir = resolve_project_dir(request.selected_project)
    selected_project_dirs = project_asset_dirs(selected_project_dir)
    latest_path = LATEST_VIDEOS_DIR / f"cut_{request.cut_number}.mp4"
    shutil.copy2(clip_path, latest_path)

    metadata = {
        "kind": "cut",
        "cut_number": request.cut_number,
        "cut_type": request.cut_type,
        "visual_style_lock": request.visual_style_lock,
        "action_state": request.action_state,
        "emotion_state": request.emotion_state,
        "motion_state": request.motion_state,
        "allowed_subject_motion": request.allowed_subject_motion,
        "blocked_actions": request.blocked_actions,
        "provider": request.provider,
        "duration": request.duration,
        "motion_grammar": request.motion_grammar,
        "active_character": request.active_character,
        "source_job_id": source_job_id,
        "source_clip_path": str(clip_path),
        "source_clip_url": clip_url,
        "image_prompt": request.image_prompt,
        "character_lock_prompt": request.character_lock_prompt,
        "scene_context_prompt": request.scene_context_prompt,
        "continuity_constraints": request.continuity_constraints,
        "video_file": str(latest_path),
        "video_url": latest_video_url_for_path(latest_path),
        "message": message,
        "project": selected_project,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(LATEST_VIDEOS_DIR / f"cut_{request.cut_number}.json", metadata)
    project_clip_path = selected_project_dirs["clips"] / f"cut_{request.cut_number}_{request.provider}.mp4"
    shutil.copy2(clip_path, project_clip_path)
    project_clip_metadata = {
        **metadata,
        "video_file": str(project_clip_path),
        "video_url": project_url_for_path(project_clip_path),
        "latest_video_url": metadata["video_url"],
    }
    write_json(project_clip_path.with_suffix(".json"), project_clip_metadata)
    update_project_metadata(
        selected_project,
        selected_project_dir,
        clip={
            "cut_number": request.cut_number,
            "path": str(project_clip_path),
            "url": project_clip_metadata["video_url"],
            "provider": request.provider,
            "created_at": metadata["updated_at"],
        },
    )
    project_cut_path = PROJECT_GENERATED_CUTS_DIR / f"cut_{request.cut_number}_{request.provider}.mp4"
    shutil.copy2(clip_path, project_cut_path)
    write_json(
        project_cut_path.with_suffix(".json"),
        {
            **metadata,
            "video_file": str(project_cut_path),
            "video_url": project_url_for_path(project_cut_path),
            "latest_video_url": metadata["video_url"],
        },
    )
    write_latest_videos_manifest()
    return metadata["video_url"]


def sync_latest_from_generated_clips() -> None:
    latest_by_cut: dict[int, Path] = {}
    for video_path in GENERATED_CLIPS_DIR.rglob("*.mp4"):
        if not is_usable_mp4(video_path):
            continue

        cut_number = find_cut_number(video_path)
        if not cut_number:
            continue

        current = latest_by_cut.get(cut_number)
        if current is None or video_path.stat().st_mtime > current.stat().st_mtime:
            latest_by_cut[cut_number] = video_path

    for cut_number, source_path in latest_by_cut.items():
        latest_path = LATEST_VIDEOS_DIR / f"cut_{cut_number}.mp4"
        if is_usable_mp4(latest_path) and latest_path.stat().st_mtime >= source_path.stat().st_mtime:
            continue

        shutil.copy2(source_path, latest_path)
        job_dir = next((parent for parent in source_path.parents if parent.parent == GENERATED_CLIPS_DIR), source_path.parent)
        clip_manifest = read_json_file(source_path.with_suffix(".json"))
        if not clip_manifest:
            clip_manifest = read_json_file(job_dir / f"cut_{cut_number}_clip_manifest.json")
        job_manifest = read_json_file(job_dir / "job_manifest.json")
        metadata = {
            "kind": "cut",
            "cut_number": cut_number,
            "cut_type": clip_manifest.get("cut_type") or job_manifest.get("cut_type") or "UNKNOWN",
            "provider": clip_manifest.get("provider") or job_manifest.get("provider") or "unknown",
            "duration": clip_manifest.get("duration") or job_manifest.get("duration") or 2,
            "motion_grammar": clip_manifest.get("motion_grammar") or job_manifest.get("motion_grammar") or {},
            "active_character": clip_manifest.get("active_character") or job_manifest.get("active_character") or "",
            "source_job_id": job_dir.name,
            "source_clip_path": str(source_path),
            "source_clip_url": clip_url_for_path(source_path),
            "video_file": str(latest_path),
            "video_url": latest_video_url_for_path(latest_path),
            "message": "Imported latest generated_clips mp4 into latest_videos for stitching.",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(latest_path.with_suffix(".json"), metadata)


def scan_latest_videos() -> list[dict]:
    sync_latest_from_generated_clips()
    videos = []
    for video_path in sorted(LATEST_VIDEOS_DIR.glob("*.mp4")):
        if not is_usable_mp4(video_path):
            continue

        metadata = read_json_file(video_path.with_suffix(".json"))
        is_full_video = video_path.name == "full_video.mp4"
        cut_number = metadata.get("cut_number") or (0 if is_full_video else find_cut_number(video_path))
        videos.append(
            {
                "kind": metadata.get("kind") or ("full" if is_full_video else "cut"),
                "cut_number": cut_number,
                "title": "FULL VIDEO" if is_full_video else f"CUT {cut_number}",
                "video_file": str(video_path),
                "video_url": latest_video_url_for_path(video_path),
                "download_url": latest_video_url_for_path(video_path),
                "provider": metadata.get("provider", "unknown"),
                "duration": metadata.get("duration", 0),
                "cut_type": metadata.get("cut_type", "FULL" if is_full_video else "UNKNOWN"),
                "motion_grammar": metadata.get("motion_grammar", {}),
                "active_character": metadata.get("active_character", ""),
                "source_job_id": metadata.get("source_job_id", ""),
                "updated_at": metadata.get(
                    "updated_at",
                    datetime.fromtimestamp(video_path.stat().st_mtime, timezone.utc).isoformat(),
                ),
                "size_bytes": video_path.stat().st_size,
            }
        )

    return sorted(videos, key=lambda item: (item["kind"] != "full", item["cut_number"] or 9999))


def video_metadata_for_project_file(video_path: Path, collection: str) -> dict:
    metadata = read_json_file(video_path.with_suffix(".json"))
    is_full_video = collection in {"final", "full_video"} or video_path.name == "full_video.mp4"
    cut_number = metadata.get("cut_number") or (0 if is_full_video else find_cut_number(video_path))
    return {
        "kind": "full" if is_full_video else "cut",
        "collection": collection,
        "cut_number": cut_number,
        "title": "FULL VIDEO" if is_full_video else f"CUT {cut_number}",
        "video_file": str(video_path),
        "video_url": project_url_for_path(video_path),
        "download_url": project_url_for_path(video_path),
        "provider": metadata.get("provider", "replicate" if collection == "selected_cuts" else "unknown"),
        "duration": metadata.get("duration", 0),
        "cut_type": metadata.get("cut_type", "FULL" if is_full_video else "UNKNOWN"),
        "motion_grammar": metadata.get("motion_grammar", {}),
        "previous_cut_memory": metadata.get("previous_cut_memory", {}),
        "continuity_memory": metadata.get("continuity_memory", {}),
        "active_character": metadata.get("active_character", DEFAULT_ACTIVE_CHARACTER if collection == "selected_cuts" else ""),
        "source_job_id": metadata.get("source_job_id", ""),
        "updated_at": metadata.get(
            "updated_at",
            datetime.fromtimestamp(video_path.stat().st_mtime, timezone.utc).isoformat(),
        ),
        "size_bytes": video_path.stat().st_size,
    }


def scan_project_video_library(project_dir: Path = DEFAULT_PROJECT_DIR) -> list[dict]:
    videos = []
    project_full_video_dir = project_dir / "full_video"
    project_selected_cuts_dir = project_dir / "selected_cuts"
    project_generated_cuts_dir = project_dir / "generated_cuts"
    for collection, directory in (
        ("final", project_dir / "final"),
        ("clips", project_dir / "clips"),
        ("full_video", project_full_video_dir),
        ("selected_cuts", project_selected_cuts_dir),
        ("generated_cuts", project_generated_cuts_dir),
    ):
        if not directory.is_dir():
            continue
        for video_path in sorted(directory.glob("*.mp4")):
            if is_usable_mp4(video_path):
                videos.append(video_metadata_for_project_file(video_path, collection))

    collection_order = {"final": 0, "full_video": 1, "clips": 2, "selected_cuts": 3, "generated_cuts": 4}
    return sorted(
        videos,
        key=lambda item: (collection_order.get(item["collection"], 9), item["cut_number"] or 9999, item["updated_at"]),
    )


def scan_project_image_library(project_dir: Path) -> list[dict]:
    image_dir = project_dir / "images"
    if not image_dir.is_dir():
        return []

    project_metadata = load_project_metadata(project_dir.name, project_dir)
    storyboard = load_project_storyboard(project_dir)
    descriptions_by_cut = {
        int(item.get("cut", 0)): item.get("description", "")
        for item in storyboard.get("cuts", [])
        if isinstance(item, dict) and int(item.get("cut", 0)) > 0
    }
    image_metadata_by_path = {
        item.get("path"): item
        for item in project_metadata.get("images", [])
        if isinstance(item, dict)
    }
    images = []
    for image_path in sorted(image_dir.iterdir()):
        if not image_path.is_file() or image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        cut_number = find_cut_number(image_path)
        image_url = project_url_for_path(image_path)
        metadata = image_metadata_by_path.get(str(image_path), {})
        images.append(
            {
                "kind": "image",
                "cut_number": cut_number,
                "title": f"CUT {cut_number}" if cut_number else image_path.stem,
                "description": metadata.get("description", "") or descriptions_by_cut.get(cut_number, ""),
                "file_name": image_path.name,
                "image_file": str(image_path),
                "image_url": image_url,
                "download_url": image_url,
                "updated_at": datetime.fromtimestamp(image_path.stat().st_mtime, timezone.utc).isoformat(),
                "size_bytes": image_path.stat().st_size,
            }
        )

    return sorted(images, key=lambda item: (item["cut_number"] or 9999, item["file_name"]))


def compact_script_text(text: str, max_chars: int = 34) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    normalized = re.sub(r"^(도입|디테일|중심|연결|마무리)\s*장면[.,]?\s*", "", normalized)
    normalized = normalized.strip(" .")
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip() + "..."


def subtitle_from_description(description: str, cut_number: int) -> str:
    text = compact_script_text(description, 30)
    if not text:
        return f"CUT {cut_number}의 장면이 이어진다"

    if "다른 강아지" in text and "노" in text:
        return "다른 친구들과 즐겁게 뛰노는 뽀식이"
    if "애견카페" in text and ("입장" in text or "들어" in text):
        return "설레는 마음으로 애견카페에 들어서는 순간"
    if "현관" in text and ("멈" in text or "서" in text):
        return "현관 앞에서 잠시 멈춰 선 순간"
    if "소파" in text and ("잠" in text or "눕" in text):
        return "소파 위에 편안히 잠드는 시간"

    subtitle_patterns = [
        "{text}",
        "{text} 순간",
        "{text} 장면",
        "{text}의 리듬",
        "{text} 속으로",
    ]
    return subtitle_patterns[(cut_number - 1) % len(subtitle_patterns)].format(text=text)


def narration_from_description(description: str, cut_number: int, topic: str = "") -> str:
    text = compact_script_text(description, 44)
    topic_text = compact_script_text(topic, 32)
    if not text:
        text = topic_text or f"CUT {cut_number}의 장면"

    if "다른 강아지" in text and "노" in text:
        return "애견카페에 도착한 뽀식이는 금세 친구들과 어울리기 시작한다."
    if "애견카페" in text and ("입장" in text or "들어" in text):
        return "문을 지나 애견카페에 들어선 뽀식이는 새로운 공간을 조심스럽게 둘러본다."
    if "현관" in text and ("멈" in text or "서" in text):
        return "현관 앞에 멈춰 선 뽀식이는 안쪽에서 새어 나오는 온기를 가만히 느낀다."
    if "소파" in text and ("잠" in text or "눕" in text):
        return "하루의 긴장이 풀린 뽀식이는 소파 위에서 천천히 잠에 빠져든다."

    narration_patterns = [
        "이야기는 {text}에서 조용히 시작된다.",
        "카메라는 {text} 장면을 따라가며 분위기를 조금씩 쌓아 올린다.",
        "이 순간, {text}이 장면의 중심으로 선명하게 다가온다.",
        "흐름은 {text}으로 이어지며 다음 감정을 준비한다.",
        "마지막으로 {text}이 남기고 간 여운이 화면을 채운다.",
    ]
    return narration_patterns[(cut_number - 1) % len(narration_patterns)].format(text=text)


def build_project_script_from_storyline(
    project_slug: str,
    project_dir: Path,
    storyline: StorylineScriptPayload | dict,
) -> dict:
    metadata = load_project_metadata(project_slug, project_dir)
    if isinstance(storyline, dict):
        storyline_payload = StorylineScriptPayload.model_validate(storyline)
    else:
        storyline_payload = storyline

    cut_flow_raw = [item for item in (storyline_payload.cut_flow or []) if str(item).strip()]
    if not cut_flow_raw:
        raise HTTPException(status_code=400, detail="Storyline cut_flow is empty.")

    cut_numbers = getattr(storyline_payload, "cut_numbers", []) or []
    topic = (getattr(storyline_payload, "topic", None) or metadata.get("topic") or metadata.get("last_topic") or "").strip()
    summary = (getattr(storyline_payload, "summary", "") or "").strip()
    story_arc = (getattr(storyline_payload, "story_arc", "") or "").strip()
    duration = int(metadata.get("duration") or max(len(cut_flow_raw) * 3, 15))
    per_cut = duration / len(cut_flow_raw)

    descriptions = []
    subtitles = []
    narration = []
    items = []
    missing_text_cuts: list[int] = []
    video_paths = collect_project_cut_video_paths(project_dir)

    for index, raw_entry in enumerate(cut_flow_raw):
        if index < len(cut_numbers) and int(cut_numbers[index]) > 0:
            cut_number = int(cut_numbers[index])
        else:
            cut_number = index + 1

        narration_text = ""
        subtitle_text = ""
        if isinstance(raw_entry, dict):
            narration_text = str(raw_entry.get("narration") or raw_entry.get("subtitle") or raw_entry.get("scene") or "").strip()
            subtitle_text = str(raw_entry.get("subtitle") or narration_text).strip()
        else:
            narration_text = str(raw_entry).strip()
            subtitle_text = narration_text

        if not narration_text:
            missing_text_cuts.append(cut_number)
            continue

        start = round(index * per_cut, 2)
        end = round(duration if index == len(cut_flow_raw) - 1 else (index + 1) * per_cut, 2)
        descriptions.append({"cut": cut_number, "text": subtitle_text})
        subtitles.append({"cut": cut_number, "text": subtitle_text, "start": start, "end": end})
        narration.append({"cut": cut_number, "text": narration_text})

        items.append(
            {
                "cut": cut_number,
                "text": narration_text,
                "subtitle": subtitle_text,
                "video_path": str(video_paths.get(cut_number) or ""),
                "audio_path": "",
                "duration": round(end - start, 2),
            }
        )

    if missing_text_cuts:
        raise HTTPException(
            status_code=400,
            detail=f"Script text missing for cuts: {missing_text_cuts}",
        )
    if cut_numbers and len(items) != len(cut_numbers):
        raise HTTPException(
            status_code=400,
            detail=f"Script item count mismatch: selected={len(cut_numbers)} items={len(items)}.",
        )

    script = {
        "project": project_slug,
        "topic": topic,
        "duration": duration,
        "storyline_summary": summary,
        "story_arc": story_arc,
        "selected_cuts": [item["cut"] for item in items],
        "items": items,
        "descriptions": descriptions,
        "subtitles": subtitles,
        "narration": narration,
        "source": "storyline",
    }
    write_json(project_dir / "storyline.json", storyline_payload.model_dump())
    write_json(project_dir / "script.json", script)
    sync_script_to_export_timing_plan(project_dir, script)
    run = ensure_current_run(
        project_dir,
        script["selected_cuts"],
        target_duration=script.get("duration"),
        reset_if_changed=True,
    )
    run["audio_done"] = []
    run["subtitle_done"] = []
    run["final_export_path"] = ""
    run["final_export_url"] = ""
    run["final_export_selected_cuts"] = []
    write_current_run(project_dir, run)
    print(f"[script] selected cuts: {script['selected_cuts']}")
    print(f"[script] items: {len(items)}")
    return script


def build_project_script(project_slug: str, project_dir: Path) -> dict:
    metadata = load_project_metadata(project_slug, project_dir)
    storyboard = load_project_storyboard(project_dir)
    storyboard_items = [
        item
        for item in storyboard.get("cuts", [])
        if isinstance(item, dict) and int(item.get("cut") or 0) > 0
    ]
    storyboard_items.sort(key=lambda item: int(item.get("cut") or 0))

    if storyboard_items:
        script_items = [
            {
                "cut_number": int(item.get("cut") or 0),
                "description": item.get("description") or item.get("title") or f"CUT {item.get('cut')}",
            }
            for item in storyboard_items
        ]
    else:
        image_items = [
            item
            for item in metadata.get("images", [])
            if isinstance(item, dict) and int(item.get("cut_number") or 0) > 0
        ]
        image_items.sort(key=lambda item: int(item.get("cut_number") or 0))
        script_items = image_items or [
            {
                "cut_number": image["cut_number"],
                "description": image.get("description") or image.get("title") or f"CUT {image['cut_number']}",
            }
            for image in scan_project_image_library(project_dir)
            if int(image.get("cut_number") or 0) > 0
        ]

    duration = int(metadata.get("duration") or max(len(script_items) * 3, 0))
    per_cut = max(1, duration / len(script_items)) if script_items else 0
    descriptions = []
    subtitles = []
    narration = []
    items = []
    video_paths = collect_project_cut_video_paths(project_dir)
    topic = storyboard.get("topic") or metadata.get("topic") or metadata.get("last_topic") or ""

    for index, item in enumerate(script_items):
        cut_number = int(item.get("cut_number") or index + 1)
        description = (item.get("description") or f"CUT {cut_number} scene").strip()
        start = round(index * per_cut, 2)
        end = round(duration if index == len(script_items) - 1 else (index + 1) * per_cut, 2)
        descriptions.append({"cut": cut_number, "text": description})
        subtitles.append({"cut": cut_number, "text": subtitle_from_description(description, cut_number), "start": start, "end": end})
        narration.append({"cut": cut_number, "text": narration_from_description(description, cut_number, topic)})
        items.append(
            {
                "cut": cut_number,
                "text": narration[-1]["text"],
                "subtitle": subtitles[-1]["text"],
                "video_path": str(video_paths.get(cut_number) or ""),
                "audio_path": "",
                "duration": round(end - start, 2),
            }
        )

    script = {
        "project": project_slug,
        "topic": topic,
        "duration": duration,
        "selected_cuts": [item["cut"] for item in items],
        "items": items,
        "descriptions": descriptions,
        "subtitles": subtitles,
        "narration": narration,
    }
    write_json(project_dir / "script.json", script)
    sync_script_to_export_timing_plan(project_dir, script)
    run = ensure_current_run(
        project_dir,
        script["selected_cuts"],
        target_duration=script.get("duration"),
        reset_if_changed=True,
    )
    run["video_done"] = []
    run["audio_done"] = []
    run["subtitle_done"] = []
    run["final_export_path"] = ""
    run["final_export_url"] = ""
    run["final_export_selected_cuts"] = []
    write_current_run(project_dir, run)
    return script


def load_project_script(project_dir: Path) -> dict:
    script = read_json_file(project_dir / "script.json")
    if not script:
        raise HTTPException(status_code=400, detail="script.json not found. Generate Script first.")
    narration = script.get("narration") or []
    if not narration:
        raise HTTPException(status_code=400, detail="script.json has no narration entries.")
    return script


def project_narration_cut_inputs_from_script(script: dict) -> list[ProjectVoiceCutInput]:
    cut_order = script_export_cut_order(script)
    slot_durations = export_timing_durations(script, cut_order)
    narration_by_cut = _script_entries_by_cut(script.get("narration") or [])
    return [
        ProjectVoiceCutInput(
            cut_number=cut_number,
            narration=(narration_by_cut[cut_number].get("text") or "").strip(),
            duration=max(0.1, float(slot_durations.get(cut_number) or 3.0)),
        )
        for cut_number in cut_order
        if cut_number in narration_by_cut and (narration_by_cut[cut_number].get("text") or "").strip()
    ]


def summarize_voice_results(results: list) -> tuple[str, str]:
    providers = {getattr(result, "provider", "mock") for result in results}
    if not providers:
        return "mock", "mock_completed"
    if providers == {"openai"}:
        return "openai", "completed"
    if providers == {"mock"}:
        return "mock", "mock_completed"
    return "openai+mock", "partial_completed"


def build_project_voice_manifest(project_slug: str, results: list) -> dict:
    provider, status = summarize_voice_results(results)
    return {
        "project": project_slug,
        "provider": provider,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tracks": [
            {
                "cut_number": result.cut_number,
                "text": result.text,
                "status": result.status,
                "provider": result.provider,
                "mp3_path": result.mp3_path,
                "audio_url": result.public_url,
                "duration_seconds": result.duration_seconds,
            }
            for result in results
        ],
    }


def update_script_items_with_voice_results(project_dir: Path, results: list) -> None:
    script = read_json_file(project_dir / "script.json") or {}
    items = script.get("items") or []
    if not items:
        return

    slot_durations = export_timing_durations(script)
    changed = False
    for item in items:
        cut_number = int(item.get("cut") or 0)
        track = next((result for result in results if getattr(result, "cut_number", None) == cut_number), None)
        if not track:
            continue
        audio_path = str(track.mp3_path)
        duration_seconds = getattr(track, "duration_seconds", None)
        if item.get("audio_path") != audio_path:
            item["audio_path"] = audio_path
            changed = True
        if duration_seconds is not None and item.get("voice_duration") != duration_seconds:
            item["voice_duration"] = duration_seconds
            changed = True
        slot_duration = slot_durations.get(cut_number)
        if slot_duration and item.get("duration") != slot_duration:
            item["duration"] = slot_duration
            changed = True

    if changed:
        write_json(project_dir / "script.json", script)


def scan_project_audio_library(project_dir: Path) -> list[dict]:
    audio_dir = project_dir / "audio"
    if not audio_dir.exists():
        return []

    items: list[dict] = []
    for mp3_path in sorted(audio_dir.glob("narration_*.mp3")):
        match = re.fullmatch(r"narration_(\d+)", mp3_path.stem)
        if not match:
            continue

        cut_number = int(match.group(1))
        sidecar_path = audio_dir / f"narration_{cut_number:02d}.json"
        sidecar = read_json_file(sidecar_path) if sidecar_path.exists() else {}
        items.append(
            {
                "kind": "audio",
                "cut_number": cut_number,
                "title": f"Narration {cut_number:02d}",
                "text": sidecar.get("text") or "",
                "provider": sidecar.get("provider") or "mock",
                "status": sidecar.get("status") or "mock_completed",
                "duration": sidecar.get("duration_seconds"),
                "audio_url": project_url_for_path(mp3_path),
                "download_url": project_url_for_path(mp3_path),
                "path": str(mp3_path),
            }
        )

    return sorted(items, key=lambda item: int(item.get("cut_number") or 0))


def collect_project_narration_tracks(project_dir: Path) -> list[Path]:
    audio_dir = project_dir / "audio"
    if not audio_dir.exists():
        return []
    tracks = [path for path in audio_dir.glob("*.mp3") if path.is_file()]

    def sort_key(path: Path) -> tuple[int, str]:
        match = re.search(r"(\d+)", path.stem)
        return (int(match.group(1)) if match else 999, path.name)

    return sorted(tracks, key=sort_key)


def find_narration_cut_number(path: Path) -> int:
    match = re.search(r"narration[_-]?0*(\d+)", path.stem, re.IGNORECASE)
    return int(match.group(1)) if match else find_cut_number(path)


def script_export_cut_order(script: dict) -> list[int]:
    selected = script.get("selected_cuts") or []
    selected_numbers = sorted({int(cut) for cut in selected if int(cut) > 0})
    if selected_numbers:
        return selected_numbers

    timing_plan_cuts = script.get("timing_plan", {}).get("selected_cuts") or []
    timing_numbers = sorted({int(cut) for cut in timing_plan_cuts if int(cut) > 0})
    if timing_numbers:
        return timing_numbers

    narration = script.get("narration") or []
    if narration:
        return [
            int(item.get("cut") or 0)
            for item in narration
            if int(item.get("cut") or 0) > 0 and (item.get("text") or "").strip()
        ]

    subtitles = script.get("subtitles") or []
    return [
        int(item.get("cut") or 0)
        for item in subtitles
        if int(item.get("cut") or 0) > 0 and (item.get("text") or "").strip()
    ]


def resolve_export_selected_cuts(
    project_dir: Path | None,
    script: dict,
    explicit_selected_cuts: list[int] | None = None,
) -> list[int]:
    if explicit_selected_cuts:
        return sorted({int(cut) for cut in explicit_selected_cuts if int(cut) > 0})

    if project_dir is not None:
        run_cuts = load_current_run(project_dir).get("selected_cuts") or []
        run_numbers = sorted({int(cut) for cut in run_cuts if int(cut) > 0})
        if run_numbers:
            return run_numbers

        project_slug = project_dir.name
        motion_selection = load_project_motion_selection(project_slug, project_dir)
        motion_cuts = selected_cuts_from_motion_selection(motion_selection)
        if motion_cuts:
            return motion_cuts

    return script_export_cut_order(script)


def _script_entries_by_cut(entries: list[dict]) -> dict[int, dict]:
    mapped: dict[int, dict] = {}
    for entry in entries or []:
        cut_number = int(entry.get("cut") or entry.get("cut_number") or 0)
        if cut_number > 0:
            mapped[cut_number] = entry
    return mapped


def filter_script_to_selected_cuts(script: dict, selected_cuts: list[int]) -> dict:
    if not selected_cuts:
        return script

    items_by_cut = _script_entries_by_cut(script.get("items") or [])
    narration_by_cut = _script_entries_by_cut(script.get("narration") or [])
    subtitles_by_cut = _script_entries_by_cut(script.get("subtitles") or [])
    descriptions_by_cut = _script_entries_by_cut(script.get("descriptions") or [])

    script["selected_cuts"] = selected_cuts
    script["items"] = [items_by_cut[cut_number] for cut_number in selected_cuts if cut_number in items_by_cut]
    script["narration"] = [
        narration_by_cut[cut_number] for cut_number in selected_cuts if cut_number in narration_by_cut
    ]
    script["subtitles"] = [
        subtitles_by_cut[cut_number] for cut_number in selected_cuts if cut_number in subtitles_by_cut
    ]
    script["descriptions"] = [
        descriptions_by_cut[cut_number] for cut_number in selected_cuts if cut_number in descriptions_by_cut
    ]
    return script


def reconcile_script_export_cuts(
    project_dir: Path,
    script: dict | None = None,
    *,
    selected_cuts: list[int] | None = None,
) -> dict:
    if script is None:
        script = read_json_file(project_dir / "script.json") or {}

    resolved_cuts = resolve_export_selected_cuts(project_dir, script, selected_cuts)
    if not resolved_cuts:
        return script

    previous_plan_cuts = script.get("timing_plan", {}).get("selected_cuts") or []
    script = filter_script_to_selected_cuts(script, resolved_cuts)
    if not selected_cuts_match(previous_plan_cuts, resolved_cuts):
        script.pop("timing_plan", None)

    script = sync_script_to_export_timing_plan(project_dir, script)
    run = ensure_current_run(
        project_dir,
        resolved_cuts,
        target_duration=script.get("duration"),
        reset_if_changed=False,
    )
    if not selected_cuts_match(run.get("selected_cuts"), resolved_cuts):
        run["selected_cuts"] = resolved_cuts
        run["per_cut_duration"] = round(
            float(script.get("duration") or 0) / len(resolved_cuts),
            3,
        ) if resolved_cuts and script.get("duration") else 0
        run["audio_done"] = [cut for cut in run.get("audio_done", []) if cut in resolved_cuts]
        run["subtitle_done"] = [cut for cut in run.get("subtitle_done", []) if cut in resolved_cuts]
        write_current_run(project_dir, run)
    return script


def build_export_timing_plan(script: dict) -> list[dict[str, float | int]]:
    cut_order = script_export_cut_order(script)
    target_duration = float(script.get("duration") or 0)
    if not cut_order:
        return []

    if target_duration <= 0:
        target_duration = max(len(cut_order) * 3.0, 3.0)

    per_cut = target_duration / len(cut_order)
    cursor = 0.0
    plan: list[dict[str, float | int]] = []
    for index, cut_number in enumerate(cut_order):
        if index == len(cut_order) - 1:
            end = round(target_duration, 3)
        else:
            end = round((index + 1) * per_cut, 3)
        start = round(cursor, 3)
        duration = round(max(end - start, 0.1), 3)
        cursor = end
        plan.append(
            {
                "cut": int(cut_number),
                "start": start,
                "end": end,
                "duration": duration,
            }
        )
    return plan


def log_export_timing_plan(script: dict) -> list[dict[str, float | int]]:
    plan = build_export_timing_plan(script)
    target_duration = float(script.get("duration") or 0)
    selected_cuts = [int(item["cut"]) for item in plan]
    per_cut = target_duration / len(selected_cuts) if selected_cuts and target_duration else 0
    print(f"[sync] selected cuts: {selected_cuts}")
    print(f"[sync] total duration: {target_duration:g}")
    print(f"[sync] per cut: {per_cut:.3f}")
    print("[sync] timing plan:")
    for item in plan:
        print(f"  cut {item['cut']}: {item['start']:.2f}-{item['end']:.2f}")
    return plan


def export_timing_durations(script: dict, cut_numbers: list[int] | None = None) -> dict[int, float]:
    plan = build_export_timing_plan(script)
    durations = {int(item["cut"]): float(item["duration"]) for item in plan}
    if cut_numbers:
        return {cut_number: durations[cut_number] for cut_number in cut_numbers if cut_number in durations}
    return durations


def sync_script_to_export_timing_plan(project_dir: Path, script: dict | None = None) -> dict:
    if script is None:
        script = read_json_file(project_dir / "script.json") or {}
    plan = build_export_timing_plan(script)
    if not plan:
        return script

    plan_by_cut = {int(item["cut"]): item for item in plan}
    for item in script.get("items") or []:
        cut_number = int(item.get("cut") or 0)
        timing = plan_by_cut.get(cut_number)
        if timing:
            item["duration"] = timing["duration"]

    for subtitle in script.get("subtitles") or []:
        cut_number = int(subtitle.get("cut") or 0)
        timing = plan_by_cut.get(cut_number)
        if timing:
            subtitle["start"] = timing["start"]
            subtitle["end"] = timing["end"]

    script["timing_plan"] = {
        "target_duration": float(script.get("duration") or 0),
        "selected_cuts": [int(item["cut"]) for item in plan],
        "cuts": plan,
    }
    write_json(project_dir / "script.json", script)
    return script


def parse_srt_last_end_seconds(srt_path: Path) -> float:
    if not srt_path.exists():
        return 0.0

    matches = re.findall(
        r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})",
        srt_path.read_text(encoding="utf-8", errors="ignore"),
    )
    if not matches:
        return 0.0

    hours, minutes, seconds, millis = matches[-1]
    return round(int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0, 3)


def script_timing_sync_valid(project_dir: Path, script: dict | None = None, tolerance: float = 0.5) -> bool:
    if script is None:
        script = read_json_file(project_dir / "script.json") or {}
    target_duration = float(script.get("duration") or 0)
    plan = script.get("timing_plan", {}).get("cuts") or build_export_timing_plan(script)
    if not plan or not target_duration:
        return False

    plan_target = float(script.get("timing_plan", {}).get("target_duration") or target_duration)
    if abs(plan_target - target_duration) > tolerance:
        return False

    plan_selected = script.get("timing_plan", {}).get("selected_cuts") or []
    export_selected = script_export_cut_order(script)
    if plan_selected and not selected_cuts_match(plan_selected, export_selected):
        return False

    last_plan_end = float(plan[-1].get("end") or 0)
    if abs(last_plan_end - target_duration) > tolerance:
        return False

    srt_path = project_dir / "subtitles" / "subtitle.srt"
    if srt_path.exists():
        subtitle_last_end = parse_srt_last_end_seconds(srt_path)
        if subtitle_last_end and abs(subtitle_last_end - target_duration) > tolerance:
            return False

    narration_track = project_dir / "exports" / "narration_track.mp3"
    if narration_track.exists() and is_valid_media_file(narration_track):
        audio_duration = float(probe_audio_metadata(narration_track).get("duration_seconds") or 0)
        if audio_duration and abs(audio_duration - target_duration) > tolerance:
            return False

    return True


def build_project_synced_narration_track(project_dir: Path, script: dict) -> Path | None:
    cut_order = script_export_cut_order(script)
    if not cut_order:
        return None

    slot_durations = export_timing_durations(script, cut_order)
    source_tracks = collect_project_narration_tracks_for_script(project_dir, script)
    if not source_tracks:
        return None

    exports_dir = project_dir / "exports"
    log_path = exports_dir / "ffmpeg_sync_audio.log"
    log_path.write_text("Synced narration track log\n", encoding="utf-8")
    padded_dir = exports_dir / "padded_narration"
    padded_tracks = build_synced_narration_tracks(source_tracks, slot_durations, padded_dir, log_path)

    narration_track_path = exports_dir / "narration_track.mp3"
    if len(padded_tracks) == 1:
        shutil.copy2(padded_tracks[0], narration_track_path)
    else:
        concat_narration_tracks(padded_tracks, narration_track_path, log_path)

    audio_duration = float(probe_audio_metadata(narration_track_path).get("duration_seconds") or 0)
    target_duration = float(script.get("duration") or 0)
    print(f"[sync] narration total duration: {audio_duration:.2f}")
    if target_duration and audio_duration and abs(audio_duration - target_duration) > 0.5:
        print("[sync] audio duration mismatch detected")
    return narration_track_path


def project_narration_mp3_path(project_dir: Path, cut_number: int) -> Path | None:
    audio_dir = project_dir / "audio"
    for name in (f"narration_{cut_number:02d}.mp3", f"narration_{cut_number}.mp3"):
        candidate = audio_dir / name
        if candidate.is_file():
            return candidate
    return None


def load_audio_manifest_tracks(project_dir: Path) -> dict[int, dict]:
    manifest = read_json_file(project_dir / "audio" / "manifest.json") or {}
    tracks: dict[int, dict] = {}
    for item in manifest.get("tracks") or []:
        cut_number = int(item.get("cut_number") or 0)
        if cut_number > 0:
            tracks[cut_number] = item
    return tracks


def resolve_cut_audio_duration_seconds(project_dir: Path, cut_number: int, fallback: float = 3.0) -> float:
    manifest_tracks = load_audio_manifest_tracks(project_dir)
    manifest_duration = manifest_tracks.get(cut_number, {}).get("duration_seconds")
    if manifest_duration:
        return max(0.1, float(manifest_duration))

    sidecar = read_json_file(project_dir / "audio" / f"narration_{cut_number:02d}.json") or {}
    sidecar_duration = sidecar.get("duration_seconds")
    if sidecar_duration:
        return max(0.1, float(sidecar_duration))

    mp3_path = project_narration_mp3_path(project_dir, cut_number)
    if mp3_path:
        return max(0.1, probe_mp3_duration(mp3_path, fallback))

    return max(0.1, fallback)


def resolve_cut_video_duration_seconds(project_dir: Path, cut_number: int, fallback: float = 3.0) -> float:
    cut_paths = collect_project_cut_video_paths(project_dir)
    video_path = cut_paths.get(cut_number)
    if video_path and video_path.exists():
        probed = probe_video_metadata(video_path)
        duration = probed.get("duration_seconds")
        if duration:
            return max(0.1, float(duration))
    return max(0.1, fallback)


def collect_project_narration_tracks_for_script(project_dir: Path, script: dict) -> list[Path]:
    tracks: list[Path] = []
    for cut_number in script_export_cut_order(script):
        mp3_path = project_narration_mp3_path(project_dir, cut_number)
        if mp3_path:
            tracks.append(mp3_path)
    return tracks


def generate_project_voice(project_slug: str, project_dir: Path, selected_cuts: list[int] | None = None) -> dict:
    script = reconcile_script_export_cuts(project_dir, selected_cuts=selected_cuts)
    log_export_timing_plan(script)
    cut_inputs = project_narration_cut_inputs_from_script(script)
    if not cut_inputs:
        raise HTTPException(status_code=400, detail="No narration text found in script.json.")

    project_dirs = project_asset_dirs(project_dir)
    voice_generator = ProjectVoiceGenerator(project_dirs["audio"], public_url_builder=project_url_for_path)
    results = []
    for cut_input in cut_inputs:
        print(f"[voice] start cut {cut_input.cut_number}")
        try:
            result = voice_generator.generate_cut(cut_input)
        except Exception as error:
            raise HTTPException(
                status_code=400,
                detail=f"CUT {cut_input.cut_number} voice generation failed: {error}",
            ) from error
        results.append(result)
        print(f"[voice] done cut {cut_input.cut_number}")
    manifest = build_project_voice_manifest(project_slug, results)
    write_json(project_dirs["audio"] / "manifest.json", manifest)

    update_script_items_with_voice_results(project_dir, results)
    script = sync_script_to_export_timing_plan(project_dir)
    log_export_timing_plan(script)
    build_project_synced_narration_track(project_dir, script)
    selected_cuts = script_export_cut_order(script)
    if script_timing_sync_valid(project_dir, script):
        update_current_run_done(project_dir, selected_cuts, "audio_done", [result.cut_number for result in results])
    else:
        print("[sync] audio timing not yet aligned with export plan")

    metadata = load_project_metadata(project_slug, project_dir)
    metadata["audio"] = manifest

    bgm_dir = project_dirs["audio"] / "bgm"
    bgm_generator = BgmGenerator(bgm_dir)
    bgm_result = bgm_generator.generate(metadata.get("topic", ""), metadata.get("style", ""))
    metadata["bgm"] = {
        "status": bgm_result.status,
        "provider": bgm_result.provider,
        "mp3_path": bgm_result.mp3_path,
        "public_url": bgm_result.public_url,
        "mood": bgm_result.mood.mood,
        "tempo": bgm_result.mood.tempo,
        "instrumentation": bgm_result.mood.instrumentation,
        "energy": bgm_result.mood.energy,
        "style_tags": bgm_result.mood.style_tags,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project_json_path(project_dir), metadata)

    openai_count = sum(1 for result in results if result.provider == "openai")
    mock_count = sum(1 for result in results if result.provider == "mock")
    if mock_count == 0:
        voice_message = (
            f"Generated {len(results)} narration mp3 files with OpenAI TTS "
            f"(gpt-4o-mini-tts, alloy) from script.json."
        )
    elif openai_count == 0:
        voice_message = (
            f"Generated {len(results)} mock narration files from script.json "
            "(OpenAI TTS unavailable or failed)."
        )
    else:
        voice_message = (
            f"Generated {len(results)} narration files from script.json "
            f"({openai_count} OpenAI TTS, {mock_count} mock fallback)."
        )

    return {
        "project": project_slug,
        "audio_dir": str(project_dirs["audio"]),
        "manifest_file": str(project_dirs["audio"] / "manifest.json"),
        "manifest_url": project_url_for_path(project_dirs["audio"] / "manifest.json"),
        "audio_count": len(results),
        "openai_count": openai_count,
        "mock_count": mock_count,
        "tracks": manifest["tracks"],
        "audio": scan_project_audio_library(project_dir),
        "bgm": metadata["bgm"],
        "message": voice_message,
    }


def load_project_script_for_subtitles(project_dir: Path) -> dict:
    script = read_json_file(project_dir / "script.json")
    if not script:
        raise HTTPException(status_code=400, detail="script.json not found. Generate Script first.")
    subtitles = script.get("subtitles") or []
    if not subtitles:
        raise HTTPException(status_code=400, detail="script.json has no subtitle entries.")
    return script


def project_subtitle_cues_from_script(
    script: dict,
    project_dir: Path | None = None,
) -> list[ProjectSubtitleCueInput]:
    subtitle_items = script.get("subtitles") or []
    subtitles_by_cut = {
        int(item.get("cut") or 0): item
        for item in subtitle_items
        if int(item.get("cut") or 0) > 0
    }
    cut_order = script_export_cut_order(script) or sorted(subtitles_by_cut)
    cues: list[ProjectSubtitleCueInput] = []
    timing_plan = build_export_timing_plan(script)
    timing_by_cut = {int(item["cut"]): item for item in timing_plan}

    for cut_number in cut_order:
        item = subtitles_by_cut.get(cut_number)
        if not item:
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue

        timing = timing_by_cut.get(cut_number)
        if timing:
            start = float(timing["start"])
            end = float(timing["end"])
        elif item.get("start") is not None and item.get("end") is not None:
            start = float(item.get("start") or 0)
            end = float(item.get("end") or start + 1)
        else:
            duration = float(script.get("duration") or 0)
            per_cut = duration / len(cut_order) if cut_order and duration else 3.0
            index = cut_order.index(cut_number)
            start = round(index * per_cut, 3)
            end = round(duration if index == len(cut_order) - 1 and duration else (index + 1) * per_cut, 3)

        cues.append(
            ProjectSubtitleCueInput(
                cut_number=cut_number,
                text=text,
                start=start,
                end=end,
            )
        )

    return cues


def refresh_project_subtitle_from_timeline(
    project_slug: str,
    project_dir: Path,
    script: dict | None = None,
    *,
    selected_cuts: list[int] | None = None,
) -> Path:
    script = reconcile_script_export_cuts(project_dir, script, selected_cuts=selected_cuts)
    log_export_timing_plan(script)
    cues = project_subtitle_cues_from_script(script, project_dir)
    if not cues:
        raise HTTPException(status_code=400, detail="No subtitle text found for export timeline.")
    expected_cuts = script_export_cut_order(script)
    if expected_cuts and len(cues) != len(expected_cuts):
        cue_cuts = [cue.cut_number for cue in cues]
        missing_cuts = [cut_number for cut_number in expected_cuts if cut_number not in cue_cuts]
        raise HTTPException(
            status_code=400,
            detail=f"Subtitle generation failed for cuts: {missing_cuts}",
        )

    project_dirs = project_asset_dirs(project_dir)
    subtitle_generator = ProjectSubtitleGenerator(
        project_dirs["subtitles"],
        public_url_builder=project_url_for_path,
    )
    result = subtitle_generator.generate(cues)
    manifest = build_project_subtitle_manifest(project_slug, result, cues)
    write_json(project_dirs["subtitles"] / "manifest.json", manifest)
    update_current_run_done(
        project_dir,
        script_export_cut_order(script),
        "subtitle_done",
        [cue.cut_number for cue in cues],
    )

    total_seconds = cues[-1].end if cues else 0
    subtitle_last_end = parse_srt_last_end_seconds(project_dirs["subtitles"] / "subtitle.srt")
    print(f"[sync] subtitle last end: {subtitle_last_end:.2f}")
    print(
        f"[subtitle] regenerated export timeline project={project_slug} "
        f"cues={len(cues)} total={total_seconds}s"
    )
    print(f"[subtitle] blocks: {len(cues)}")
    return project_dirs["subtitles"] / "subtitle.srt"


def build_project_subtitle_manifest(project_slug: str, result, cues: list[ProjectSubtitleCueInput]) -> dict:
    return {
        "project": project_slug,
        "provider": result.provider,
        "status": result.status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "subtitle_file": "subtitle.srt",
        "srt_path": result.srt_path,
        "subtitle_url": result.public_url,
        "cue_count": result.cue_count,
        "cues": [
            {
                "cut_number": cue.cut_number,
                "text": cue.text,
                "start": cue.start,
                "end": cue.end,
            }
            for cue in cues
        ],
    }


def scan_project_subtitle_library(project_dir: Path) -> list[dict]:
    subtitles_dir = project_dir / "subtitles"
    srt_path = subtitles_dir / "subtitle.srt"
    if not srt_path.exists():
        return []

    manifest = read_json_file(subtitles_dir / "manifest.json")
    return [
        {
            "kind": "subtitle",
            "title": "subtitle.srt",
            "provider": manifest.get("provider") or "mock",
            "status": manifest.get("status") or "mock_completed",
            "cue_count": manifest.get("cue_count") or 0,
            "subtitle_url": project_url_for_path(srt_path),
            "download_url": project_url_for_path(srt_path),
            "path": str(srt_path),
        }
    ]


def collect_project_subtitle_file(project_dir: Path) -> Path | None:
    srt_path = project_dir / "subtitles" / "subtitle.srt"
    return srt_path if srt_path.exists() else None


def generate_project_subtitle(project_slug: str, project_dir: Path, selected_cuts: list[int] | None = None) -> dict:
    script = reconcile_script_export_cuts(project_dir, selected_cuts=selected_cuts)
    log_export_timing_plan(script)
    cues = project_subtitle_cues_from_script(script, project_dir)
    if not cues:
        raise HTTPException(status_code=400, detail="No subtitle text found in script.json.")

    project_dirs = project_asset_dirs(project_dir)
    subtitle_generator = ProjectSubtitleGenerator(
        project_dirs["subtitles"],
        public_url_builder=project_url_for_path,
    )
    result = subtitle_generator.generate(cues)
    manifest = build_project_subtitle_manifest(project_slug, result, cues)
    write_json(project_dirs["subtitles"] / "manifest.json", manifest)
    update_current_run_done(
        project_dir,
        script_export_cut_order(script),
        "subtitle_done",
        [cue.cut_number for cue in cues],
    )
    subtitle_last_end = parse_srt_last_end_seconds(project_dirs["subtitles"] / "subtitle.srt")
    print(f"[sync] subtitle last end: {subtitle_last_end:.2f}")
    if not script_timing_sync_valid(project_dir, script):
        print("[sync] subtitle timing not yet aligned with export plan")

    metadata = load_project_metadata(project_slug, project_dir)
    metadata["subtitles"] = manifest
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project_json_path(project_dir), metadata)

    return {
        "project": project_slug,
        "subtitles_dir": str(project_dirs["subtitles"]),
        "manifest_file": str(project_dirs["subtitles"] / "manifest.json"),
        "manifest_url": project_url_for_path(project_dirs["subtitles"] / "manifest.json"),
        "subtitle_file": str(project_dirs["subtitles"] / "subtitle.srt"),
        "subtitle_url": result.public_url,
        "cue_count": result.cue_count,
        "subtitles": scan_project_subtitle_library(project_dir),
        "message": "Mock subtitle.srt was generated from script.json subtitles.",
    }


def project_full_video_path(project_dir: Path) -> Path:
    return project_dir / "final" / "full_video.mp4"


PROJECT_CUT_VIDEO_DIRS = ("clips", "generated_cuts", "selected_cuts")


def collect_project_cut_video_paths(project_dir: Path) -> dict[int, Path]:
    cut_paths: dict[int, Path] = {}
    for collection in PROJECT_CUT_VIDEO_DIRS:
        directory = project_dir / collection
        if not directory.is_dir():
            continue
        for video_path in sorted(directory.glob("*.mp4")):
            if not is_usable_mp4(video_path):
                continue
            cut_number = find_cut_number(video_path)
            if cut_number <= 0:
                continue
            cut_paths.setdefault(cut_number, video_path)
    return dict(sorted(cut_paths.items()))


def write_project_full_video_metadata(
    target_path: Path,
    project_slug: str,
    *,
    source_path: Path | None = None,
    cut_count: int = 0,
    method: str = "",
) -> None:
    source_metadata = read_json_file(source_path.with_suffix(".json")) if source_path else {}
    probed = probe_video_metadata(target_path)
    duration = source_metadata.get("duration") or probed.get("duration_seconds") or 0
    metadata = {
        "kind": "full",
        "cut_number": 0,
        "cut_type": "FULL",
        "provider": source_metadata.get("provider", "project"),
        "duration": duration,
        "video_file": str(target_path),
        "video_url": project_url_for_path(target_path),
        "project": project_slug,
        "prepare_method": method,
        "source_cuts": [source_path.name] if source_path else [],
        "cut_count": cut_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(target_path.with_suffix(".json"), metadata)


def full_video_cut_numbers_from_metadata(metadata: dict) -> list[int]:
    timeline = metadata.get("timeline") or []
    if timeline:
        return [
            int(item.get("cut_number") or 0)
            for item in timeline
            if int(item.get("cut_number") or 0) > 0
        ]
    return [
        find_cut_number(Path(name))
        for name in (metadata.get("source_cuts") or [])
        if find_cut_number(Path(name)) > 0
    ]


def project_full_video_matches_cut_order(
    target_path: Path,
    expected_cut_numbers: list[int],
    expected_duration: float | None = None,
) -> bool:
    if not expected_cut_numbers:
        return is_usable_mp4(target_path)
    if not is_usable_mp4(target_path):
        return False
    metadata = read_json_file(target_path.with_suffix(".json"))
    if full_video_cut_numbers_from_metadata(metadata) != expected_cut_numbers:
        return False
    if expected_duration:
        actual_duration = float(metadata.get("duration") or probe_video_metadata(target_path).get("duration_seconds") or 0)
        return abs(actual_duration - float(expected_duration)) <= 0.5
    return True


def selected_cut_durations_from_script(script: dict, cut_numbers: list[int]) -> dict[int, float]:
    return export_timing_durations(script, cut_numbers)


def export_per_cut_duration(script: dict, cut_numbers: list[int]) -> float:
    target_duration = float(script.get("duration") or 0)
    if target_duration and cut_numbers:
        return max(0.1, target_duration / len(cut_numbers))
    return 3.0


def export_video_cut_durations(script: dict, cut_numbers: list[int]) -> dict[int, float]:
    per_cut = export_per_cut_duration(script, cut_numbers)
    return {cut_number: per_cut for cut_number in cut_numbers}


def stitch_export_full_video_normalized(project_slug: str, project_dir: Path, script: dict) -> dict:
    cut_numbers = script_export_cut_order(script)
    target_duration = float(script.get("duration") or 0)
    per_cut_duration = export_per_cut_duration(script, cut_numbers)
    print(f"[duration] target total: {target_duration:g}")
    print(f"[duration] selected cuts: {len(cut_numbers)}")
    print(f"[duration] per cut: {per_cut_duration:g}")

    available_cut_paths = collect_project_cut_video_paths(project_dir)
    missing_cuts = [cut_number for cut_number in cut_numbers if cut_number not in available_cut_paths]
    if missing_cuts:
        raise HTTPException(status_code=400, detail=f"Missing video files for selected cuts: {missing_cuts}")

    normalized_dir = project_dir / "exports" / "normalized_cuts"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    log_path = project_dir / "exports" / "ffmpeg_normalize.log"
    log_path.write_text("Final export normalized cut log\n", encoding="utf-8")

    normalized_paths: list[Path] = []
    timeline_payload = []
    for cut_number in cut_numbers:
        source_path = available_cut_paths[cut_number]
        normalized_path = normalized_dir / f"normalized_cut_{cut_number:02d}.mp4"
        normalize_export_cut_video(source_path, normalized_path, per_cut_duration, log_path)
        normalized_paths.append(normalized_path)
        timeline_payload.append(
            {
                "cut_number": cut_number,
                "selected": True,
                "transition_type": "cut",
                "transition_duration": 0.0,
                "duration": per_cut_duration,
                "source_file": str(source_path),
                "normalized_file": str(normalized_path),
            }
        )

    target_path = project_full_video_path(project_dir)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    stitch_normalized_export_videos(normalized_paths, target_path, log_path)

    probed = probe_video_metadata(target_path)
    actual_duration = float(probed.get("duration_seconds") or 0)
    print(f"[duration] full video actual: {actual_duration:g}")

    metadata = {
        "kind": "full",
        "cut_number": 0,
        "cut_type": "FULL",
        "provider": "export-normalized",
        "duration": actual_duration,
        "target_duration": target_duration,
        "per_cut_duration": per_cut_duration,
        "source_cuts": [Path(item["source_file"]).name for item in timeline_payload],
        "normalized_cuts": [Path(item["normalized_file"]).name for item in timeline_payload],
        "timeline": timeline_payload,
        "ffmpeg_mode": "concat-normalized",
        "video_file": str(target_path),
        "video_url": project_url_for_path(target_path),
        "project": project_slug,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(target_path.with_suffix(".json"), metadata)
    update_project_metadata(project_slug, project_dir, final_video=str(target_path))

    return {
        "built": True,
        "method": "normalized_concat",
        "path": str(target_path),
        "cut_count": len(cut_numbers),
        "target_duration": target_duration,
        "per_cut_duration": per_cut_duration,
        "actual_duration": actual_duration,
        "normalized_dir": str(normalized_dir),
        "message": f"Stitched {len(cut_numbers)} normalized cuts into final/full_video.mp4.",
    }


def script_duration_for_cut(script: dict, cut_number: int, fallback: float = 3.0) -> float:
    durations = selected_cut_durations_from_script(script, script_export_cut_order(script))
    return max(0.1, float(durations.get(cut_number) or fallback))


def project_timeline_cuts_for_script(project_dir: Path, script: dict, cut_numbers: list[int]) -> list[DirectorTimelineCut]:
    available_cut_paths = collect_project_cut_video_paths(project_dir)
    missing_cuts = [cut_number for cut_number in cut_numbers if cut_number not in available_cut_paths]
    if missing_cuts:
        raise HTTPException(status_code=400, detail=f"Missing video files for selected cuts: {missing_cuts}")

    target_durations = export_video_cut_durations(script, cut_numbers)
    timeline_cuts: list[DirectorTimelineCut] = []
    for cut_number in cut_numbers:
        video_path = available_cut_paths[cut_number]
        duration = target_durations[cut_number]
        timeline_cuts.append(
            DirectorTimelineCut(
                cut_number=cut_number,
                selected=True,
                transition_type="cut",
                transition_duration=0,
                duration=max(0.1, float(duration)),
                video_file=str(video_path),
            )
        )
    return timeline_cuts


def ensure_project_full_video(project_slug: str, project_dir: Path, script: dict | None = None) -> dict:
    target_path = project_full_video_path(project_dir)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    expected_cut_numbers = script_export_cut_order(script or {})
    expected_duration = float((script or {}).get("duration") or 0)

    if project_full_video_matches_cut_order(target_path, expected_cut_numbers, expected_duration):
        return {
            "built": False,
            "method": "existing",
            "path": str(target_path),
            "message": "Using existing final/full_video.mp4.",
        }

    metadata = load_project_metadata(project_slug, project_dir)
    final_video_ref = metadata.get("final_video")
    if final_video_ref:
        candidate = Path(final_video_ref)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        if (
            project_full_video_matches_cut_order(candidate, expected_cut_numbers, expected_duration)
            and candidate.resolve() != target_path.resolve()
        ):
            shutil.copy2(candidate, target_path)
            write_project_full_video_metadata(
                target_path,
                project_slug,
                source_path=candidate,
                cut_count=1,
                method="copy_metadata_ref",
            )
            update_project_metadata(project_slug, project_dir, final_video=str(target_path))
            print(f"[full-video] copied metadata ref {candidate} -> {target_path}")
            return {
                "built": True,
                "method": "copy_metadata_ref",
                "source": str(candidate),
                "path": str(target_path),
                "message": "Prepared final/full_video.mp4 from project metadata.",
            }

    alternate_full = project_dir / "full_video" / "full_video.mp4"
    if project_full_video_matches_cut_order(alternate_full, expected_cut_numbers, expected_duration):
        shutil.copy2(alternate_full, target_path)
        write_project_full_video_metadata(
            target_path,
            project_slug,
            source_path=alternate_full,
            cut_count=1,
            method="copy_full_video_dir",
        )
        update_project_metadata(project_slug, project_dir, final_video=str(target_path))
        print(f"[full-video] copied {alternate_full} -> {target_path}")
        return {
            "built": True,
            "method": "copy_full_video_dir",
            "source": str(alternate_full),
            "path": str(target_path),
            "message": "Prepared final/full_video.mp4 from full_video/full_video.mp4.",
        }

    cut_paths = collect_project_cut_video_paths(project_dir)
    if not cut_paths:
        return {
            "built": False,
            "method": "missing",
            "path": str(target_path),
            "cut_count": 0,
            "message": "No project cut mp4 files found to build full_video.mp4.",
        }

    if expected_cut_numbers and expected_duration:
        return stitch_export_full_video_normalized(project_slug, project_dir, script or {})

    if len(cut_paths) == 1:
        source_path = next(iter(cut_paths.values()))
        shutil.copy2(source_path, target_path)
        write_project_full_video_metadata(
            target_path,
            project_slug,
            source_path=source_path,
            cut_count=1,
            method="copy_single_cut",
        )
        update_project_metadata(project_slug, project_dir, final_video=str(target_path))
        print(f"[full-video] copied single cut {source_path} -> {target_path}")
        return {
            "built": True,
            "method": "copy_single_cut",
            "source": str(source_path),
            "path": str(target_path),
            "cut_count": 1,
            "message": f"Prepared final/full_video.mp4 from {source_path.name}.",
        }

    timeline_cuts = project_timeline_cuts_for_script(project_dir, script or {}, expected_cut_numbers) if expected_cut_numbers else None
    input_count = len(timeline_cuts) if timeline_cuts is not None else len(cut_paths)
    print(f"[full-video] stitching {input_count} cuts for project={project_slug}")
    print(f"[final] video inputs: {input_count}")
    build_latest_full_video_file(timeline_cuts=timeline_cuts, selected_project=project_slug)
    if not is_usable_mp4(target_path):
        raise HTTPException(
            status_code=400,
            detail="Failed to stitch project cuts into final/full_video.mp4.",
        )
    return {
        "built": True,
        "method": "stitched",
        "path": str(target_path),
        "cut_count": input_count,
        "message": f"Stitched {input_count} cuts into final/full_video.mp4.",
    }


def validate_project_export_assets(project_dir: Path, script: dict | None = None) -> dict:
    video_path = project_full_video_path(project_dir)
    if script is None:
        script = read_json_file(project_dir / "script.json") or {}
    cut_order = script_export_cut_order(script)
    if cut_order:
        audio_tracks = collect_project_narration_tracks_for_script(project_dir, script)
        audio_cuts = {find_narration_cut_number(path) for path in audio_tracks}
    else:
        audio_tracks = collect_project_narration_tracks(project_dir)
        audio_cuts = {find_narration_cut_number(path) for path in audio_tracks}
    subtitle_path = collect_project_subtitle_file(project_dir)
    missing: list[str] = []

    if not video_path.exists():
        missing.append("final/full_video.mp4")
    if cut_order:
        missing_audio_cuts = [cut_number for cut_number in cut_order if cut_number not in audio_cuts]
        if missing_audio_cuts:
            missing.append(f"audio for cuts {missing_audio_cuts}")
    elif not audio_tracks:
        missing.append("audio/*.mp3")
    if not subtitle_path:
        missing.append("subtitles/subtitle.srt")

    return {
        "ready": not missing,
        "missing": missing,
        "video_path": video_path if video_path.exists() else None,
        "audio_tracks": audio_tracks,
        "subtitle_path": subtitle_path,
        "audio_count": len(audio_tracks),
    }


def scan_project_export_library(project_dir: Path) -> list[dict]:
    export_manifest_path = project_dir / "exports" / "final_export.json"
    if not export_manifest_path.exists():
        return []

    manifest = read_json_file(export_manifest_path)
    final_video_path = project_dir / "exports" / "final_export.mp4"
    has_output = final_video_path.exists() and final_video_path.stat().st_size >= 1024
    if not has_output:
        return []

    media_metadata = probe_video_metadata(final_video_path)
    duration = manifest.get("duration") or media_metadata.get("duration") or "unknown"
    resolution = manifest.get("resolution") or media_metadata.get("resolution") or "unknown"
    file_size = manifest.get("file_size") or media_metadata.get("file_size") or "unknown"
    log_path = project_dir / "exports" / "ffmpeg_export.log"

    return [
        {
            "kind": "export",
            "title": "Final Export",
            "project": manifest.get("project") or project_dir.name,
            "status": manifest.get("status") or "ready",
            "video": manifest.get("video") or "",
            "audio_count": manifest.get("audio_count") or 0,
            "subtitle": manifest.get("subtitle") or "",
            "output": manifest.get("output") or "exports/final_export.mp4",
            "duration": duration,
            "duration_seconds": manifest.get("duration_seconds") or media_metadata.get("duration_seconds"),
            "resolution": resolution,
            "file_size": file_size,
            "file_size_bytes": manifest.get("file_size_bytes") or media_metadata.get("file_size_bytes") or 0,
            "export_url": project_url_for_path(export_manifest_path),
            "video_url": project_url_for_path(final_video_path),
            "download_url": project_url_for_path(final_video_path),
            "ffmpeg_log_url": project_url_for_path(log_path) if log_path.exists() else None,
            "path": str(export_manifest_path),
        }
    ]


def collect_project_final_export_manifest(project_dir: Path) -> Path | None:
    manifest_path = project_dir / "exports" / "final_export.json"
    return manifest_path if manifest_path.exists() else None


def generate_project_final_export(project_slug: str, project_dir: Path, selected_cuts: list[int] | None = None) -> dict:
    print(f"[final-export] assembling project={project_slug} dir={project_dir}")
    script = reconcile_script_export_cuts(project_dir, selected_cuts=selected_cuts)
    log_export_timing_plan(script)
    selected_cut_order = script_export_cut_order(script)
    target_duration = float(script.get("duration") or 0)
    per_cut_duration = target_duration / len(selected_cut_order) if selected_cut_order and target_duration else 0
    print(f"[post] selected cuts: {selected_cut_order}")
    print(f"[duration] target total: {target_duration:g}")
    print(f"[duration] selected cuts: {len(selected_cut_order)}")
    print(f"[duration] per cut: {per_cut_duration:g}")
    full_video_prepare = ensure_project_full_video(project_slug, project_dir, script)
    if full_video_prepare.get("built"):
        print(
            f"[final-export] prepared full video project={project_slug} "
            f"method={full_video_prepare.get('method')} cuts={full_video_prepare.get('cut_count', 0)}"
        )
    refresh_project_subtitle_from_timeline(project_slug, project_dir, script)
    build_project_synced_narration_track(project_dir, script)
    validation = validate_project_export_assets(project_dir, script)
    if not validation["ready"]:
        missing = ", ".join(validation["missing"])
        print(f"[final-export] assets missing project={project_slug} missing={missing}")
        detail = f"Export assets missing: {missing}."
        if "final/full_video.mp4" in validation["missing"]:
            detail += " Generate Video or Export Full Video first."
        else:
            detail += " Generate Full Video, Voice, and Subtitle first."
        raise HTTPException(status_code=400, detail=detail)

    print(
        f"[final-export] timeline cuts={script_export_cut_order(script)} "
        f"audio_tracks={[path.name for path in validation['audio_tracks']]}"
    )
    print(f"[final] audio inputs: {len(validation['audio_tracks'])}")

    project_dirs = project_asset_dirs(project_dir)
    output_path = project_dirs["exports"] / "final_export.mp4"

    bgm_track = project_dirs["audio"] / "bgm" / "bgm.mp3"
    usable_bgm_track = bgm_track if bgm_track.exists() and is_valid_media_file(bgm_track) else None
    if bgm_track.exists() and usable_bgm_track is None:
        print(f"[final-export] skipping invalid bgm project={project_slug} path={bgm_track}")
    try:
        audio_durations = export_timing_durations(script, script_export_cut_order(script))
        export_result = assemble_project_final_export(
            ProjectFinalExportInput(
                video_path=validation["video_path"],
                audio_tracks=validation["audio_tracks"],
                subtitle_path=validation["subtitle_path"],
                output_path=output_path,
                bgm_track=usable_bgm_track,
                audio_durations=audio_durations,
            )
        )
    except FFmpegNotInstalledError as error:
        raise HTTPException(status_code=400, detail="ffmpeg not installed") from error
    except FFmpegRunError as error:
        detail = error.args[0] if error.args else "ffmpeg export failed."
        if error.log_path:
            detail = f"{detail}\n\nffmpeg log: {error.log_path}"
        raise HTTPException(status_code=400, detail=detail) from error
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not output_path.exists() or output_path.stat().st_size < 1024:
        raise HTTPException(status_code=400, detail="ffmpeg finished but final_export.mp4 was not created.")

    actual_duration = float(export_result.duration_seconds or 0)
    print(f"[duration] final actual: {actual_duration:g}")
    subtitle_last_end = parse_srt_last_end_seconds(validation["subtitle_path"])
    print(f"[sync] subtitle last end: {subtitle_last_end:.2f}")
    narration_track = project_dir / "exports" / "narration_track.mp3"
    if narration_track.exists():
        final_audio_duration = float(probe_audio_metadata(narration_track).get("duration_seconds") or 0)
        print(f"[sync] narration total duration: {final_audio_duration:.2f}")
    if target_duration and abs(actual_duration - target_duration) > 0.5:
        print("[duration] mismatch detected")
        raise HTTPException(
            status_code=400,
            detail=(
                f"Final export duration mismatch: expected {target_duration:g}s, "
                f"got {actual_duration:g}s. Check exports/ffmpeg_export.log and exports/ffmpeg_normalize.log."
            ),
        )

    export_manifest = {
        "project": project_slug,
        "video": "final/full_video.mp4",
        "audio_count": validation["audio_count"],
        "narration_track": "exports/narration_track.mp3",
        "bgm": "audio/bgm/bgm.mp3" if usable_bgm_track else None,
        "subtitle": "subtitles/subtitle.srt",
        "status": "ready",
        "output": "exports/final_export.mp4",
        "target_duration": target_duration,
        "per_cut_duration": round(per_cut_duration, 3) if per_cut_duration else 0,
        "duration_seconds": export_result.duration_seconds,
        "duration": export_result.duration,
        "resolution": export_result.resolution,
        "file_size_bytes": export_result.file_size_bytes,
        "file_size": export_result.file_size,
        "export_logs": export_result.export_logs,
        "probe_summary": export_result.probe_summary,
    }
    export_manifest_path = project_dirs["exports"] / "final_export.json"
    write_json(export_manifest_path, export_manifest)
    run = ensure_current_run(
        project_dir,
        script_export_cut_order(script),
        target_duration=script.get("duration"),
        reset_if_changed=False,
    )
    selected_cuts = list(run.get("selected_cuts") or script_export_cut_order(script))
    run["video_done"] = selected_cuts
    run["audio_done"] = selected_cuts
    run["subtitle_done"] = selected_cuts
    run["final_export_path"] = str(output_path)
    run["final_export_url"] = project_url_for_path(output_path)
    run["final_export_selected_cuts"] = selected_cuts
    run["target_duration"] = target_duration
    run["per_cut_duration"] = round(per_cut_duration, 3) if per_cut_duration else 0
    run["final_duration"] = export_result.duration_seconds or 0
    write_current_run(project_dir, run)

    metadata = load_project_metadata(project_slug, project_dir)
    metadata["final_export"] = {
        **export_manifest,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(export_manifest_path),
        "output_path": str(output_path),
        "ffmpeg_command": export_result.ffmpeg_command,
    }
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project_json_path(project_dir), metadata)

    exports = scan_project_export_library(project_dir)
    print(
        f"[final-export] success project={project_slug} "
        f"output={output_path} duration={export_result.duration} size={export_result.file_size}"
    )
    return {
        "project": project_slug,
        "full_video_prepare": full_video_prepare,
        "exports_dir": str(project_dirs["exports"]),
        "manifest_file": str(export_manifest_path),
        "manifest_url": project_url_for_path(export_manifest_path),
        "output_file": str(output_path),
        "output_url": project_url_for_path(output_path),
        "ffmpeg_command": export_result.ffmpeg_command,
        "ffmpeg_log_file": export_result.ffmpeg_log_path,
        "ffmpeg_log_url": project_url_for_path(Path(export_result.ffmpeg_log_path))
        if export_result.ffmpeg_log_path
        else None,
        "narration_track_file": export_result.narration_track_path,
        "narration_track_url": project_url_for_path(Path(export_result.narration_track_path))
        if export_result.narration_track_path
        else None,
        "export_logs": export_result.export_logs,
        "probe_summary": export_result.probe_summary,
        "final_export": export_manifest,
        "exports": exports,
        "duration": export_result.duration,
        "resolution": export_result.resolution,
        "file_size": export_result.file_size,
        "message": export_result.message,
    }


def write_latest_videos_manifest() -> None:
    videos = scan_latest_videos()
    write_json(
        LATEST_VIDEOS_DIR / "manifest.json",
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "video_count": len(videos),
            "videos": videos,
        },
    )


def transition_filter_name(transition_type: str) -> str:
    normalized = (transition_type or "cut").strip().lower()
    return {
        "dissolve": "dissolve",
        "cross dissolve": "dissolve",
        "fade": "fade",
        "whip pan": "smoothleft",
        "match cut": "fade",
        "cut": "fade",
    }.get(normalized, "fade")


def transition_duration_for_cut(cut: DirectorTimelineCut, is_last_cut: bool) -> float:
    if is_last_cut:
        return 0

    transition_type = (cut.transition_type or "cut").strip().lower()
    if transition_type in {"cut", "match cut"}:
        return 0

    return max(0.05, min(float(cut.transition_duration or 0.5), max(0.05, float(cut.duration) - 0.05)))


def resolve_timeline_cut_path(cut: DirectorTimelineCut, available_cut_paths: dict[int, Path]) -> Path | None:
    if cut.video_file:
        raw_path = cut.video_file.strip()
        if raw_path.startswith("/projects/"):
            candidate = PROJECTS_DIR / raw_path.removeprefix("/projects/")
        elif raw_path.startswith("/latest_videos/"):
            candidate = LATEST_VIDEOS_DIR / raw_path.removeprefix("/latest_videos/")
        elif raw_path.startswith("/generated_clips/"):
            candidate = GENERATED_CLIPS_DIR / raw_path.removeprefix("/generated_clips/")
        else:
            candidate = Path(raw_path)

        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        if is_usable_mp4(candidate):
            return candidate

    return available_cut_paths.get(cut.cut_number)


def build_latest_full_video_file(
    timeline_cuts: list[DirectorTimelineCut] | None = None,
    selected_project: str = DEFAULT_PROJECT_SLUG,
) -> dict:
    project_slug, project_dir = resolve_project_dir(selected_project)
    project_dirs = project_asset_dirs(project_dir)
    available_cut_paths = collect_project_cut_video_paths(project_dir)
    if not available_cut_paths and project_slug == DEFAULT_PROJECT_SLUG:
        available_cut_paths = {
            find_cut_number(path): path
            for path in LATEST_VIDEOS_DIR.glob("cut_*.mp4")
            if is_usable_mp4(path) and find_cut_number(path)
        }
    if timeline_cuts is None:
        selected_cuts = [
            DirectorTimelineCut(
                cut_number=cut_number,
                selected=True,
                transition_type="cut",
                transition_duration=0.5,
                duration=read_json_file(path.with_suffix(".json")).get("duration", 1) or 1,
                video_file=str(path),
            )
            for cut_number, path in sorted(available_cut_paths.items())
        ]
    else:
        selected_cuts = [cut for cut in timeline_cuts if cut.selected]

    if not selected_cuts:
        raise HTTPException(status_code=400, detail="No selected timeline cuts found.")

    resolved_cut_paths = [resolve_timeline_cut_path(cut, available_cut_paths) for cut in selected_cuts]
    missing_cuts = [cut.cut_number for cut, path in zip(selected_cuts, resolved_cut_paths) if path is None]
    if missing_cuts:
        raise HTTPException(status_code=400, detail=f"Missing latest video files for cuts: {missing_cuts}")

    cut_paths = [path for path in resolved_cut_paths if path is not None]
    if not cut_paths:
        raise HTTPException(status_code=400, detail="No latest cut mp4 files found.")

    full_path = LATEST_VIDEOS_DIR / "full_video.mp4"
    ffmpeg_command = ["ffmpeg", "-y"]
    for path in cut_paths:
        ffmpeg_command.extend(["-i", str(path)])

    filter_parts = []
    for index, cut in enumerate(selected_cuts):
        duration = max(0.1, float(cut.duration))
        filter_parts.append(
            f"[{index}:v]"
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"tpad=stop_mode=clone:stop_duration={duration},"
            f"trim=duration={duration},setpts=PTS-STARTPTS"
            f"[v{index}]"
        )

    transition_plan = [
        {
            "from_cut": selected_cuts[index].cut_number,
            "to_cut": selected_cuts[index + 1].cut_number,
            "transition_type": selected_cuts[index].transition_type,
            "transition_filter": transition_filter_name(selected_cuts[index].transition_type),
            "transition_duration": transition_duration_for_cut(selected_cuts[index], False),
        }
        for index in range(len(selected_cuts) - 1)
    ]
    uses_transitions = any(item["transition_duration"] > 0 for item in transition_plan)

    if uses_transitions and len(selected_cuts) > 1:
        cumulative_duration = float(selected_cuts[0].duration)
        previous_label = "v0"
        for index, transition in enumerate(transition_plan, start=1):
            transition_duration = transition["transition_duration"]
            transition_filter = transition["transition_filter"]
            if transition_duration <= 0:
                transition_duration = 0.02
                transition_filter = "fade"

            offset = max(0.01, cumulative_duration - transition_duration)
            output_label = f"x{index}"
            filter_parts.append(
                f"[{previous_label}][v{index}]xfade=transition={transition_filter}:"
                f"duration={transition_duration}:offset={offset}[{output_label}]"
            )
            cumulative_duration = cumulative_duration + float(selected_cuts[index].duration) - transition_duration
            previous_label = output_label
        final_label = previous_label
        ffmpeg_filter = ";".join(filter_parts)
    elif len(selected_cuts) == 1:
        final_label = "v0"
        ffmpeg_filter = ";".join(filter_parts)
    else:
        concat_inputs = [f"[v{index}]" for index in range(len(selected_cuts))]
        filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(selected_cuts)}:v=1:a=0[outv]")
        final_label = "outv"
        ffmpeg_filter = ";".join(filter_parts)

    ffmpeg_command.extend(
        [
            "-filter_complex",
            ffmpeg_filter,
            "-map",
            f"[{final_label}]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            str(full_path),
        ]
    )

    try:
        subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise HTTPException(status_code=400, detail="ffmpeg is required to build full_video.mp4.") from error
    except subprocess.CalledProcessError as error:
        raise HTTPException(status_code=400, detail=error.stderr or str(error)) from error

    transition_overlap = sum(item["transition_duration"] for item in transition_plan)
    duration = max(0.1, sum(float(cut.duration) for cut in selected_cuts) - transition_overlap)
    providers = sorted({read_json_file(path.with_suffix(".json")).get("provider", "unknown") for path in cut_paths})
    source_jobs = sorted({read_json_file(path.with_suffix(".json")).get("source_job_id", "") for path in cut_paths if read_json_file(path.with_suffix(".json")).get("source_job_id", "")})
    timeline_payload = [
        {
            **cut.dict(),
            "source_file": str(cut_paths[index]),
            "cut_type": read_json_file(cut_paths[index].with_suffix(".json")).get("cut_type", "UNKNOWN"),
            "active_character": read_json_file(cut_paths[index].with_suffix(".json")).get("active_character", ""),
        }
        for index, cut in enumerate(selected_cuts)
    ]
    metadata = {
        "kind": "full",
        "cut_number": 0,
        "cut_type": "FULL",
        "provider": ", ".join(providers) if providers else "unknown",
        "duration": duration,
        "source_job_id": ", ".join(source_jobs),
        "source_cuts": [path.name for path in cut_paths],
        "timeline": timeline_payload,
        "transition_plan": transition_plan,
        "ffmpeg_mode": "xfade" if uses_transitions else "concat",
        "ffmpeg_filter": ffmpeg_filter,
        "video_file": str(full_path),
        "video_url": latest_video_url_for_path(full_path),
        "project": project_slug,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(full_path.with_suffix(".json"), metadata)
    write_json(LATEST_VIDEOS_DIR / "director_timeline.json", metadata)
    selected_project_full_path = project_dirs["final"] / "full_video.mp4"
    shutil.copy2(full_path, selected_project_full_path)
    selected_project_metadata = {
        **metadata,
        "video_file": str(selected_project_full_path),
        "video_url": project_url_for_path(selected_project_full_path),
        "latest_video_url": metadata["video_url"],
    }
    write_json(selected_project_full_path.with_suffix(".json"), selected_project_metadata)
    update_project_metadata(project_slug, project_dir, final_video=str(selected_project_full_path))
    project_full_path = PROJECT_FULL_VIDEO_DIR / "full_video.mp4"
    shutil.copy2(full_path, project_full_path)
    write_json(
        project_full_path.with_suffix(".json"),
        {
            **metadata,
            "video_file": str(project_full_path),
            "video_url": project_url_for_path(project_full_path),
            "latest_video_url": metadata["video_url"],
        },
    )
    write_latest_videos_manifest()
    return metadata


@app.get("/project-list")
def get_project_list():
    projects = list_project_entries()
    return {
        "default_project": DEFAULT_PROJECT_SLUG,
        "project_count": len(projects),
        "projects": projects,
    }


@app.get("/projects")
def get_projects():
    return {"projects": [project["slug"] for project in list_project_entries()]}


@app.post("/projects/create")
def create_project(request: ProjectCreateRequest):
    project_slug, project_dir = create_project_dir(request.project)
    return {
        "project": project_slug,
        "project_dir": str(project_dir),
        "projects": [project["slug"] for project in list_project_entries()],
    }


app.mount("/projects", StaticFiles(directory=PROJECTS_DIR), name="projects")


@app.get("/video-library")
def get_video_library(project: str = Query(default=DEFAULT_PROJECT_SLUG)):
    project_slug, project_dir = resolve_project_dir(project)
    videos = scan_project_video_library(project_dir)
    return {
        "project": project_slug,
        "project_dir": str(project_dir),
        "video_count": len(videos),
        "videos": videos,
    }


@app.get("/project-videos")
def get_project_videos(project: str = Query(default=DEFAULT_PROJECT_SLUG)):
    project_slug, project_dir = resolve_project_dir(project)
    videos = scan_project_video_library(project_dir)
    images = scan_project_image_library(project_dir)
    audio = scan_project_audio_library(project_dir)
    subtitles = scan_project_subtitle_library(project_dir)
    exports = scan_project_export_library(project_dir)
    full_videos = [video for video in videos if video.get("kind") == "full"]
    cut_videos = [video for video in videos if video.get("kind") == "cut"]
    return {
        "project": project_slug,
        "project_dir": str(project_dir),
        "video_count": len(videos),
        "image_count": len(images),
        "audio_count": len(audio),
        "subtitle_count": len(subtitles),
        "export_count": len(exports),
        "full_video_count": len(full_videos),
        "cut_video_count": len(cut_videos),
        "full_videos": full_videos,
        "cut_videos": cut_videos,
        "images": images,
        "audio": audio,
        "subtitles": subtitles,
        "exports": exports,
        "videos": videos,
    }


@app.get("/project-restore", response_model=ProjectRestoreResponse)
def get_project_restore(project: str = Query(default=DEFAULT_PROJECT_SLUG)):
    project_slug, _ = resolve_project_dir(project)
    return build_project_restore_payload(project_slug)


@app.post("/generate-script")
def generate_script(request: GenerateScriptRequest):
    project_slug, project_dir = resolve_project_dir(request.selected_project)
    if request.storyline:
        script = build_project_script_from_storyline(project_slug, project_dir, request.storyline)
    else:
        script = build_project_script(project_slug, project_dir)
    script = reconcile_script_export_cuts(project_dir, script)
    log_export_timing_plan(script)
    return {
        "project": project_slug,
        "script_file": str(project_dir / "script.json"),
        "script_url": project_url_for_path(project_dir / "script.json"),
        "script": script,
    }


@app.post("/generate-scenario-options", response_model=GenerateScenarioOptionsResponse)
def generate_scenario_options(request: GenerateScenarioOptionsRequest):
    project_slug, project_dir = resolve_project_dir(request.selected_project)
    response = build_scenario_options(request.topic, request.style, request.duration)
    write_json(
        project_dir / "scenario_options.json",
        {
            "topic": response.topic,
            "style": response.style,
            "duration": response.duration,
            "scenarios": [item.model_dump() for item in response.scenarios],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    update_project_metadata(project_slug, project_dir, topic=request.topic, duration=request.duration)
    return response


@app.post("/select-scenario")
def select_scenario(request: SelectScenarioRequest):
    project_slug, project_dir = resolve_project_dir(request.selected_project)
    write_json(
        project_dir / "current_scenario.json",
        {
            "scenario": request.scenario.model_dump(),
            "selected_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        "ok": True,
        "project": project_slug,
        "current_scenario": request.scenario.model_dump(),
    }


@app.post("/generate-storyline", response_model=GenerateStorylineResponse)
def generate_storyline(request: GenerateStorylineRequest):
    if request.scenario:
        return build_storyline_from_scenario(request.topic, request.scenario, request.style)
    return build_mock_storyline(request.topic, request.style)


def normalize_cut_flow_items(cut_flow: list[Any]) -> list[str]:
    if not isinstance(cut_flow, list):
        return []

    normalized: list[str] = []
    for item in cut_flow:
        if item is None:
            continue
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = " ".join(
                str(item.get(key, "")).strip()
                for key in ("narration", "subtitle", "scene", "emotion")
                if item.get(key) is not None
            ).strip()
            if not text:
                text = " ".join(str(value).strip() for value in item.values() if value is not None).strip()
        else:
            try:
                text = str(item).strip()
            except Exception:
                text = ""
        if text:
            normalized.append(text)
    return normalized


@app.post("/generate-cut-prompts", response_model=GenerateCutPromptsResponse)
def generate_cut_prompts(request: GenerateCutPromptsRequest):
    project_slug, project_dir = resolve_project_dir(request.selected_project)
    cut_flow = request.cut_flow
    if not cut_flow:
        raise HTTPException(status_code=400, detail="Storyline cut_flow is empty.")
    response = build_cut_prompts_from_storyline(
        topic=request.topic,
        style=request.style,
        summary=request.summary,
        cut_flow=cut_flow,
    )
    write_json(project_dir / "cut_prompts.json", response.model_dump())
    update_project_metadata(project_slug, project_dir, topic=request.topic)
    return response


@app.post("/generate-voice")
def generate_voice(request: GenerateVoiceRequest):
    project_slug, project_dir = resolve_project_dir(request.selected_project)
    return generate_project_voice(project_slug, project_dir, selected_cuts=request.selected_cuts)


@app.post("/generate-subtitle")
def generate_subtitle(request: GenerateSubtitleRequest):
    project_slug, project_dir = resolve_project_dir(request.selected_project)
    return generate_project_subtitle(project_slug, project_dir, selected_cuts=request.selected_cuts)


@app.post("/final-export")
def final_export(request: FinalExportRequest):
    requested_project = request.selected_project
    print(f"[final-export] START project={requested_project!r}")
    try:
        project_slug, project_dir = resolve_project_dir(request.selected_project)
        result = generate_project_final_export(project_slug, project_dir, selected_cuts=request.selected_cuts)
        print(f"[final-export] DONE project={project_slug} output={result.get('output_file')}")
        return result
    except HTTPException as error:
        print(f"[final-export] FAIL project={requested_project!r} status={error.status_code} detail={error.detail}")
        raise
    except Exception as error:
        print(f"[final-export] ERROR project={requested_project!r} error={error}")
        raise


@app.post("/project-motion-prompt")
def save_project_motion_prompt(request: UpdateMotionPromptRequest):
    project_slug, project_dir = resolve_project_dir(request.selected_project)
    storyboard = update_project_motion_prompt(project_slug, project_dir, request.cut, request.motion_prompt)
    return {
        "project": project_slug,
        "cut": request.cut,
        "motion_prompt": request.motion_prompt.strip(),
        "storyboard": storyboard,
    }


@app.get("/latest-videos")
def get_latest_videos():
    videos = scan_latest_videos()
    return {
        "directory": str(LATEST_VIDEOS_DIR),
        "video_count": len(videos),
        "videos": videos,
    }


@app.post("/latest-videos/build-full")
def build_latest_full_video(request: BuildFullVideoRequest | None = None):
    selected_project = request.selected_project if request else DEFAULT_PROJECT_SLUG
    return build_latest_full_video_file(selected_project=selected_project)


@app.post("/director-timeline/stitch")
def stitch_director_timeline(request: DirectorTimelineStitchRequest):
    return build_latest_full_video_file(request.cuts, request.selected_project)


def cleanup_dry_run_targets() -> list[dict]:
    preserved_roots = {DEFAULT_PROJECT_DIR.resolve(), ARCHIVE_DIR.resolve()}
    targets = []

    for video_path in GENERATED_CLIPS_DIR.rglob("*.mp4"):
        if not is_usable_mp4(video_path):
            targets.append({"path": str(video_path), "type": "file", "reason": "empty_or_invalid_mp4", "size_bytes": video_path.stat().st_size})
        elif "mock" in video_path.name.lower() or "test" in str(video_path).lower():
            targets.append({"path": str(video_path), "type": "file", "reason": "mock_or_test_video", "size_bytes": video_path.stat().st_size})

    for job_dir in GENERATED_CLIPS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        playable = [path for path in job_dir.rglob("*.mp4") if is_usable_mp4(path)]
        if not playable:
            targets.append({"path": str(job_dir), "type": "directory", "reason": "old_experiment_job_without_playable_mp4", "size_bytes": 0})

    for video_path in LATEST_VIDEOS_DIR.glob("*.mp4"):
        if not is_usable_mp4(video_path):
            targets.append({"path": str(video_path), "type": "file", "reason": "invalid_latest_video", "size_bytes": video_path.stat().st_size})

    deduped = []
    seen = set()
    for target in targets:
        resolved = Path(target["path"]).resolve()
        if any(resolved == root or root in resolved.parents for root in preserved_roots):
            continue
        if target["path"] in seen:
            continue
        seen.add(target["path"])
        deduped.append(target)

    return sorted(deduped, key=lambda item: (item["type"], item["reason"], item["path"]))


@app.get("/cleanup/dry-run")
def cleanup_dry_run():
    targets = cleanup_dry_run_targets()
    return {
        "mode": "dry-run",
        "target_count": len(targets),
        "targets": targets,
        "note": "No files were moved or deleted. Use archive with confirm=true to separate these targets into archive/.",
    }


@app.post("/cleanup/archive")
def archive_cleanup_targets(request: CleanupArchiveRequest):
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Archive requires confirm=true after reviewing dry-run targets.")

    archive_run_dir = ARCHIVE_DIR / f"cleanup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    moved = []
    for target in cleanup_dry_run_targets():
        source = Path(target["path"])
        if not source.exists():
            continue
        destination = archive_run_dir / source.relative_to(PROJECT_ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved.append({**target, "archive_path": str(destination)})

    return {
        "status": "archived",
        "archive_dir": str(archive_run_dir),
        "moved_count": len(moved),
        "moved": moved,
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dump_model(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def create_export_zip(zip_path: Path, source_dir: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path == zip_path or not file_path.is_file():
                continue
            archive.write(file_path, file_path.relative_to(source_dir.parent))


def character_memory_file(character_name: str) -> Path:
    return REFERENCE_CHARACTERS_DIR / character_name / "character_memory.json"


def discover_character_images(character_name: str) -> list[Path]:
    character_dir = REFERENCE_CHARACTERS_DIR / character_name
    if not character_dir.exists():
        return []

    discovered = sorted(
        path
        for path in character_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_REFERENCE_EXTENSIONS
        and not path.name.startswith(".")
    )
    valid: list[Path] = []
    for path in discovered:
        if inspect_reference_image_file(path):
            valid.append(path)
    return valid


def resolve_reference_image_for_character(character_name: str) -> Path | None:
    character_dir = REFERENCE_CHARACTERS_DIR / character_name
    if not character_dir.exists():
        return None

    candidates: list[Path] = []
    seen: set[Path] = set()
    for preferred_name in REFERENCE_IMAGE_PRIORITY_NAMES:
        candidate = character_dir / preferred_name
        if candidate.is_file() and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    for path in discover_character_images(character_name):
        if path not in seen:
            candidates.append(path)
            seen.add(path)

    for candidate in candidates:
        if inspect_reference_image_file(candidate):
            print(f"[reference] selected: {candidate}")
            return candidate

    print(f"[reference] no valid reference image found for {character_name}")
    return None


def reference_public_url_for_path(image_path: Path) -> str:
    try:
        relative = image_path.relative_to(REFERENCE_CHARACTERS_DIR)
        return f"/reference_characters/{relative.as_posix()}"
    except ValueError:
        return f"/reference_characters/{image_path.name}"


def build_bposik_character_profile_text(character_name: str) -> tuple[str, str]:
    display_name = character_name.replace("_", " ").title()
    character_summary = (
        f"{display_name} is a small cream/apricot toy poodle with fluffy curly fur, round dark eyes, "
        "a black button nose, soft floppy ears, compact legs, and a gentle alert expression. "
        "Reference images show an indoor companion dog with a rounded teddy-bear grooming silhouette, "
        "sometimes wearing a collar or name tag."
    )
    character_prompt = (
        f"Character consistency: keep the same small cream/apricot toy poodle named {display_name} in every cut. "
        f"Use reference character {character_name} as the identity anchor. "
        "tain fluffy curly coat texture, rounded teddy-bear face, dark glossy eyes, black nose, "
        "soft floppy ears, compact body proportions, short poodle legs. "
        "Preserve the same dog identity across wide shots, emotional close-ups, reveal shots, transitions, and ending shots. "
        "Do not change breed, fur color, facial structure, body size, ear shape, or nose/eye placement. "
        "Expression, gaze, ears, tail, and body pose may change per cut via the acting layer."
    )
    return character_summary, character_prompt


def build_character_profile(character_name: str) -> CharacterProfile:
    image_paths = discover_character_images(character_name)
    reference_images = [
        reference_public_url_for_path(path)
        for path in image_paths
    ]

    if character_name.lower() in {"bposik", "bposik_v2"}:
        character_summary, character_prompt = build_bposik_character_profile_text(character_name)
    else:
        character_summary = (
            f"{character_name} character profile generated from {len(reference_images)} reference image(s). "
            "Keep visible identity markers, silhouette, color palette, facial structure, and accessories consistent."
        )
        character_prompt = (
            f"Character consistency: keep the same character named {character_name} in every cut. "
            "Use the reference images as identity memory. Preserve body proportions, face shape, color, silhouette, "
            "distinctive markings, accessories, and expression style across all shots."
        )

    return CharacterProfile(
        character_name=character_name,
        character_summary=character_summary,
        character_prompt=character_prompt,
        reference_images=reference_images,
        memory_file=f"reference_characters/{character_name}/character_memory.json",
    )


def load_or_create_character_profile(character_name: str) -> CharacterProfile | None:
    normalized_name = normalize_active_character_name(character_name)
    memory_path = character_memory_file(normalized_name)
    if memory_path.exists():
        try:
            return CharacterProfile(**json.loads(memory_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    if not discover_character_images(normalized_name):
        return None

    profile = build_character_profile(normalized_name)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(memory_path, dump_model(profile))
    return profile


def apply_active_character_defaults(plan_payload: dict) -> dict:
    if not isinstance(plan_payload, dict):
        return plan_payload

    active = plan_payload.get("active_character")
    if isinstance(active, dict):
        raw_name = active.get("character_name")
    elif isinstance(active, str):
        raw_name = active
    else:
        raw_name = ""

    character_name = normalize_active_character_name(raw_name)
    profile = load_or_create_character_profile(character_name)
    if profile:
        plan_payload["active_character"] = profile.model_dump()
        for cut in plan_payload.get("cuts") or []:
            if isinstance(cut, dict):
                cut["active_character"] = profile.character_name
    return plan_payload


def validate_selected_cut(plan: VideoPlanResponse, selected_cut: int | None) -> None:
    if selected_cut is None:
        return

    if not any(cut.cut_number == selected_cut for cut in plan.cuts):
        raise HTTPException(status_code=400, detail=f"selected_cut {selected_cut} is not included in the storyboard.")


MOTION_GRAMMAR_BY_CUT_TYPE = {
    "ESTABLISHING": {
        "strategy": "wide cinematic drift with stable slow camera and environment emphasis",
        "camera_movement": "slow stabilized wide drift, gentle dolly-in, minimal pan, no handheld shake",
        "depth": "foreground atmospheric parallax, midground subject scale, deep background environmental movement",
        "continuity": "establish geography, screen direction, weather, color temperature, and spatial rhythm for the next cut",
        "avoid": "fast push, sudden reframing, aggressive subject motion",
    },
    "EMOTIONAL": {
        "strategy": "subtle handheld intimacy with slow push-in and breathing motion",
        "camera_movement": "controlled handheld micro-drift, slow emotional push-in, tiny pan correction following the subject",
        "depth": "shallow tactile foreground, soft background motion, human-scale parallax",
        "continuity": "preserve eyeline, light direction, subject identity, and emotional pacing from the previous cut",
        "avoid": "visible shake, decorative orbit, excessive background motion",
    },
    "REVEAL": {
        "strategy": "dolly-in reveal with parallax movement and focus transition",
        "camera_movement": "disciplined dolly push-in, focus settles from layered foreground into the  subject",
        "depth": "foreground-to-midground reveal, controlled compression of background depth",
        "continuity": "turn the prior detail into a clear hero reveal without breaking screen position",
        "avoid": "snap focus, jump cut, unstable anatomy, dramatic speed ramp",
    },
    "TRANSITION": {
        "strategy": "lateral connective movement with pacing continuity",
        "camera_movement": "smooth lateral track or pan, foreground wipe/parallax, horizon-stable counter-tilt",
        "depth": "foreground elements pass near lens while background travels slower to connect spaces",
        "continuity": "carry direction, energy, and emotional momentum into the ending shot",
        "avoid": "whip pan, disconnected geography, sudden stop",
    },
    "ENDING": {
        "strategy": "slow pull-back with lingering atmosphere and fade-out feeling",
        "camera_movement": "weighted dolly-back or slow zoom-out feeling, soft deceleration into final hold",
        "depth": "space opens wider as subject settles, atmosphere continues after motion resolves",
        "continuity": "resolve prior travel direction, reduce motion energy, preserve palette and final emotional tail",
        "avoid": "new visual information, abrupt ending, fast retreat",
    },
}


def get_motion_grammar(cut_type: str) -> dict[str, str]:
    return MOTION_GRAMMAR_BY_CUT_TYPE.get(cut_type, MOTION_GRAMMAR_BY_CUT_TYPE["EMOTIONAL"])


ACTION_STATE_RULES = {
    "SLEEPING": {
        "emotion_state": "PEACEFUL",
        "motion_state": "BREATHING_ONLY",
        "allowed_subject_motion": "sleeping pose only, gentle breathing, tiny chest rise, subtle ear or eyelid micro-movement",
        "blocked_actions": ["running", "jumping", "walking", "playing", "standing up", "chasing", "fast head turn"],
    },
    "RUNNING": {
        "emotion_state": "ENERGETIC",
        "motion_state": "FORWARD_MOTION_ONLY",
        "allowed_subject_motion": "one clear forward running direction with consistent gait and body rhythm",
        "blocked_actions": ["sleeping", "lying still", "sitting motionless", "crying stillness", "turning into another action"],
    },
    "SAD": {
        "emotion_state": "MELANCHOLIC",
        "motion_state": "SLOW_EMOTIONAL_ONLY",
        "allowed_subject_motion": "slow emotional motion only, lowered posture, subtle breathing, small gaze shift",
        "blocked_actions": ["running", "jumping", "playing", "excited wagging", "fast movement"],
    },
    "RESTING": {
        "emotion_state": "CALM",
        "motion_state": "SETTLED_STILLNESS",
        "allowed_subject_motion": "resting or settled posture, small breathing motion, no travel across frame",
        "blocked_actions": ["running", "jumping", "chasing", "sudden gesture"],
    },
    "WALKING": {
        "emotion_state": "SEARCHING",
        "motion_state": "SINGLE_DIRECTION_WALK",
        "allowed_subject_motion": "one slow walking direction only, readable step rhythm, no sudden action change",
        "blocked_actions": ["sleeping", "running sprint", "jumping", "lying down mid-shot"],
    },
    "OBSERVING": {
        "emotion_state": "CURIOUS",
        "motion_state": "MINIMAL_ATTENTION_SHIFT",
        "allowed_subject_motion": "quiet observing pose, small gaze shift or gentle posture adjustment only",
        "blocked_actions": ["running", "jumping", "sleeping", "playing", "fast movement"],
    },
    "EXPRESSIVE": {
        "emotion_state": "PLAYFUL",
        "motion_state": "ANIMATED_REACTION",
        "allowed_subject_motion": (
            "readable facial expression, curious eyes, tiny head tilt, ears perked, "
            "playful tail wag, sneaky glance, sudden pause, cute hesitation, "
            "varied distinct body actions per cut, expressive but natural compact toy-poodle body language, "
            "short legs and rounded body preserved, readable micro-gestures, no repeated paw-lift across cuts"
        ),
        "blocked_actions": [
            "standing still",
            "sitting still",
            "neutral expression",
            "static pose",
            "motionless",
            "blank face",
        ],
    },
}


STATIC_ACTING_BANNED = [
    "standing still",
    "sitting still",
    "neutral expression",
    "static pose",
    "motionless",
    "blank face",
    "sits motionless",
    "res still",
    "holds perfectly still",
]

STYLE_CATEGORY_HINTS: dict[str, list[str]] = {
    "comic": [
        "코믹",
        "comic",
        "코미디",
        "애니메이션",
        "animation",
        "animated",
        "cartoon",
        "유머",
        "funny",
        "cute comedy",
    ],
    "emotional": ["감성", "감성적인", "emotional", "tender", "heartfelt", "soft mood", "따뜻"],
    "sensory": ["감각", "감각적인", "sensory", "cinematic mood", "atmospheric", "몽환"],
    "dynamic": ["역동", "역동적인", "dynamic", "energetic", "active", "fast", "활발"],
}

STYLE_ACTING_PRESETS: dict[str, str] = {
    "comic": (
        "cute expressive face with same short rounded muzzle and round teddy-bear face, "
        "same small compact body and short legs, readable distinct emotion per cut, "
        "clear facial acting change cut to cut, tiny natural head tilt, "
        "playful tail wag, sneaky glance, gentle pause, readable distinct pose per cut, "
        "animated but natural toy-poodle body language, expression changes only not face or body structure, "
        "clear readable pose change, not static, not neutral, not blank face, "
        f"{COMIC_FACIAL_ACTING_BOOST}. {COMIC_NATURAL_STYLE}"
    ),
    "emotional": (
        "soft blinking eyes, gentle gaze, relaxed ears, calm breathing, subtle tail movement, warm expression, "
        "tender emotional read, not static, not blank face"
    ),
    "sensory": (
        "cinematic micro movement, delicate gaze shift, soft ear movement, subtle paw movement, "
        "atmospheric body language, slow breathing, refined gesture, warm expression, not motionless"
    ),
    "dynamic": (
        "lively compact step forward on short legs, alert but small rounded body posture, "
        "energetic tail movement, curious sniffing, quick head turn, readable action beat, not neutral, not static pose"
    ),
}

STYLE_ACTING_PRESET = STYLE_ACTING_PRESETS["comic"]

CUT_ACTING_IDENTITY_ANCHORS: dict[int, str] = {
    1: "curious sniff, round teddy-bear face, compact small body, short legs, same bposik_v2 proportions",
    2: "cautious step, compact small body, short rounded muzzle, no tall poodle posture",
    3: "averted gaze playing innocent, compact toy-poodle body, short muzzle unchanged",
    4: "sneaky low posture, compact rounded body, short legs, round teddy-bear face",
    5: "soft satisfied eyes, relaxed compact sitting, small rounded body, short legs visible",
}

CUT_ACTING_BEATS: dict[int, dict[str, str]] = {
    1: {
        "story_beat": "curiosity begins — 호기심 시작",
        "emotion_label": "curiosity (호기심)",
        "identity_anchor": CUT_ACTING_IDENTITY_ANCHORS[1],
        "expression": (
            "curious wide eyes, nose slightly lifted as if sniffing, soft alert expression, slight head tilt, "
            f"{EMOTION_EXPRESSION_IDENTITY_SUFFIX}"
        ),
        "eye_direction": "wide curious eyes toward cafe activity, other dogs, or something interesting ahead",
        "head_angle": "slight head tilt toward the target, gentle curious cock of the head, face stays round not elongated",
        "ear_motion": "ears slightly perked with alert curiosity",
        "tail_motion": "tiny anticipatory tail wag",
        "mouth_state": "closed relaxed mouth, soft alert lips, no human smile",
        "body_posture": "compact small toy-poodle body, short legs visible, forward-leaning curiosity, not tall",
        "paw_action": "nose sniffing forward, at most a barely visible tiny front-paw lift, no big paw raise",
        "emotional_beat": "wonder and eager curiosity with readable bright eyes",
        "micro_movement": "nostril twitch, soft sniff, ear flick, eyebrow area subtly lifted for alert curiosity",
        "start_pose": "Bbosik notices something with curious wide eyes and nose forward, compact rounded body visible",
        "motion_beat": "he sniffs the air with nose lifted, tilts his head with curious wide eyes, compact body stays rounded",
        "end_pose": "he holds curious wide eyes and soft alert expression toward the next beat, short muzzle unchanged",
    },
    2: {
        "story_beat": "approach then pause — 조심스럽게 한 발 앞으로",
        "emotion_label": "cautious anticipation (조심스러운 기대)",
        "identity_anchor": CUT_ACTING_IDENTITY_ANCHORS[2],
        "expression": (
            "cautious but excited eyes, mouth closed, hopeful careful face, ears slightly perked, "
            f"{EMOTION_EXPRESSION_IDENTITY_SUFFIX}"
        ),
        "eye_direction": "glancing toward mom, owner, or other dogs, then back toward the goal",
        "head_angle": "head turns as he checks mom or friends then the space ahead, compact round face preserved",
        "ear_motion": "ears slightly perked, alert but soft",
        "tail_motion": "small hopeful tail wag",
        "mouth_state": "mouth closed, no open grin, no human smile",
        "body_posture": "compact small body, short legs, weight shifted forward mid-approach, not tall poodle posture",
        "paw_action": "one cautious step forward only, front paw reaching then pausing, no paw lift pose",
        "emotional_beat": "excited but careful approach with readable cautious hope in the eyes",
        "micro_movement": "single careful step on short legs, quick glance to mom or other dogs, small tail wag",
        "start_pose": "Bbosik takes a cautious step with excited-but-careful eyes, compact body visible",
        "motion_beat": "he glances toward mom or other dogs, takes one careful step, pauses with cautious excited eyes",
        "end_pose": "he freezes mid-step with cautious but excited expression, compact body not elongated",
    },
    3: {
        "story_beat": "playing innocent — 모르는 척",
        "emotion_label": "feigned innocence / sly cuteness (능청, 모르는 척)",
        "identity_anchor": CUT_ACTING_IDENTITY_ANCHORS[3],
        "expression": (
            "innocent pretending face, tiny guilty look, subtle raised-brow feeling, soft sly cuteness, "
            f"{EMOTION_EXPRESSION_IDENTITY_SUFFIX}"
        ),
        "eye_direction": "eyes looking slightly sideways, averted gaze, then sneaky peek back",
        "head_angle": "head turned slightly sideways away from mom or watcher",
        "ear_motion": "one ear rotates back, then perks slightly with guilty-cute tension",
        "tail_motion": "tail wag hidden low, tiny guilty twitch",
        "mouth_state": "mouth closed with tiny guilty lip tension, no human smile",
        "body_posture": "relaxed compact posture, faking calm, all paws on ground, small rounded body",
        "paw_action": "paws flat on ground, no front paw lift, subtle weight shift only while pretending not to notice",
        "emotional_beat": "guilty-cute mischief and pretending not to know",
        "micro_movement": "side glance, subtle brow lift, hidden tail twitch, tiny guilty lip shift",
        "start_pose": "Bbosik looks away sideways with an innocent pretending face, paws down",
        "motion_beat": "he turns head slightly aside with sideways eyes, sneaks a peek back, posture stays relaxed",
        "end_pose": "he freezes with caught-but-cute guilty innocent expression, no paw raised",
    },
    4: {
        "story_beat": "sneaky move — 낮은 자세로 조심스럽게 이동",
        "emotion_label": "playful focus (장난스러운 집중)",
        "identity_anchor": CUT_ACTING_IDENTITY_ANCHORS[4],
        "expression": (
            "focused playful eyes, determined but cute face, no angry expression, no scary face, "
            f"{EMOTION_EXPRESSION_IDENTITY_SUFFIX}"
        ),
        "eye_direction": "focused playful eyes locked on target or other dogs",
        "head_angle": "head low and forward toward the goal, but face stays round with short muzzle visible",
        "ear_motion": "ears forward and alert with playful focus",
        "tail_motion": "tail raised slightly, tense playful twitch",
        "mouth_state": "closed mouth with playful focus, no snarl, no angry teeth",
        "body_posture": "low lowered compact body, short legs, belly closer to floor, careful sneaky creep, face not stretched",
        "paw_action": "careful creeping step on all four paws, controlled paw placement, no paw lift",
        "emotional_beat": "focused comic determination with playful not angry energy",
        "micro_movement": "nose lead with short muzzle visible, shoulder dip low, slow paw placement, playful eye sparkle",
        "start_pose": "Bbosik lowers his body with focused playful eyes, round teddy-bear face visible",
        "motion_beat": "he creeps forward low with playful focused eyes, round face preserved, ears alert",
        "end_pose": "he pauses in a low crouch with playful focused determination, same round face not elongated",
    },
    5: {
        "story_beat": "satisfied ending — 만족스러운 마무리",
        "emotion_label": "satisfaction and quiet pride (만족, 뿌듯함)",
        "identity_anchor": CUT_ACTING_IDENTITY_ANCHORS[5],
        "expression": (
            "soft satisfied eyes, tiny happy smile-like expression, relaxed cute face, calm proud look, "
            f"{EMOTION_EXPRESSION_IDENTITY_SUFFIX}"
        ),
        "eye_direction": "soft relaxed eye contact toward mom, friends, or camera",
        "head_angle": "tiny natural satisfied head tilt, compact round face",
        "ear_motion": "ears relaxed softly",
        "tail_motion": "natural small tail wag slowing into calm",
        "mouth_state": "tiny happy smile-like mouth curve, natural dog expression not human grin",
        "body_posture": "relaxed compact sitting pose, small rounded body, short legs visible, no tall upright posture",
        "paw_action": "all four paws on ground in natural sit, settled and relaxed, no paw lift or human-like waving",
        "emotional_beat": "warm quiet contentment with readable satisfied proud expression",
        "micro_movement": "soft blink, gentle breath, natural small tail wag, subtle happy mouth curve",
        "start_pose": "Bbosik sits relaxed with soft satisfied eyes, small rounded body visible",
        "motion_beat": "he exhales softly in a natural dog sit, tail gives a small wag, face shows calm proud satisfaction",
        "end_pose": "he holds relaxed satisfied face with tiny smile-like expression, compact sitting posture not elongated",
    },
}

EMOTION_KEYWORD_ACTING: list[tuple[list[str], dict[str, str]]] = [
    (
        ["놀람", "surprise", "surprised", "깜짝", "startled"],
        {
            "expression": "wide surprised eyes, slightly open mouth with same short muzzle unchanged",
            "eye_direction": "eyes snap wide toward the sudden stimulus",
            "ear_motion": "ears perked sharply upward",
        },
    ),
    (
        ["긴장", "nervous", "tense", "anxious", "불안"],
        {
            "body_posture": "cautious lowered posture, hesitant freeze",
            "eye_direction": "eyes glancing around nervously",
            "micro_movement": "tiny weight shift, quick breath",
        },
    ),
    (
        ["만족", "satisfied", "proud", "happy", "기쁨", "행복"],
        {
            "expression": "relaxed satisfied face, soft happy eyes",
            "tail_motion": "gentle happy tail wag slowing down",
            "emotional_beat": "content warm relief",
        },
    ),
    (
        ["죄책", "guilty", "들킨", "미안"],
        {
            "expression": "guilty-cute face, tiny embarrassed smile",
            "eye_direction": "avoiding eye contact then sneaky peek",
            "tail_motion": "tail wag hidden low",
        },
    ),
]

SCENE_KEYWORD_ACTING: list[tuple[list[str], dict[str, str]]] = [
    (
        ["간식", "snack", "treat", "cookie", "뼈다귀", "사료"],
        {
            "eye_direction": "gaze locked on the snack",
            "paw_action": "sniffing toward snack, nose forward",
            "micro_movement": "nostril sniff, eager lip twitch",
        },
    ),
    (
        ["엄마", "mom", "owner", "주인", "보호자"],
        {
            "eye_direction": "sneaky glance toward mom or owner",
            "emotional_beat": "guilty-cute awareness of being watched",
        },
    ),
    (
        ["카페", "cafe", "애견", "친구", "friends", "play", "놀"],
        {
            "expression": (
                "socially curious expressive face, readable excitement in the eyes, "
                f"{EMOTION_EXPRESSION_IDENTITY_SUFFIX}"
            ),
            "micro_movement": "curious sniff of cafe scents, playful social energy, subtle happy mouth curve",
            "ear_motion": "ears rotate toward cafe activity and other dogs",
            "paw_action": "lively step toward friends on all four paws, no repeated paw-lift greeting",
            "eye_direction": "eyes toward other dogs and cafe activity",
        },
    ),
]


def resolve_style_acting_category(style: str) -> str:
    lowered = (style or "").lower()
    for category, hints in STYLE_CATEGORY_HINTS.items():
        if any(hint.lower() in lowered for hint in hints):
            return category
    return "comic"


def get_style_acting_preset(style: str) -> str:
    return STYLE_ACTING_PRESETS.get(resolve_style_acting_category(style), STYLE_ACTING_PRESETS["comic"])


def build_style_layer_text(style: str) -> str:
    preset = get_style_acting_preset(style)
    layered = f"{style}. {preset}"
    if resolve_style_acting_category(style) == "comic":
        layered = f"{layered}. {COMIC_FACIAL_ACTING_BOOST}"
    return layered


def get_cut_pose_directive(cut_number: int) -> str:
    return CUT_POSE_DIRECTIVES.get(
        cut_number,
        CUT_POSE_DIRECTIVES.get(((cut_number - 1) % 5) + 1, POSE_VARIETY_RULE),
    )


def build_negative_prompt_for_cut(cut_number: int = 0, topic: str = "") -> str:
    combined = build_combined_negative_prompt()
    normalized_cut = cut_number if cut_number in CUT_POSE_DIRECTIVES else ((cut_number - 1) % 5) + 1
    extras: list[str] = []
    if topic_implies_dog_cafe(topic):
        extras.append(DOG_CAFE_BACKGROUND_NEGATIVE)
    if normalized_cut in {1, 2}:
        extras.append(CUT1_2_BODY_NEGATIVE)
    if normalized_cut == 4:
        extras.append(CUT4_FACE_NEGATIVE)
    if normalized_cut == 5:
        extras.extend([CUT5_POSE_NEGATIVE, CUT5_BODY_NEGATIVE])
    if extras:
        return f"{combined}, {', '.join(extras)}"
    return combined


def style_is_expressive(style: str) -> bool:
    return resolve_style_acting_category(style) == "comic"


def sanitize_static_acting_phrases(text: str) -> str:
    cleaned = text
    for phrase in STATIC_ACTING_BANNED:
        cleaned = re.sub(
            re.escape(phrase),
            "starts still, then tilts head with a tiny step",
            cleaned,
            flags=re.IGNORECASE,
        )
    return cleaned


def refine_acting_beat_from_cut_flow(
    beat: dict[str, str],
    *,
    scene: str = "",
    emotion: str = "",
    narration: str = "",
    subtitle: str = "",
) -> dict[str, str]:
    refined = dict(beat)
    flow_text = " ".join(part for part in (scene, emotion, narration, subtitle) if part).lower()
    for keywords, adjustments in EMOTION_KEYWORD_ACTING + SCENE_KEYWORD_ACTING:
        if any(keyword.lower() in flow_text for keyword in keywords):
            for key, value in adjustments.items():
                existing = refined.get(key, "")
                refined[key] = f"{existing}; {value}".strip("; ") if existing else value
    if emotion.strip():
        refined["emotional_beat"] = f"{refined.get('emotional_beat', '')}; story emotion: {emotion.strip()}".strip("; ")
    return refined


def get_refined_cut_acting_beat(
    cut_number: int,
    *,
    scene: str = "",
    emotion: str = "",
    narration: str = "",
    subtitle: str = "",
) -> dict[str, str]:
    base_beat = CUT_ACTING_BEATS.get(cut_number) or CUT_ACTING_BEATS.get(((cut_number - 1) % 5) + 1, {})
    return refine_acting_beat_from_cut_flow(
        base_beat,
        scene=scene,
        emotion=emotion,
        narration=narration,
        subtitle=subtitle,
    )


def build_emotion_acting_block(beat: dict[str, str], cut_number: int) -> str:
    emotion_label = beat.get("emotion_label") or beat.get("emotional_beat") or f"cut {cut_number} emotion"
    expression = beat.get("expression", "readable facial expression")
    eye_direction = beat.get("eye_direction", "motivated gaze")
    mouth = beat.get("mouth_state", "natural dog mouth, no human smile")
    ear_motion = beat.get("ear_motion", "subtle ear movement")
    tail_motion = beat.get("tail_motion", "subtle tail movement")
    return (
        "EMOTION ACTING:\n"
        f"- emotion: {emotion_label}\n"
        f"- facial expression: {expression}\n"
        f"- eye direction: {eye_direction}\n"
        f"- mouth/ears/tail: mouth {mouth}; ears {ear_motion}; tail {tail_motion}\n"
        f"- identity preserved: {EMOTION_EXPRESSION_IDENTITY_SUFFIX}"
    )


def build_cut_story_beat(cut_number: int, template: dict, topic: str) -> str:
    beat = get_refined_cut_acting_beat(
        cut_number,
        scene=template.get("scene", topic),
        emotion=template.get("story_emotion") or template.get("emotion_state", ""),
        narration=template.get("narration", ""),
        subtitle=template.get("subtitle", ""),
    )
    scene = template.get("scene", topic)
    narration = template.get("narration", "")
    subtitle = template.get("subtitle", "")
    acting_detail = (
        f"emotion: {beat.get('emotion_label', beat.get('emotional_beat', ''))}; "
        f"expression: {beat.get('expression', '')}; "
        f"paw action: {beat.get('paw_action', '')}; "
        f"eye direction: {beat.get('eye_direction', '')}; "
        f"posture: {beat.get('body_posture', '')}"
    )
    return sanitize_static_acting_phrases(
        f"CUT STORY BEAT {cut_number}: {beat.get('story_beat', 'story beat')}. "
        f"{acting_detail}. "
        f"Pose directive: {get_cut_pose_directive(cut_number)}. {POSE_VARIETY_RULE}. "
        f"Face shape lock: {BPOSIK_FACE_SHAPE_LOCK}. {ACTING_FACE_SHAPE_GUARD}. "
        f"Body identity lock: {BPOSIK_BODY_IDENTITY_LOCK}. {ACTING_BODY_SHAPE_GUARD}. "
        f"Scene: {scene}. Narration: {narration}. Subtitle: {subtitle}."
    )


def format_acting_layer_text(beat: dict[str, str], cut_number: int, cut_type: str, style: str) -> str:
    style_preset = get_style_acting_preset(style)
    identity_anchor = (
        beat.get("identity_anchor")
        or CUT_ACTING_IDENTITY_ANCHORS.get(cut_number)
        or ACTING_IDENTITY_GUARD
    )
    pose_directive = get_cut_pose_directive(cut_number)
    return sanitize_static_acting_phrases(
        "ACTING LAYER "
        f"cut {cut_number} ({cut_type}): "
        f"emotion label: {beat.get('emotion_label', beat.get('emotional_beat', 'distinct emotion per cut'))}; "
        f"facial expression: {beat.get('expression', 'readable emotional reaction')}; "
        f"eye direction: {beat.get('eye_direction', 'motivated eye line toward story beat')}; "
        f"head angle: {beat.get('head_angle', 'slight tilt matching the beat')}; "
        f"mouth state: {beat.get('mouth_state', 'natural dog mouth')}; "
        f"ear motion: {beat.get('ear_motion', 'subtle ear movement')}; "
        f"tail motion: {beat.get('tail_motion', 'subtle tail movement')}; "
        f"body posture: {beat.get('body_posture', 'expressive posture shift')}; "
        f"paw action / small action: {beat.get('paw_action', 'clear readable gesture')}; "
        f"emotional beat: {beat.get('emotional_beat', 'distinct emotion per cut')}; "
        f"micro movement: {beat.get('micro_movement', 'starts still, then moves with a tiny gesture')}. "
        f"Style acting preset: {style_preset}. "
        f"Pose directive: {pose_directive}. {POSE_VARIETY_RULE}. "
        f"Face shape lock: {BPOSIK_FACE_SHAPE_LOCK}. "
        f"Body identity lock: {BPOSIK_BODY_IDENTITY_LOCK}. "
        f"Identity preservation: {identity_anchor}, {ACTING_IDENTITY_GUARD}, {ACTING_FACE_SHAPE_GUARD}, {ACTING_BODY_SHAPE_GUARD}."
    )


def build_cut_acting_layer(
    cut_number: int,
    cut_type: str,
    style: str,
    scene: str = "",
    emotion: str = "",
    narration: str = "",
    subtitle: str = "",
) -> str:
    beat = get_refined_cut_acting_beat(
        cut_number,
        scene=scene,
        emotion=emotion,
        narration=narration,
        subtitle=subtitle,
    )
    acting = format_acting_layer_text(beat, cut_number, cut_type, style)
    emotion_block = build_emotion_acting_block(beat, cut_number)
    return sanitize_static_acting_phrases(f"{acting} {emotion_block}")


def build_video_start_motion_end(template: dict, style: str, topic: str, cut_number: int) -> dict[str, str]:
    beat = get_refined_cut_acting_beat(
        cut_number,
        scene=template.get("scene", topic),
        emotion=template.get("story_emotion") or template.get("emotion_state", ""),
        narration=template.get("narration", ""),
        subtitle=template.get("subtitle", ""),
    )
    start = sanitize_static_acting_phrases(
        template.get("start_frame_description")
        or beat.get("start_pose")
        or f"Bbosik reacts to '{topic}' with {beat.get('expression', 'expressive face')}"
    )
    motion = sanitize_static_acting_phrases(
        beat.get("motion_beat")
        or f"he shifts with {beat.get('paw_action', 'a tiny step')}, "
        f"{beat.get('ear_motion', 'ears move')}, {beat.get('tail_motion', 'tail moves')}"
    )
    end = sanitize_static_acting_phrases(
        template.get("end_frame_description")
        or beat.get("end_pose")
        or f"Bbosik finishes with {beat.get('expression', 'a clearer emotional read')}"
    )
    camera_motion = sanitize_static_acting_phrases(
        f"{template.get('camera_path', '')}. {template.get('environmental_motion', '')}".strip()
    )
    if camera_motion:
        motion = f"{motion}. Camera: {camera_motion}"
    return {"start": start, "motion": motion, "end": end, "beat": beat}


def build_motion_prompt_with_acting(template: dict, style: str, topic: str, cut_number: int) -> str:
    acting_layer = template.get("acting_layer_prompt") or build_cut_acting_layer(
        cut_number,
        template.get("cut_type", "EMOTIONAL"),
        style,
        scene=template.get("scene", topic),
        emotion=template.get("story_emotion") or template.get("emotion_state", ""),
        narration=template.get("narration", ""),
        subtitle=template.get("subtitle", ""),
    )
    video_beats = build_video_start_motion_end(template, style, topic, cut_number)
    emotion_block = template.get("emotion_acting_block") or build_emotion_acting_block(video_beats["beat"], cut_number)
    scene_context = template.get("scene_context_prompt") or build_scene_context_lock_prompt(topic, cut_number)
    return sanitize_static_acting_phrases(
        f"A. Reference Character: {BPOSIK_REFERENCE_CHARACTER}. "
        f"B. Strong Identity Lock: {BPOSIK_STRONG_IDENTITY_LOCK} "
        f"C. Scene Context Lock: {scene_context} "
        f"E. Acting Layer: {acting_layer} "
        f"{emotion_block} "
        f"Face shape lock: {BPOSIK_FACE_SHAPE_LOCK}. {ACTING_FACE_SHAPE_GUARD}. "
        f"Body identity lock: {BPOSIK_BODY_IDENTITY_LOCK}. {ACTING_BODY_SHAPE_GUARD}. "
        f"Pose directive: {get_cut_pose_directive(cut_number)}. {POSE_VARIETY_RULE}. "
        f"Start: {video_beats['start']} "
        f"Motion: {video_beats['motion']} "
        f"End: {video_beats['end']} "
        f"F. Style Layer: {build_style_layer_text(style)}. "
        f"H. Negative Prompt: {build_negative_prompt_for_cut(cut_number, topic)}."
    )


def build_acting_audit_block(template: dict, style: str, topic: str, cut_number: int) -> str:
    acting_layer = template.get("acting_layer_prompt") or build_cut_acting_layer(
        cut_number,
        template.get("cut_type", "EMOTIONAL"),
        style,
        scene=template.get("scene", topic),
        emotion=template.get("story_emotion") or template.get("emotion_state", ""),
        narration=template.get("narration", ""),
        subtitle=template.get("subtitle", ""),
    )
    video_beats = build_video_start_motion_end(template, style, topic, cut_number)
    beat = video_beats["beat"]
    emotion_block = template.get("emotion_acting_block") or build_emotion_acting_block(beat, cut_number)
    identity_anchor = beat.get("identity_anchor") or CUT_ACTING_IDENTITY_ANCHORS.get(cut_number, ACTING_IDENTITY_GUARD)
    return "\n".join(
        [
            "ACTING AUDIT:",
            f"- Reference Character: {template.get('reference_character_section') or template.get('reference_character_prompt') or BPOSIK_REFERENCE_CHARACTER}",
            f"- Strong Identity Lock: {template.get('character_lock_prompt') or BPOSIK_STRONG_IDENTITY_LOCK}",
            f"- Face shape lock: {BPOSIK_FACE_SHAPE_LOCK}",
            f"- Body identity lock: {BPOSIK_BODY_IDENTITY_LOCK}",
            f"- Negative Prompt: {build_negative_prompt_for_cut(cut_number, topic)}",
            f"- Acting face guard: {ACTING_FACE_SHAPE_GUARD}",
            f"- Acting body guard: {ACTING_BODY_SHAPE_GUARD}",
            f"- Pose directive: {get_cut_pose_directive(cut_number)}",
            f"- Pose variety: {POSE_VARIETY_RULE}",
            f"- active image provider: {template.get('image_provider', 'openai')}",
            f"- ACTING LAYER: {acting_layer}",
            emotion_block,
            f"- identity anchor: {identity_anchor}",
            f"- Start: {video_beats['start']}",
            f"- Motion: {video_beats['motion']}",
            f"- End: {video_beats['end']}",
            f"- expression: {beat.get('expression', '')}",
            f"- body_action: {beat.get('body_posture', '')}; {beat.get('paw_action', '')}",
            f"- eye_direction: {beat.get('eye_direction', '')}",
            f"- ear_tail_motion: {beat.get('ear_motion', '')}; {beat.get('tail_motion', '')}",
        ]
    )


def log_acting_prompt_audit(
    cut_number: int,
    template: dict,
    style: str,
    topic: str,
    *,
    image_provider: str = "openai",
    character_name: str = DEFAULT_ACTIVE_CHARACTER,
) -> None:
    audit = build_acting_audit_block(template, style, topic, cut_number)
    provider_label = image_provider.strip().lower()
    if provider_label == "openai":
        provider_label = "OpenAI"
    elif provider_label == "replicate":
        provider_label = "Replicate"
    elif provider_label == "mock":
        provider_label = "Mock"
    print(f"[image] active image provider: {provider_label}")
    print(f"[image] Reference Character: {character_name}")
    print(f"[image] cut {cut_number}\n{audit}")


def apply_style_acting(template: dict, style: str, topic: str, cut_number: int) -> dict:
    styled = dict(template)
    category = resolve_style_acting_category(style)
    if category == "comic":
        styled["action_state"] = "EXPRESSIVE"
        rule = ACTION_STATE_RULES["EXPRESSIVE"]
        styled["emotion_state"] = rule["emotion_state"]
        styled["motion_state"] = rule["motion_state"]
        styled["allowed_subject_motion"] = rule["allowed_subject_motion"]
        styled["blocked_actions"] = rule["blocked_actions"]
        styled["subject_motion"] = sanitize_static_acting_phrases(rule["allowed_subject_motion"])
    acting_layer = build_cut_acting_layer(
        cut_number,
        styled.get("cut_type", "EMOTIONAL"),
        style,
        scene=styled.get("scene", topic),
        emotion=styled.get("story_emotion") or styled.get("emotion_state", ""),
        narration=styled.get("narration", ""),
        subtitle=styled.get("subtitle", ""),
    )
    beat = get_refined_cut_acting_beat(
        cut_number,
        scene=styled.get("scene", topic),
        emotion=styled.get("story_emotion") or styled.get("emotion_state", ""),
        narration=styled.get("narration", ""),
        subtitle=styled.get("subtitle", ""),
    )
    styled["acting_layer_prompt"] = acting_layer
    styled["emotion_acting_block"] = build_emotion_acting_block(beat, cut_number)
    styled["cut_story_beat"] = build_cut_story_beat(cut_number, styled, topic)
    styled["subject_motion"] = sanitize_static_acting_phrases(
        f"{styled.get('subject_motion', '')}; {acting_layer}"
    )
    styled["motion_prompt"] = build_motion_prompt_with_acting(styled, style, topic, cut_number)
    return styled


CONFLICTING_ACTION_GROUPS = [
    {"sleep", "sleeping", "asleep", "자는", "잠든", "running", "run", "뛰는", "달리는"},
    {"sad", "crying", "슬픈", "우는", "happy playing", "playing", "jumping", "노는", "점프"},
    {"still", "resting", "정지", "쉬는", "walking", "running", "걷는", "달리는"},
]


def build_visual_style_lock(style: str) -> dict[str, str]:
    return {
        "resolution_feeling": "consistent 4K upscale feeling, clean high-detail image plane, no low-resolution softness",
        "sharpness": "same crisp but filmic sharpness on every cut, detailed fur texture, stable edges, no mushy frames",
        "lens_style": "consistent premium cinema prime lens rendering, controlled depth of field, natural perspective continuity",
        "color_grading": f"locked {style} grade, matched contrast, black level, highlight rolloff, and saturation across all cuts",
        "cinematic_density": "same cinematic density across all cuts: layered foreground, readable midground subject, atmospheric background, premium film still quality",
    }


def infer_action_state(topic: str, cut_type: str, style: str = "") -> str:
    if style_is_expressive(style):
        return "EXPRESSIVE"
    lowered = topic.lower()
    if any(word in lowered for word in ["sleep", "sleeping", "asleep", "자는", "잠든", "잠자는"]):
        return "SLEEPING"
    if any(word in lowered for word in ["run", "running", "달리는", "뛰는"]):
        return "RUNNING"
    if any(word in lowered for word in ["sad", "crying", "lonely", "슬픈", "우는", "외로운"]):
        return "SAD"

    return {
        "ESTABLISHING": "OBSERVING",
        "EMOTIONAL": "SAD",
        "REVEAL": "RESTING",
        "TRANSITION": "WALKING",
        "ENDING": "RESTING",
    }.get(cut_type, "OBSERVING")


def detect_action_conflicts(text: str, action_state: str) -> list[str]:
    lowered = text.lower()
    conflicts = []
    allowed = ACTION_STATE_RULES[action_state]["allowed_subject_motion"].lower()
    for group in CONFLICTING_ACTION_GROUPS:
        hits = [token for token in group if token in lowered]
        if len(hits) > 1:
            conflicts.append(f"Conflicting action words detected and locked to {action_state}: {', '.join(sorted(hits))}")

    for blocked in ACTION_STATE_RULES[action_state]["blocked_actions"]:
        if blocked.lower() in lowered and blocked.lower() not in allowed:
            conflicts.append(f"Blocked action removed for {action_state}: {blocked}")

    return conflicts


def apply_acting_state(template: dict, topic: str, style: str = "", cut_number: int = 0) -> dict:
    action_state = infer_action_state(topic, template["cut_type"], style)
    rule = ACTION_STATE_RULES.get(action_state, ACTION_STATE_RULES["OBSERVING"])
    state_template = dict(template)
    original_motion_text = " ".join(
        [
            topic,
            template.get("scene", ""),
            template.get("prompt", ""),
            template.get("subject_motion", ""),
            template.get("motion_prompt", ""),
        ]
    )
    state_template["action_state"] = action_state
    state_template["emotion_state"] = rule["emotion_state"]
    state_template["motion_state"] = rule["motion_state"]
    state_template["allowed_subject_motion"] = rule["allowed_subject_motion"]
    state_template["blocked_actions"] = rule["blocked_actions"]
    state_template["conflict_warnings"] = detect_action_conflicts(original_motion_text, action_state)
    state_template["subject_motion"] = sanitize_static_acting_phrases(rule["allowed_subject_motion"])
    state_template["motion_intensity"] = f"{template['motion_intensity']}; acting lock {rule['motion_state']}"
    if cut_number:
        return apply_style_acting(state_template, style, topic, cut_number)
    return state_template


def visual_style_lock_text(visual_style_lock: dict[str, str]) -> str:
    return "VISUAL STYLE LOCK: " + "; ".join(
        f"{key.replace('_', ' ')}: {value}" for key, value in visual_style_lock.items()
    )


def topic_implies_rain_weather(topic: str) -> bool:
    lowered = topic.lower()
    return any(keyword in lowered for keyword in RAIN_TOPIC_KEYWORDS)


def topic_implies_dog_cafe(topic: str) -> bool:
    lowered = topic.lower()
    return any(keyword.lower() in lowered for keyword in DOG_CAFE_TOPIC_KEYWORDS)


def dog_cafe_cut_position(cut_number: int) -> str:
    normalized = cut_number if cut_number in DOG_CAFE_CUT_POSITIONS else ((cut_number - 1) % 5) + 1
    return DOG_CAFE_CUT_POSITIONS[normalized]


def build_scene_context_lock_prompt(topic: str, cut_number: int = 0) -> str:
    if not topic_implies_dog_cafe(topic):
        return ""
    cut_pos = dog_cafe_cut_position(cut_number)
    return (
        f"SCENE CONTEXT LOCK: {DOG_CAFE_SCENE_CONTEXT_LOCK}. "
        f"Cut camera zone: {cut_pos}. "
        "Only expression, pose, body action, and camera angle may change; "
        "the location stays the same indoor dog cafe."
    )


def scene_implies_home_location(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword.lower() in lowered for keyword in HOME_SCENE_KEYWORDS)


def normalize_cut_scene_for_topic(scene: str, cut_number: int, topic: str) -> str:
    if not topic_implies_dog_cafe(topic):
        return scene.strip()
    cleaned = scene.strip()
    cafe_pos = dog_cafe_cut_position(cut_number)
    if not cleaned or scene_implies_home_location(cleaned):
        return f"같은 애견카페 안, {cafe_pos}"
    if not any(keyword in cleaned.lower() for keyword in ("애견카페", "카페", "cafe", "dog cafe", "pet cafe")):
        return f"같은 애견카페 안, {cafe_pos}. {cleaned}"
    return cleaned


def build_dog_cafe_image_scene_prompt(*, style: str, topic: str, cut_number: int, action_hint: str = "") -> str:
    cafe_pos = dog_cafe_cut_position(cut_number)
    action_clause = f", {action_hint}" if action_hint.strip() else ""
    return (
        f"{style}, same indoor dog cafe, {topic}, {cafe_pos}{action_clause}, "
        f"{DOG_CAFE_SCENE_CONTEXT_LOCK}, warm cozy pet cafe interior, wooden floor, "
        "small dogs playing softly in the background, cafe tables and chairs, "
        "cinematic still, realistic lighting, 16:9 frame, no home interior, no bedroom, no TV"
    )


def build_scene_context(topic: str, style: str) -> SceneContext:
    if topic_implies_dog_cafe(topic):
        return SceneContext(
            weather="dry clean indoor air, cozy cafe warmth, no rain, no outdoor weather",
            location=(
                "same indoor dog cafe interior across all cuts; warm cozy pet cafe with wooden floor, "
                "dog play area, cafe tables and chairs, and small dogs in the background"
            ),
            props="cafe tables and chairs, dog play mats, toy balls, water bowls, other small dogs in background",
            lighting="soft warm cafe lighting with cozy amber practicals, consistent across every cut",
            action="social playful dog movement inside the same cafe space, never teleporting to home or outdoor park",
        )

    if topic_implies_rain_weather(topic):
        return SceneContext(
            weather="steady moderate rain with soft haze, rain streaks, wet reflections, and rain-soaked surfaces",
            location=f"environment supporting '{topic}' with rainy night or rain-soaked exterior-interior continuity",
            props="dark umbrella near or above the subject when appropriate, wet ground reflections, rain-lit props",
            lighting="warm practical or window glow against cool rainy blue-gray night atmosphere",
            action="timid tired movement under rain, gentle observing or slow walking with matched rain ambience",
        )

    return SceneContext(
        weather="dry clean atmosphere, no rain, no wet-fur weather effect, calm breathable air",
        location=f"environment naturally supporting '{topic}' without forced rain or storm drama",
        props="topic-appropriate props only, no umbrella unless explicitly implied by the topic",
        lighting=f"{style}-matched cinematic lighting with coherent color temperature for '{topic}'",
        action="natural topic-appropriate posture and gentle movement, clean dry fur presentation",
    )


def build_continuity_state(scene_context: SceneContext, topic: str) -> ContinuityState:
    if topic_implies_rain_weather(topic):
        return ContinuityState(
            color_temperature=scene_context.lighting,
            emotional_arc="timid tired vulnerability gradually resolving into quiet calm under rain",
        )
    return ContinuityState(
        color_temperature=scene_context.lighting,
        emotional_arc="gentle curiosity and emotional clarity that resolves into quiet calm",
    )


def build_master_character_prompt(
    master_character: MasterCharacter,
    *,
    character_prompt_prefix: str = "",
) -> str:
    prefix = f"{character_prompt_prefix.strip()} " if character_prompt_prefix.strip() else ""
    return (
        f"{prefix}MASTER CHARACTER LOCK: {master_character.lock_sentence}. "
        f"character_type: {master_character.character_type}; breed: {master_character.breed}; "
        f"body_size: {master_character.body_size}; fur_color: {master_character.fur_color}; "
        f"posture: {master_character.posture}; emotional_state: {master_character.emotional_state}; "
        f"key visual traits: {', '.join(master_character.key_visual_traits)}."
    ).strip()


def build_scene_context_prompt(scene_context: SceneContext, topic: str = "", cut_number: int = 0) -> str:
    base = (
        f"SCENE CONTEXT: weather: {scene_context.weather}; location: {scene_context.location}; "
        f"props: {scene_context.props}; lighting: {scene_context.lighting}; action: {scene_context.action}."
    )
    lock = build_scene_context_lock_prompt(topic, cut_number)
    if lock:
        return f"{lock} {base}"
    return base


def scenario_subject_from_topic(topic: str) -> str:
    normalized = topic.strip()
    if "뽀식" in normalized:
        return "뽀식이"
    return "주인공"


def _scenario_narration_from_scene(scene: str, cut_number: int) -> str:
    text = scene.strip()
    if not text:
        return f"컷 {cut_number}."
    if len(text) <= 28:
        return text
    return f"{text[:25]}…"


def build_dog_cafe_storyline_cuts(subject: str) -> list[dict]:
    return [
        {
            "cut": 1,
            "scene": f"애견카페 입구 근처에서 다른 강아지 친구들을 발견하는 {subject}",
            "emotion": "호기심",
            "narration": "새 친구들이 보인다!",
            "subtitle": "새 친구들이 보인다!",
        },
        {
            "cut": 2,
            "scene": f"같은 애견카페 놀이공간에서 조심스럽게 다가가는 {subject}",
            "emotion": "조심스러운 기대",
            "narration": "조금만 더 가까이.",
            "subtitle": "조금만 더 가까이.",
        },
        {
            "cut": 3,
            "scene": f"같은 애견카페 중앙에서 친구들과 눈치를 보는 {subject}",
            "emotion": "능청",
            "narration": "못 본 척해야지.",
            "subtitle": "못 본 척해야지.",
        },
        {
            "cut": 4,
            "scene": f"같은 애견카페 바닥 근처에서 장난스럽게 낮은 자세로 움직이는 {subject}",
            "emotion": "장난",
            "narration": "슬금슬금!",
            "subtitle": "슬금슬금!",
        },
        {
            "cut": 5,
            "scene": f"같은 애견카페 테이블 근처에서 만족스럽게 쉬는 {subject}",
            "emotion": "만족",
            "narration": "오늘도 즐거웠어.",
            "subtitle": "오늘도 즐거웠어.",
        },
    ]


def build_scenario_options(topic: str, style: str = "", duration: int = 15) -> GenerateScenarioOptionsResponse:
    normalized_topic = topic.strip()
    style_hint = style.strip() or "코믹한 영상"
    subject = scenario_subject_from_topic(normalized_topic)
    style_clause = f" ({style_hint})" if style_hint else ""

    if topic_implies_dog_cafe(normalized_topic):
        cafe_cuts = build_dog_cafe_storyline_cuts(subject)
        comic_cuts = cafe_cuts
        emotional_cuts = [
            {**item, "emotion": "설렘" if item["cut"] <= 2 else item["emotion"]}
            for item in cafe_cuts
        ]
        twist_cuts = [
            {
                **item,
                "scene": item["scene"].replace("발견하는", "발견한 줄 알았지만 사실은").replace(
                    "만족스럽게 쉬는", "쉬는 척하지만 아직 놀고 싶은"
                ),
            }
            for item in cafe_cuts
        ]
        comic_summary = (
            f"{subject}는 같은 애견카페 안에서 다른 친구 강아지들과 {normalized_topic}. "
            f"표정과 포즈만 바뀌며{style_clause} 코믹하게 이어진다."
        )
        emotional_summary = (
            f"같은 애견카페 안에서 {subject}의 작은 설렘이 쌓이며 "
            f"친구들과의 순간이{style_clause} 따뜻하게 마무리된다."
        )
        twist_summary = (
            f"같은 애견카페 안에서 {subject}의 반응이 예상과 조금 다르게 흘러가며"
            f"{style_clause} 짧은 반전으로 끝난다."
        )
    elif "퇴근" in normalized_topic:
        comic_cuts = [
            {"cut": 1, "scene": f"{subject}가 소파에서 현관만 슬쩍 보며 '아직 멀었지?' 표정", "emotion": "여유"},
            {"cut": 2, "scene": "현관 쪽 소리에 귀만 쫑긋, 몸은 꼼짝 않는 코믹 대치", "emotion": "가식"},
            {"cut": 3, "scene": "문 열리는 소리에 순간 몸을 돌리다가 '못 본 척' 실패", "emotion": "들킴"},
            {"cut": 4, "scene": "집사 발소리에 꼬리만 먼저 출렁이는 반응", "emotion": "들뜸"},
            {"cut": 5, "scene": "결국 달려가 안기려다 마지막에 쿨한 척 멈추는 결말", "emotion": "코믹 안도"},
        ]
        emotional_cuts = [
            {"cut": 1, "scene": f"저녁 거실, {subject} 혼자 조용히 앉아 하루를 기다림", "emotion": "고요"},
            {"cut": 2, "scene": "창밖 어두워지고 현관 불빛만 기다리는 시선", "emotion": "그리움"},
            {"cut": 3, "scene": "열쇠 소리에 귀가 먼저 반응하는 작은 순간", "emotion": "설렘"},
            {"cut": 4, "scene": "문이 열리고 집사와 눈이 마주치는 따뜻한 장면", "emotion": "반가움"},
            {"cut": 5, "scene": "같은 자리에 앉아 하루가 다시 채워지는 마무리", "emotion": "안정"},
        ]
        twist_cuts = [
            {"cut": 1, "scene": f"{subject}가 퇴근을 기다리는 것처럼 보이지만 사실 배달을 기다림", "emotion": "무심"},
            {"cut": 2, "scene": "현관 소리에 반응했으나 택배 기사 등장", "emotion": "당황"},
            {"cut": 3, "scene": "실망한 표정이 순식간에 '그래도 간식'으로 전환", "emotion": "전환"},
            {"cut": 4, "scene": "그때 진짜 집사 퇴근, 표정이 다시 뒤집힘", "emotion": "놀람"},
            {"cut": 5, "scene": "간식과 집사 사이에서 선택 장면으로 마무리", "emotion": "코믹 반전"},
        ]
        comic_summary = (
            f"{subject}는 퇴근을 기다리는 척하지만 이미 집사 귀환을 다 읽고 있다. "
            f"쿨한 척하다 결국 들키는{style_clause} 코믹한 하루의 끝."
        )
        emotional_summary = (
            f"조용한 저녁, {subject}는 집사가 돌아오기만을 기다린다. "
            f"작은 소리와 눈맞춤으로 채워지는{style_clause} 따뜻한 퇴근 후 루틴."
        )
        twist_summary = (
            f"퇴근을 기다리는 {subject}, 사실 첫 번째 방문객은 택배였다. "
            f"진짜 집사 등장과 겹치며{style_clause} 짧은 반전이 터진다."
        )
    elif "루틴" in normalized_topic:
        comic_cuts = [
            {"cut": 1, "scene": f"{subject}의 루틴 첫 단계: 일부러 늦잠", "emotion": "나태"},
            {"cut": 2, "scene": "계획대로 산책하려다 벤치에서 관찰만", "emotion": "회피"},
            {"cut": 3, "scene": "간식 타임에만 속도가 붙는 루틴", "emotion": "들뜸"},
            {"cut": 4, "scene": "씻기 루틴 앞에서 갑자기 사라짐", "emotion": "도주"},
            {"cut": 5, "scene": "결국 소파 루틴으로 돌아와 승리", "emotion": "만족"},
        ]
        emotional_cuts = [
            {"cut": 1, "scene": f"아침 햇살 아래 {subject}의 익숙한 첫 순간", "emotion": "차분"},
            {"cut": 2, "scene": "같은 길, 같은 냄새, 같은 속도로 걷는 시간", "emotion": "안정"},
            {"cut": 3, "scene": "작은 변화에도 반응하는 섬세한 표정", "emotion": "공감"},
            {"cut": 4, "scene": "하루의 피로를 풀며 쉬는 고요한 장면", "emotion": "편안"},
            {"cut": 5, "scene": "내일도 이어질 루틴을 바라보는 여운", "emotion": "따뜻함"},
        ]
        twist_cuts = [
            {"cut": 1, "scene": "평범한 루틴 시작, 하지만 오늘만 다른 소리", "emotion": "의아"},
            {"cut": 2, "scene": "익숙한 공간에 낯선 물건 등장", "emotion": "경계"},
            {"cut": 3, "scene": "루틴을 깨는 작은 사건", "emotion": "긴장"},
            {"cut": 4, "scene": "알고 보니 서프라이즈 준비였던 순간", "emotion": "놀람"},
            {"cut": 5, "scene": "새 루틴이 하나 더 생긴 듯한 결말", "emotion": "반전"},
        ]
        comic_summary = f"'{normalized_topic}' 루틴을 지키려 하지만{style_clause} 매번 엇나가는 코믹 하루."
        emotional_summary = f"'{normalized_topic}' 속 작은 습관들이{style_clause} 하루의 감정을 채우는 이야기."
        twist_summary = f"익숙한 '{normalized_topic}' 중{style_clause} 뜻밖의 변화가 짧게 반전을 만든다."
    else:
        comic_cuts = [
            {"cut": 1, "scene": f"'{normalized_topic}' 시작, {subject}가 과하게 자신 있어함", "emotion": "자신"},
            {"cut": 2, "scene": "계획과 다른 상황에 표정이 굳는 순간", "emotion": "당황"},
            {"cut": 3, "scene": "어색하게 상황을 수습하려는 몸짓", "emotion": "코믹"},
            {"cut": 4, "scene": "생각보다 잘 풀린 듯 보이는 착각", "emotion": "들뜸"},
            {"cut": 5, "scene": "마지막에 작은 실수로 웃음을 남김", "emotion": "유쾌"},
        ]
        emotional_cuts = [
            {"cut": 1, "scene": f"'{normalized_topic}'의 시작, 조용한 공기", "emotion": "고요"},
            {"cut": 2, "scene": "작은 디테일에 마음이 움직이는 장면", "emotion": "공감"},
            {"cut": 3, "scene": "감정이 조금씩 깊어지는 순간", "emotion": "몰입"},
            {"cut": 4, "scene": "관계나 공간이 따뜻해지는 전환", "emotion": "위로"},
            {"cut": 5, "scene": "남는 여운으로 마무리", "emotion": "잔잔함"},
        ]
        twist_cuts = [
            {"cut": 1, "scene": f"'{normalized_topic}'처럼 보이지만 다른 단서", "emotion": "미스터리"},
            {"cut": 2, "scene": "관점이 바뀌며 상황 재해석", "emotion": "의문"},
            {"cut": 3, "scene": "예상과 다른 진실이 드러남", "emotion": "충격"},
            {"cut": 4, "scene": "반전 이후 감정 정리", "emotion": "전환"},
            {"cut": 5, "scene": "새로운 결론으로 짧게 마무리", "emotion": "반전"},
        ]
        comic_summary = f"'{normalized_topic}'을(를) 코믹하게 풀어낸{style_clause} 가벼운 5컷 이야기."
        emotional_summary = f"'{normalized_topic}'의 감정선을 따라가는{style_clause} 잔잔한 5컷 이야기."
        twist_summary = f"'{normalized_topic}'의 끝에서{style_clause} 짧은 반전이 터지는 5컷 이야기."

    def _build_option(option_id: str, title: str, summary: str, tone: str, raw_cuts: list[dict]) -> ScenarioOption:
        cut_flow: list[ScenarioCutFlowItem] = []
        for item in raw_cuts:
            scene = str(item.get("scene") or "").strip()
            emotion = str(item.get("emotion") or "").strip()
            narration = _scenario_narration_from_scene(scene, int(item.get("cut") or len(cut_flow) + 1))
            cut_flow.append(
                ScenarioCutFlowItem(
                    cut=int(item.get("cut") or len(cut_flow) + 1),
                    scene=scene,
                    emotion=emotion,
                    narration=narration,
                    subtitle=narration,
                )
            )
        return ScenarioOption(
            id=option_id,
            title=title,
            summary=summary,
            tone=tone,
            cut_flow=cut_flow,
        )

    scenarios = [
        _build_option(
            "scenario_1",
            f"코믹형 · {normalized_topic[:18]}",
            comic_summary,
            "코믹",
            comic_cuts,
        ),
        _build_option(
            "scenario_2",
            f"감성형 · {normalized_topic[:18]}",
            emotional_summary,
            "감성",
            emotional_cuts,
        ),
        _build_option(
            "scenario_3",
            f"반전형 · {normalized_topic[:18]}",
            twist_summary,
            "반전",
            twist_cuts,
        ),
    ]

    return GenerateScenarioOptionsResponse(
        ok=True,
        topic=normalized_topic,
        style=style_hint,
        duration=int(duration),
        scenarios=scenarios,
    )


def build_storyline_from_scenario(topic: str, scenario: ScenarioOption, style: str = "") -> GenerateStorylineResponse:
    normalized_topic = topic.strip() or scenario.summary[:40]
    style_hint = style.strip()
    tone = scenario.tone.strip()
    arc_by_tone = {
        "코믹": "도입-가식-들킴-들뜸-코믹 마무리",
        "감성": "도입-그리움-설렘-만남-안정",
        "반전": "도입-단서-전환-반전-마무리",
    }
    story_arc = arc_by_tone.get(tone, "도입-전개-전환-클라이맥스-마무리")
    summary = scenario.summary.strip()
    if style_hint and style_hint not in summary:
        summary = f"{summary} ({style_hint})"

    cut_flow: list[StorylineCutItem] = []
    for item in scenario.cut_flow:
        scene = normalize_cut_scene_for_topic(item.scene.strip(), item.cut, normalized_topic)
        emotion = item.emotion.strip()
        narration = (item.narration or _scenario_narration_from_scene(scene, item.cut)).strip()
        subtitle = (item.subtitle or narration).strip()
        cut_flow.append(
            StorylineCutItem(
                cut=item.cut,
                scene=scene,
                emotion=emotion,
                narration=narration,
                subtitle=subtitle,
            )
        )

    return GenerateStorylineResponse(
        topic=normalized_topic,
        summary=summary,
        story_arc=story_arc,
        cut_flow=cut_flow,
    )


def build_mock_storyline(topic: str, style: str = "") -> GenerateStorylineResponse:
    normalized_topic = topic.strip()
    style_hint = style.strip()
    subject = "뽀식이" if "뽀식" in normalized_topic else "주인공"

    if "퇴근" in normalized_topic:
        story_arc = "도입-긴장-기대-만남-안정"
        summary = (
            f"{subject}는 집사가 퇴근하기를 기다리며 조용한 척하지만 사실은 계속 현관 소리에 신경을 쓴다. "
            f"집사가 돌아오자 모르는 척하다가 결국 반가움을 숨기지 못한다."
        )
        cuts = [
            {
                "cut": 1,
                "scene": "거실에서 현관 쪽을 바라보며 앉아 있는 {subject}".format(subject=subject),
                "emotion": "기대",
                "narration": "어떤 소리가 들릴까?",
                "subtitle": "어떤 소리가 들릴까?",
            },
            {
                "cut": 2,
                "scene": "문 쪽으로 귀를 기울이는 모습",
                "emotion": "초조",
                "narration": "문 소리가 났나?",
                "subtitle": "문 소리가 났나?",
            },
            {
                "cut": 3,
                "scene": "집사가 들어오는 순간 고개를 돌리는 모습",
                "emotion": "놀람",
                "narration": "지금인가?",
                "subtitle": "지금인가?",
            },
            {
                "cut": 4,
                "scene": "눈치를 보며 다가가려는 자세",
                "emotion": "설렘",
                "narration": "이제 다가가도 될까?",
                "subtitle": "이제 다가가도 될까?",
            },
            {
                "cut": 5,
                "scene": "소파에 편안히 앉아 있는 모습",
                "emotion": "안도",
                "narration": "이제야 마음이 놓인다.",
                "subtitle": "이제야 마음이 놓인다.",
            },
        ]
    elif topic_implies_dog_cafe(normalized_topic):
        story_arc = "입장-접근-눈치-장난-만족"
        summary = (
            f"{subject}는 같은 애견카페 안에서 다른 친구 강아지들과 {normalized_topic}. "
            "장소는 변하지 않고 표정과 포즈만 달라지며 이야기가 이어진다."
        )
        cuts = build_dog_cafe_storyline_cuts(subject)
    elif "루틴" in normalized_topic:
        story_arc = "도입-관찰-반응-전개-마무리"
        summary = (
            f"{subject}는 '{normalized_topic}' 속에서 익숙한 하루의 리듬을 따라 움직인다. "
            f"작은 습관과 짧은 감정 변화가 이어지며, 마지막에는 따뜻한 여운으로 마무리된다."
        )
        cuts = [
            {
                "cut": 1,
                "scene": "아침 햇살이 스며드는 방 안에서 기지개를 켜는 모습",
                "emotion": "차분함",
                "narration": "오늘도 같은 일과가 시작된다.",
                "subtitle": "오늘도 같은 일과가 시작된다.",
            },
            {
                "cut": 2,
                "scene": "주위를 살피며 천천히 자리에서 일어나는 동작",
                "emotion": "호기심",
                "narration": "무엇이 나를 기다리고 있을까?",
                "subtitle": "무엇이 나를 기다리고 있을까?",
            },
            {
                "cut": 3,
                "scene": "익숙한 공간을 지나며 주변을 바라보는 순간",
                "emotion": "안정",
                "narration": "여기서 항상 느끼던 그 느낌이야.",
                "subtitle": "여기서 항상 느끼던 그 느낌이야.",
            },
            {
                "cut": 4,
                "scene": "작은 변화에 반응하며 발걸음을 옮기는 모습",
                "emotion": "소소한 기쁨",
                "narration": "오늘은 조금 다른 느낌이다.",
                "subtitle": "오늘은 조금 다른 느낌이다.",
            },
            {
                "cut": 5,
                "scene": "편안히 쉬면서 하루를 마무리하는 모습",
                "emotion": "안도",
                "narration": "이제야 진짜 휴식이 온다.",
                "subtitle": "이제야 진짜 휴식이 온다.",
            },
        ]
    else:
        story_arc = "도입-전개-전환-클라이맥스-마무리"
        style_clause = f" {style_hint} 톤으로" if style_hint else ""
        summary = (
            f"'{normalized_topic}'을(를) 중심으로{style_clause} 짧은 이야기가 전개된다. "
            f"{subject}의 시선을 따라 분위기가 쌓이고, 마지막 컷에서 감정이 정리된다."
        )
        cuts = [
            {
                "cut": 1,
                "scene": "첫 장면에서 주인공이 주위를 살피는 모습",
                "emotion": "호기심",
                "narration": "무엇이 시작될까?",
                "subtitle": "무엇이 시작될까?",
            },
            {
                "cut": 2,
                "scene": "중요한 요소를 발견하고 반응하는 장면",
                "emotion": "집중",
                "narration": "조금 더 다가가 보자.",
                "subtitle": "조금 더 다가가 보자.",
            },
            {
                "cut": 3,
                "scene": "감정이 서서히 드러나는 순간",
                "emotion": "어어움",
                "narration": "이제 이야기가 깊어졌다.",
                "subtitle": "이제 이야기가 깊어졌다.",
            },
            {
                "cut": 4,
                "scene": "결정적인 행동이 이어지는 장면",
                "emotion": "결의",
                "narration": "이제 선택할 때다.",
                "subtitle": "이제 선택할 때다.",
            },
            {
                "cut": 5,
                "scene": "마지막으로 여운을 남기며 정리되는 장면",
                "emotion": "안도",
                "narration": "오늘의 이야기가 이렇게 마무리된다.",
                "subtitle": "오늘의 이야기가 이렇게 마무리된다.",
            },
        ]

    return GenerateStorylineResponse(
        topic=normalized_topic,
        summary=summary,
        story_arc=story_arc,
        cut_flow=[StorylineCutItem.model_validate(item) for item in cuts],
    )


CUT_PROMPT_TYPE_SEQUENCE = ["ESTABLISHING", "EMOTIONAL", "REVEAL", "TRANSITION", "ENDING"]


def build_cut_prompts_from_storyline(
    *,
    topic: str,
    style: str = "",
    summary: str = "",
    cut_flow: list[Any],
) -> GenerateCutPromptsResponse:
    normalized_topic = topic.strip()
    style_hint = style.strip() or "cinematic"
    parsed_flow: list[dict[str, str]] = []
    for item in cut_flow:
        if item is None:
            continue
        if isinstance(item, dict):
            parsed_flow.append(
                {
                    "scene": str(item.get("scene", "")).strip(),
                    "narration": str(item.get("narration", "")).strip(),
                    "subtitle": str(item.get("subtitle", "")).strip(),
                    "emotion": str(item.get("emotion", "")).strip(),
                }
            )
        else:
            text = str(item).strip()
            if text:
                parsed_flow.append({"scene": "", "narration": text, "subtitle": text, "emotion": ""})
    if not parsed_flow:
        raise HTTPException(status_code=400, detail="Storyline cut_flow is empty.")

    while len(parsed_flow) < 5:
        parsed_flow.append(parsed_flow[-1])
    parsed_flow = parsed_flow[:5]

    cuts: list[CutPromptItem] = []
    for index, flow_item in enumerate(parsed_flow, start=1):
        cut_type = CUT_PROMPT_TYPE_SEQUENCE[index - 1]
        flow_line = " ".join(
            part for part in (flow_item["scene"], flow_item["narration"], flow_item["subtitle"], flow_item["emotion"]) if part
        ).strip()
        subtitle = flow_item["subtitle"] or (flow_line if len(flow_line) <= 28 else f"{flow_line[:25]}…")
        narration = flow_item["narration"] or flow_line
        emotion = flow_item["emotion"]
        if topic_implies_dog_cafe(normalized_topic):
            flow_line = normalize_cut_scene_for_topic(flow_line, index, normalized_topic)
            scene_description = normalize_cut_scene_for_topic(
                flow_item["scene"] or flow_line,
                index,
                normalized_topic,
            )
            image_prompt = build_dog_cafe_image_scene_prompt(
                style=style_hint,
                topic=normalized_topic,
                cut_number=index,
                action_hint=flow_line,
            )
        else:
            if index == 1 and summary and summary not in flow_line:
                narration = f"{summary} {narration}".strip()
            scene_description = flow_item["scene"] or (
                f"컷 {index}: {flow_line}. "
                f"'{normalized_topic}' 스토리라인 흐름에 맞춘 {cut_type.lower()} 장면."
            )
            image_prompt = (
                f"{style_hint}, {flow_line}, topic: {normalized_topic}, "
                f"cinematic still, detailed composition, realistic lighting, 16:9 frame, no text"
            )
        if topic_implies_dog_cafe(normalized_topic):
            if index == 1 and summary and summary not in narration:
                narration = f"{summary} {narration}".strip()
        elif index == 1 and summary and summary not in flow_line:
            narration = f"{summary} {narration}".strip()
        cuts.append(
            CutPromptItem(
                cut_number=index,
                cut_type=cut_type,
                scene_description=scene_description,
                narration=narration,
                subtitle=subtitle,
                emotion=emotion,
                image_prompt=image_prompt,
            )
        )

    return GenerateCutPromptsResponse(
        topic=normalized_topic,
        style=style_hint,
        summary=summary.strip(),
        cuts=cuts,
    )


def build_cut_scene_prompt(scene_image_prompt: str, *, topic: str = "", cut_number: int = 0) -> str:
    base = f"CUT SCENE: {scene_image_prompt}"
    lock = build_scene_context_lock_prompt(topic, cut_number)
    if lock:
        return f"{base}. {lock}"
    return base


def build_camera_prompt(template: dict, visual_style_lock: dict[str, str]) -> str:
    return (
        f"CAMERA: {template['shot_type']}, {template['lens']}, {template['camera_path']}, "
        f"{template['framing']}, {template['lighting']}. {visual_style_lock_text(visual_style_lock)} "
        f"Acting state: {template['action_state']}; only {template['allowed_subject_motion']}; "
        f"avoid {', '.join(template['blocked_actions'])}."
    )


def normalize_continuity_strength(strength: str) -> str:
    normalized = (strength or "medium").strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    return "medium"


def build_inheritance_strength_prompt(strength: str) -> str:
    normalized = normalize_continuity_strength(strength)
    if normalized == "low":
        return (
            "REFERENCE FRAME INHERITANCE (low): keep the subject loosely similar to the reference frame "
            "while allowing camera, pose, and scene context to change."
        )
    if normalized == "high":
        return (
            "REFERENCE FRAME INHERITANCE (high): STRICTLY preserve the exact same bposik_v2 individual from the reference image. "
            f"{BPOSIK_FACE_SHAPE_LOCK}. {BPOSIK_BODY_IDENTITY_LOCK}. "
            "Do not change breed, age, color, face shape, muzzle length, body size, or silhouette. "
            "Only change expression, gaze direction, pose, and small body action."
        )
    return (
        "REFERENCE FRAME INHERITANCE (medium): preserve the exact same bposik_v2 face and body identity from the reference frame. "
        f"{BPOSIK_FACE_SHAPE_LOCK}. {BPOSIK_BODY_IDENTITY_LOCK}. "
        "Keep short rounded muzzle, round teddy-bear face, compact body, short legs, eye spacing, nose, fur, and ears fixed "
        "while updating scene and camera."
    )


def build_reference_character_prompt(
    *,
    source_cut_number: int = 1,
    strength: str = "medium",
    character_name: str = DEFAULT_ACTIVE_CHARACTER,
    reference_image_path: Path | None = None,
) -> str:
    if reference_image_path and reference_image_path.exists():
        public_path = reference_public_url_for_path(reference_image_path)
        source_label = f"reference file {reference_image_path.name}"
    else:
        public_path = REFERENCE_CHARACTER_PUBLIC_PATH
        source_label = f"source CUT {source_cut_number}"
    return (
        f"REFERENCE CHARACTER: {character_name}; match {source_label} at {public_path}. "
        f"{build_inheritance_strength_prompt(strength)} "
        f"{BPOSIK_STRONG_IDENTITY_LOCK} "
        "Lock outward appearance only (face, head shape, eye spacing, nose, muzzle, compact body, short legs, "
        "fur color, teddy-bear grooming silhouette, ear shape). "
        "Do not lock acting; expression, gaze, ears, tail, and pose may change per cut."
    )


def build_reference_character_section(
    *,
    character_name: str,
    reference_image_path: Path | None,
    strength: str = "medium",
) -> str:
    normalized_name = normalize_active_character_name(character_name)
    line = f"Reference Character: {normalized_name}"
    if not reference_image_path or not reference_image_path.exists():
        return f"{line} (prompt-only lock; add images to reference_characters/{normalized_name})"
    return line


def build_combined_negative_prompt() -> str:
    return (
        "text, watermark, logo, distorted anatomy, flicker, warped geometry, "
        f"{CHARACTER_NEGATIVE_PROMPT}, {EMOTION_EXPRESSION_NEGATIVE}"
    )


def log_reference_image_usage(
    *,
    reference_image_path: Path | None,
    reference_frame: dict | None,
    character_name: str = DEFAULT_ACTIVE_CHARACTER,
) -> None:
    frame = reference_frame or {}
    mode = str(frame.get("mode") or "")
    label = normalize_active_character_name(character_name)
    if mode == "edit_with_reference":
        print(f"[reference] {label} image reference used")
    elif mode in {"generate", "generate_prompt_only_fallback"} or frame.get("reference_fallback"):
        print("[reference] prompt-only fallback")
    elif reference_image_path and reference_image_path.exists() and mode != "reference_conversion_failed":
        print(f"[reference] {label} image reference used")
    else:
        print("[reference] prompt-only fallback")


def build_reference_frame_edit_prompt(
    *,
    base_prompt: str,
    strength: str,
    previous_cut_image_path: Path | None,
) -> str:
    inheritance_prompt = build_inheritance_strength_prompt(strength)
    previous_note = ""
    if previous_cut_image_path and previous_cut_image_path.exists():
        previous_note = (
            f" PREVIOUS CUT FRAME: continue screen direction and motion continuity from {previous_cut_image_path.name} "
            "while keeping the exact same character identity as the reference frame."
        )
    return (
        f"{BPOSIK_STRONG_IDENTITY_LOCK} {BPOSIK_FACE_SHAPE_LOCK} {BPOSIK_BODY_IDENTITY_LOCK} "
        f"{inheritance_prompt} "
        "Recreate this cinematic 16:9 film still from the reference image. "
        "The subject must remain the exact same bposik_v2 dog with the same short rounded muzzle, round teddy-bear face, "
        "and same small compact body with short legs as the reference. "
        f"{base_prompt}{previous_note}"
    )


def save_current_reference_image(source_path: Path) -> tuple[str, str | None]:
    if not source_path.exists():
        return REFERENCE_CHARACTER_PUBLIC_PATH, f"Reference source file not found: {source_path}"

    CURRENT_REFERENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        if Image is not None:
            with Image.open(source_path) as image:
                image.convert("RGBA" if "A" in image.getbands() else "RGB").save(
                    CURRENT_REFERENCE_FILE,
                    format="PNG",
                )
        else:
            shutil.copy2(source_path, CURRENT_REFERENCE_FILE)
    except Exception as exc:
        return REFERENCE_CHARACTER_PUBLIC_PATH, str(exc)

    return REFERENCE_CHARACTER_PUBLIC_PATH, None


def save_reference_character_from_image_url(image_url: str, *, source_cut_number: int) -> tuple[ReferenceCharacter, str | None]:
    source_path = resolve_local_image_path(image_url)
    if source_path is None:
        return (
            ReferenceCharacter(
                path=REFERENCE_CHARACTER_PUBLIC_PATH,
                source_cut_number=source_cut_number,
                prompt=build_reference_character_prompt(source_cut_number=source_cut_number),
            ),
            "Reference image URL is not a local generated or reference asset.",
        )

    saved_path, error = save_current_reference_image(source_path)
    reference = ReferenceCharacter(
        path=saved_path,
        source_cut_number=source_cut_number,
        prompt=build_reference_character_prompt(source_cut_number=source_cut_number),
    )
    return reference, error


def combine_image_prompt(
    *,
    reference_character_section: str = "",
    reference_character_prompt: str = "",
    character_lock: str = "",
    master_character_prompt: str = "",
    scene_context_prompt: str,
    cut_story_beat: str = "",
    acting_layer_prompt: str = "",
    emotion_acting_block: str = "",
    style_layer: str = "",
    cut_scene_prompt: str,
    camera_prompt: str,
    consistency_rule: str = "",
    negative_prompt: str = "",
    cut_number: int = 0,
    topic: str = "",
) -> str:
    reference_section = (reference_character_section or reference_character_prompt or BPOSIK_REFERENCE_CHARACTER).strip()
    identity_lock = (character_lock or master_character_prompt or BPOSIK_STRONG_IDENTITY_LOCK).strip()
    resolved_negative = negative_prompt.strip() or build_negative_prompt_for_cut(cut_number, topic=topic)
    identity_with_pose = (
        f"{identity_lock} {BPOSIK_FACE_SHAPE_LOCK} {BPOSIK_BODY_IDENTITY_LOCK} {POSE_VARIETY_RULE}"
    )
    sections = [
        ("A. Reference Character", reference_section),
        ("B. Strong Identity Lock", identity_with_pose),
        (
            "C. Scene Context Lock" if "SCENE CONTEXT LOCK" in scene_context_prompt else "C. Scene Context",
            scene_context_prompt,
        ),
        ("D. Cut Story Beat", cut_story_beat),
        ("E. Acting Layer", acting_layer_prompt),
        ("EMOTION ACTING", emotion_acting_block),
        ("F. Style Layer", style_layer),
        ("G. Camera / Lens", camera_prompt or cut_scene_prompt),
        ("H. Negative Prompt", resolved_negative),
    ]
    parts: list[str] = []
    for label, content in sections:
        if content.strip():
            parts.append(f"{label}: {content.strip()}")
    return sanitize_static_acting_phrases(" ".join(parts))


def adapt_template_for_scene_context(template: dict, scene_context: SceneContext, topic: str) -> dict:
    adapted = dict(template)
    adapted["environmental_motion"] = (
        f"{scene_context.weather}; props and ambience: {scene_context.props}; "
        f"layered background motion continues at varied depth speeds"
    )
    adapted["continuity_notes"] = (
        f"Preserve scene weather ({scene_context.weather}), lighting ({scene_context.lighting}), "
        f"and screen direction for the next cut"
    )
    if not topic_implies_rain_weather(topic):
        for field in ("foreground", "sound_design", "background"):
            value = adapted.get(field, "")
            value = (
                value.replace("Rain, ", "Ambient ")
                .replace("rain streak, ", "soft atmospheric texture, ")
                .replace("rain on glass", "soft window reflections")
                .replace("rain,", "ambient air,")
                .replace("rain ", "ambient ")
            )
            adapted[field] = value
    return adapted


def adapt_template_for_dog_cafe(template: dict, cut_number: int, topic: str, style: str) -> dict:
    if not topic_implies_dog_cafe(topic):
        return template

    adapted = dict(template)
    cafe_pos = dog_cafe_cut_position(cut_number)
    adapted["background"] = (
        "same cozy indoor dog cafe interior, wooden floor, dog play area, "
        "cafe tables and chairs, small dogs softly blurred in background"
    )
    adapted["foreground"] = "dog cafe play zone edge, wooden floor texture, cafe furniture at frame edge"
    adapted["midground"] = f"Bbosik in the same dog cafe play area, {cafe_pos}"
    adapted["environmental_motion"] = (
        "other small dogs moving softly in the same cafe background, warm cafe ambience, "
        "no home furniture, no TV glow, no bedroom objects"
    )
    adapted["continuity_notes"] = (
        "Preserve the same indoor dog cafe location, wooden floor, warm lighting, and background dogs across every cut; "
        "only camera angle and subject pose may change"
    )
    adapted["lighting"] = "soft warm dog cafe lighting with cozy amber practicals, consistent across all cuts"
    adapted["image_prompt"] = build_dog_cafe_image_scene_prompt(
        style="{style}",
        topic="{topic}",
        cut_number=cut_number,
    )
    adapted["start_frame_prompt"] = (
        "{style}, same indoor dog cafe, {topic}, "
        f"{cafe_pos}, first frame, warm cozy pet cafe interior, wooden floor, small dogs in background, "
        "realistic film lighting, 16:9, no text, no home interior, no bedroom, no TV"
    )
    adapted["end_frame_prompt"] = (
        "{style}, same indoor dog cafe, {topic}, "
        f"{cafe_pos}, final frame, same cozy pet cafe interior, wooden floor, small dogs in background, "
        "realistic film lighting, 16:9, no text, no home interior, no bedroom, no TV"
    )
    adapted["start_frame_description"] = (
        f"Begin inside the same cozy dog cafe near {cafe_pos}, with Bbosik and other small dogs visible in the warm cafe interior."
    )
    adapted["end_frame_description"] = (
        f"End inside the same cozy dog cafe at {cafe_pos}, preserving cafe tables, wooden floor, and background dogs."
    )
    if cut_number == 5:
        adapted["image_prompt"] = (
            "{style}, same indoor dog cafe, {topic}, "
            f"{cafe_pos}, final moment inside the same cozy dog cafe, "
            "Bbosik sitting near a cafe table or dog play area, other small dogs softly blurred in background, "
            f"{DOG_CAFE_SCENE_CONTEXT_LOCK}, no bed, no TV, no living room, cinematic still, 16:9 frame"
        )
        adapted["end_frame_description"] = (
            "Final satisfied moment inside the same cozy dog cafe; Bbosik near a cafe table or play area; "
            "no bed, no TV, no living room."
        )
    if isinstance(adapted.get("scene"), str):
        adapted["scene"] = normalize_cut_scene_for_topic(adapted["scene"], cut_number, topic)
    return adapted


def build_continuity_memory(
    template: dict,
    scene_context: SceneContext,
    previous_memory: dict[str, str] | None = None,
) -> dict[str, str]:
    previous_memory = previous_memory or {}
    return {
        "character_position": template["midground"],
        "facing_direction": template["subject_direction"],
        "emotional_state": template.get("emotion_state") or template["emotional_intensity"],
        "lighting_mood": f"{scene_context.lighting}; mood color {template['mood_color']}",
        "weather_state": (
            f"{scene_context.weather} continues with matched direction, haze density, "
            f"ambient texture, and scene props ({scene_context.props})"
        ),
        "camera_momentum": f"{template['camera_path']}; incoming momentum from previous cut: {previous_memory.get('camera_momentum', 'none, opening beat')}",
        "handoff_to_next": template["next_shot_relation"],
    }


def continuity_memory_text(label: str, memory: dict[str, str]) -> str:
    if not memory:
        return f"{label}: none"

    return f"{label}: " + "; ".join(
        f"{key.replace('_', ' ')}: {value}" for key, value in memory.items()
    )


def build_provider_motion_prompt(cut: CutPlan) -> str:
    grammar = cut.motion_grammar or get_motion_grammar(cut.cut_type)
    acting_block = cut.acting_layer_prompt or ""
    reference_line = cut.reference_character_prompt or BPOSIK_REFERENCE_CHARACTER
    return "\n".join(
        [
            f"A. Reference Character: {reference_line}",
            f"B. Strong Identity Lock: {cut.character_lock_prompt or BPOSIK_STRONG_IDENTITY_LOCK}",
            f"C. Scene Context: {cut.scene_context_prompt}",
            f"D. Cut Story Beat: {cut.cut_story_beat or ''}",
            f"E. Acting Layer: {acting_block}",
            f"F. Style Layer: {cut.character_prompt}",
            f"G. Camera / Lens: {cut.camera_path}; {cut.lens}; {cut.shot_type}",
            f"H. Negative Prompt: {build_negative_prompt_for_cut(cut.cut_number)}",
            continuity_memory_text("CONTINUITY CONSTRAINTS", cut.continuity_constraints),
            visual_style_lock_text(cut.visual_style_lock) if cut.visual_style_lock else "",
            f"CUT TYPE: {cut.cut_type}",
            f"ACTING STATE LOCK: action_state={cut.action_state}; emotion_state={cut.emotion_state}; motion_state={cut.motion_state}",
            f"Allowed subject action only: {cut.allowed_subject_motion}",
            f"Forbidden conflicting actions: {', '.join(cut.blocked_actions)}",
            f"SHOT-TYPE MOTION GRAMMAR: {grammar['strategy']}",
            f"Grammar camera movement: {grammar['camera_movement']}",
            f"Grammar depth/parallax: {grammar['depth']}",
            f"Grammar continuity: {grammar['continuity']}",
            f"Grammar avoid: {grammar['avoid']}",
            cut.motion_prompt,
            f"Camera movement: {cut.camera_path}",
            f"Subject motion: {cut.allowed_subject_motion}; {cut.subject_motion}",
            f"Environmental motion and parallax: {cut.environmental_motion}",
            f"Transition continuity: {cut.transition_style}; {cut.previous_shot_relation}; {cut.next_shot_relation}",
            f"Continuity lock: {cut.continuity_notes}",
            continuity_memory_text("PREVIOUS CUT MEMORY", cut.previous_cut_memory),
            continuity_memory_text("CURRENT CUT END MEMORY", cut.continuity_memory),
            f"Motion strength: {cut.motion_strength}; camera speed: {cut.camera_speed}; pacing: {cut.cinematic_pacing}",
            "Keep screen direction, lighting, atmosphere, subject identity, and spatial depth consistent across cuts.",
            "Do not combine contradictory actions inside one cut. The subject performs one clear acting state only.",
        ]
    ).strip()


def create_video_job_manifest(
    plan: VideoPlanResponse,
    selected_cut: int | None = None,
    motion_selection: dict[str, bool] | None = None,
    selected_project: str = DEFAULT_PROJECT_SLUG,
) -> VideoJobResponse:
    validate_selected_cut(plan, selected_cut)
    project_slug, project_dir = resolve_project_dir(selected_project)
    update_project_metadata(project_slug, project_dir, topic=plan.topic)
    created_at = datetime.now(timezone.utc).isoformat()
    job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    job_dir = VIDEO_JOBS_DIR / job_id
    cuts_dir = job_dir / "cuts"
    clips_dir = GENERATED_CLIPS_DIR / job_id
    cuts_dir.mkdir(parents=True, exist_ok=False)
    clips_dir.mkdir(parents=True, exist_ok=False)
    output_dir = ensure_job_output_layout(job_id)
    audio_pipeline = build_default_audio_pipeline_state(job_id, output_dir)

    job_cuts: list[VideoJobCut] = []
    timeline = []
    cursor = 0
    storyboard_export_file = clips_dir / "storyboard_export.json"
    timeline_export_file = clips_dir / "timeline_export.json"
    render_manifest_file = clips_dir / "render_manifest.json"
    export_package_file = clips_dir / f"{job_id}_export_package.zip"

    motion_enabled_by_cut: dict[str, bool] = {}
    for cut in plan.cuts:
        motion_enabled = resolve_motion_enabled_for_cut(cut.cut_number, motion_selection)
        motion_enabled_by_cut[str(cut.cut_number)] = motion_enabled
        cut_name = f"cut_{cut.cut_number:02d}"
        cut_dir = cuts_dir / cut_name
        clip_dir = clips_dir / cut_name
        cut_dir.mkdir()
        clip_dir.mkdir()

        estimated_render_time = max(18, cut.recommended_duration * 8)
        still_hold_duration = STILL_HOLD_DURATION_SECONDS
        timeline_type = "motion" if motion_enabled else "still"
        clip_duration = cut.recommended_duration if motion_enabled else int(round(still_hold_duration))
        video_path = clip_dir / f"{cut_name}.mp4"
        clip_json_file = clip_dir / f"{cut_name}.json"
        prompt_txt_file = clip_dir / "prompt.txt"
        continuity_notes_file = clip_dir / "continuity_notes.txt"
        video_path.touch()

        prompt_payload = {
            "cut_number": cut.cut_number,
            "cut_type": cut.cut_type,
            "visual_style_lock": cut.visual_style_lock,
            "action_state": cut.action_state,
            "emotion_state": cut.emotion_state,
            "motion_state": cut.motion_state,
            "allowed_subject_motion": cut.allowed_subject_motion,
            "blocked_actions": cut.blocked_actions,
            "conflict_warnings": cut.conflict_warnings,
            "character_lock_prompt": cut.character_lock_prompt,
            "scene_context_prompt": cut.scene_context_prompt,
            "reference_character_path": cut.reference_character_path,
            "reference_character_prompt": cut.reference_character_prompt,
            "previous_cut_image_path": cut.previous_cut_image_path,
            "reference_frame_path": cut.reference_frame_path,
            "continuity_inheritance_strength": cut.continuity_inheritance_strength,
            "reference_frame_applied": cut.reference_frame_applied,
            "reference_frame": cut.reference_frame,
            "continuity_constraints": cut.continuity_constraints,
            "active_character": cut.active_character,
            "character_summary": cut.character_summary,
            "character_prompt": cut.character_prompt,
            "acting_layer_prompt": cut.acting_layer_prompt,
            "cut_story_beat": cut.cut_story_beat,
            "motion_grammar": cut.motion_grammar,
            "generator": "runway-style",
            "status": "queued",
            "video_status": "queued",
            "render_progress": 0,
            "duration": cut.recommended_duration,
            "clip_duration": cut.recommended_duration,
            "estimated_render_time": estimated_render_time,
            "source_image_url": cut.image_url,
            "video_prompt": cut.runway_prompt,
            "provider_motion_prompt": build_provider_motion_prompt(cut),
            "negative_prompt": build_negative_prompt_for_cut(cut.cut_number),
            "start_frame_prompt": cut.start_frame_prompt,
            "end_frame_prompt": cut.end_frame_prompt,
            "camera_path": cut.camera_path,
            "subject_motion": cut.subject_motion,
            "environmental_motion": cut.environmental_motion,
            "continuity_notes": cut.continuity_notes,
            "previous_cut_memory": cut.previous_cut_memory,
            "continuity_memory": cut.continuity_memory,
            "transition_in": cut.previous_shot_relation,
            "transition_out": cut.next_shot_relation,
            "output": {
                "format": "mp4",
                "path": str(video_path),
                "url": f"/generated_clips/{job_id}/{cut_name}/{cut_name}.mp4",
            },
        }
        prompt_file = cut_dir / "prompt.json"
        write_json(prompt_file, prompt_payload)
        write_json(
            clip_json_file,
            {
                "job_id": job_id,
                "render_timestamp": created_at,
                "clip_metadata": {
                    "cut_number": cut.cut_number,
                    "cut_type": cut.cut_type,
                    "visual_style_lock": cut.visual_style_lock,
                    "action_state": cut.action_state,
                    "emotion_state": cut.emotion_state,
                    "motion_state": cut.motion_state,
                    "allowed_subject_motion": cut.allowed_subject_motion,
                    "blocked_actions": cut.blocked_actions,
                    "conflict_warnings": cut.conflict_warnings,
                    "character_lock_prompt": cut.character_lock_prompt,
                    "scene_context_prompt": cut.scene_context_prompt,
                    "reference_character_path": cut.reference_character_path,
                    "reference_character_prompt": cut.reference_character_prompt,
                    "previous_cut_image_path": cut.previous_cut_image_path,
                    "reference_frame_path": cut.reference_frame_path,
                    "continuity_inheritance_strength": cut.continuity_inheritance_strength,
                    "reference_frame_applied": cut.reference_frame_applied,
                    "reference_frame": cut.reference_frame,
                    "continuity_constraints": cut.continuity_constraints,
                    "clip_name": cut_name,
                    "video_status": "queued",
                    "render_progress": 0,
                    "clip_duration": cut.recommended_duration,
                    "estimated_render_time": estimated_render_time,
                    "generator": "runway-style",
                    "output_file": str(video_path),
                    "output_url": f"/generated_clips/{job_id}/{cut_name}/{cut_name}.mp4",
                },
                "storyboard": {
                    "active_character": cut.active_character,
                    "character_summary": cut.character_summary,
                    "subtitle": cut.subtitle,
                    "scene_description": cut.scene_description,
                    "shot_type": cut.shot_type,
                    "lens": cut.lens,
                    "movement": cut.movement,
                    "lighting": cut.lighting,
                    "mood_color": cut.mood_color,
                },
                "prompt": {
                    "character_prompt": cut.character_prompt,
                    "character_lock_prompt": cut.character_lock_prompt,
                    "scene_context_prompt": cut.scene_context_prompt,
                    "reference_character_path": cut.reference_character_path,
                    "reference_character_prompt": cut.reference_character_prompt,
                    "previous_cut_image_path": cut.previous_cut_image_path,
                    "reference_frame_path": cut.reference_frame_path,
                    "continuity_inheritance_strength": cut.continuity_inheritance_strength,
                    "reference_frame_applied": cut.reference_frame_applied,
                    "reference_frame": cut.reference_frame,
                    "continuity_constraints": cut.continuity_constraints,
                    "motion_grammar": cut.motion_grammar,
                    "video_prompt": cut.runway_prompt,
                    "motion_prompt": cut.motion_prompt,
                    "provider_motion_prompt": build_provider_motion_prompt(cut),
                    "start_frame_prompt": cut.start_frame_prompt,
                    "end_frame_prompt": cut.end_frame_prompt,
                    "camera_path": cut.camera_path,
                    "subject_motion": cut.subject_motion,
                    "environmental_motion": cut.environmental_motion,
                },
                "continuity_notes": {
                    "notes": cut.continuity_notes,
                    "previous_cut_memory": cut.previous_cut_memory,
                    "continuity_memory": cut.continuity_memory,
                    "previous_shot_relation": cut.previous_shot_relation,
                    "next_shot_relation": cut.next_shot_relation,
                    "emotional_transition": cut.emotional_transition,
                    "camera_transition": cut.camera_transition,
                    "spatial_transition": cut.spatial_transition,
                },
            },
        )
        write_text(
            prompt_txt_file,
            "\n\n".join(
                [
                    f"JOB: {job_id}",
                    f"CUT: {cut.cut_number}",
                    f"CLIP DURATION: {cut.recommended_duration}s",
                    f"ESTIMATED RENDER TIME: {estimated_render_time}s",
                    build_acting_audit_block(
                        {
                            "acting_layer_prompt": cut.acting_layer_prompt,
                            "reference_character_section": cut.reference_character_prompt,
                            "character_lock_prompt": cut.character_lock_prompt,
                            "scene": cut.scene_description,
                            "narration": cut.narration,
                            "subtitle": cut.subtitle,
                            "emotion_state": cut.emotion_state,
                            "start_frame_description": cut.start_frame_description,
                            "end_frame_description": cut.end_frame_description,
                            "camera_path": cut.camera_path,
                            "environmental_motion": cut.environmental_motion,
                        },
                        style=plan.style,
                        topic=plan.topic,
                        cut_number=cut.cut_number,
                    ),
                    f"Reference Character: {cut.active_character or DEFAULT_ACTIVE_CHARACTER}",
                    f"Strong Identity Lock: {cut.character_lock_prompt or BPOSIK_STRONG_IDENTITY_LOCK}",
                    "IMAGE PROMPT:",
                    cut.image_prompt,
                    "NEGATIVE PROMPT:",
                    build_negative_prompt_for_cut(cut.cut_number),
                    "VIDEO PROMPT:",
                    cut.runway_prompt,
                    "MOTION PROMPT:",
                    cut.motion_prompt,
                    "PROVIDER MOTION PROMPT:",
                    build_provider_motion_prompt(cut),
                    "START FRAME:",
                    cut.start_frame_prompt,
                    "END FRAME:",
                    cut.end_frame_prompt,
                    "CAMERA PATH:",
                    cut.camera_path,
                    "SUBJECT MOTION:",
                    cut.subject_motion,
                    "ENVIRONMENTAL MOTION:",
                    cut.environmental_motion,
                ]
            ),
        )
        write_text(
            continuity_notes_file,
            "\n".join(
                [
                    f"Continuity Notes: {cut.continuity_notes}",
                    f"Previous Shot Relation: {cut.previous_shot_relation}",
                    f"Next Shot Relation: {cut.next_shot_relation}",
                    f"Emotional Transition: {cut.emotional_transition}",
                    f"Camera Transition: {cut.camera_transition}",
                    f"Spatial Transition: {cut.spatial_transition}",
                    f"Pacing Curve: {cut.pacing_curve}",
                ]
            ),
        )

        status_payload = {
            "cut_number": cut.cut_number,
            "status": "queued",
            "video_status": "queued",
            "render_progress": 0,
            "clip_duration": cut.recommended_duration,
            "estimated_render_time": estimated_render_time,
            "message": "Prompt package is ready; attach a video generator adapter to render this cut.",
            "updated_at": created_at,
            "video_file": str(video_path),
        }
        write_json(cut_dir / "status.json", status_payload)

        timeline.append(
            {
                "cut_number": cut.cut_number,
                "start": cursor,
                "end": cursor + clip_duration,
                "duration": clip_duration,
                "clip_duration": clip_duration,
                "estimated_render_time": estimated_render_time,
                "storyboard_ref": f"storyboard.cut.{cut.cut_number}",
                "video_ref": f"video.cut.{cut.cut_number}",
                "video_file": str(video_path),
                "video_status": "queued",
                "render_progress": 0,
                "motion_enabled": motion_enabled,
                "timeline_type": timeline_type,
                "still_hold_duration": still_hold_duration,
            }
        )
        cursor += clip_duration

        job_cuts.append(
            VideoJobCut(
                cut_number=cut.cut_number,
                status="queued",
                video_status="queued",
                render_progress=0,
                storyboard_ref=f"storyboard.cut.{cut.cut_number}",
                timeline_ref=f"timeline.cut.{cut.cut_number}",
                prompt_file=str(prompt_file),
                clip_json_file=str(clip_json_file),
                prompt_txt_file=str(prompt_txt_file),
                continuity_notes_file=str(continuity_notes_file),
                video_file=str(video_path),
                video_url=f"/generated_clips/{job_id}/{cut_name}/{cut_name}.mp4",
                clip_duration=clip_duration,
                estimated_render_time=estimated_render_time,
                duration=clip_duration,
                generator="runway-style",
                motion_enabled=motion_enabled,
                timeline_type=timeline_type,
                still_hold_duration=still_hold_duration,
            )
        )

    write_json(job_dir / "storyboard.json", dump_model(plan))
    write_json(job_dir / "timeline.json", {"duration": plan.duration, "clips": timeline})
    write_json(storyboard_export_file, dump_model(plan))
    write_json(
        timeline_export_file,
        {
            "job_id": job_id,
            "render_timestamp": created_at,
            "duration": plan.duration,
            "clips": timeline,
        },
    )

    response = VideoJobResponse(
        job_id=job_id,
        status="queued",
        selected_cut=selected_cut,
        job_dir=str(job_dir),
        manifest_file=str(job_dir / "manifest.json"),
        clips_dir=str(clips_dir),
        render_manifest_file=str(render_manifest_file),
        render_manifest_url=f"/generated_clips/{job_id}/render_manifest.json",
        storyboard_export_file=str(storyboard_export_file),
        storyboard_export_url=f"/generated_clips/{job_id}/storyboard_export.json",
        timeline_export_file=str(timeline_export_file),
        timeline_export_url=f"/generated_clips/{job_id}/timeline_export.json",
        export_package_file=str(export_package_file),
        export_package_url=f"/generated_clips/{job_id}/{export_package_file.name}",
        export_status="completed",
        created_at=created_at,
        cuts=job_cuts,
    )
    render_manifest = {
        **dump_model(response),
        "render_timestamp": created_at,
        "skipped_still_cuts": [
            cut.cut_number for cut in job_cuts if not cut.motion_enabled
        ],
        "job_metadata": {
            "job_id": job_id,
            "selected_project": project_slug,
            "topic": plan.topic,
            "style": plan.style,
            "duration": plan.duration,
            "master_character": dump_model(plan.master_character) if plan.master_character else {},
            "scene_context": dump_model(plan.scene_context) if plan.scene_context else {},
            "reference_character": dump_model(plan.reference_character) if plan.reference_character else {},
            "reference_frame": dump_model(plan.reference_frame) if plan.reference_frame else {},
            "continuity_state": dump_model(plan.continuity_state) if plan.continuity_state else {},
            "cut_count": len(plan.cuts),
            "created_at": created_at,
            "clips_dir": str(clips_dir),
            "export_status": "completed",
        },
        "motion_enabled": motion_enabled_by_cut,
        "clip_metadata": [
            {
                "cut_number": cut.cut_number,
                "video_status": cut.video_status,
                "render_progress": cut.render_progress,
                "clip_duration": cut.clip_duration,
                "estimated_render_time": cut.estimated_render_time,
                "video_file": cut.video_file,
                "prompt_txt_file": cut.prompt_txt_file,
                "clip_json_file": cut.clip_json_file,
                "continuity_notes_file": cut.continuity_notes_file,
                "motion_enabled": cut.motion_enabled,
                "timeline_type": cut.timeline_type,
                "still_hold_duration": cut.still_hold_duration,
                "character_lock_prompt": plan.cuts[index].character_lock_prompt,
                "scene_context_prompt": plan.cuts[index].scene_context_prompt,
                "reference_character_path": plan.cuts[index].reference_character_path,
                "reference_character_prompt": plan.cuts[index].reference_character_prompt,
                "previous_cut_image_path": plan.cuts[index].previous_cut_image_path,
                "reference_frame_path": plan.cuts[index].reference_frame_path,
                "continuity_inheritance_strength": plan.cuts[index].continuity_inheritance_strength,
                "reference_frame_applied": plan.cuts[index].reference_frame_applied,
                "reference_frame": plan.cuts[index].reference_frame,
                "continuity_constraints": plan.cuts[index].continuity_constraints,
            }
            for index, cut in enumerate(job_cuts)
        ],
        "continuity_notes": [
            {
                "cut_number": cut.cut_number,
                "continuity_notes": plan.cuts[index].continuity_notes,
                "previous_shot_relation": plan.cuts[index].previous_shot_relation,
                "next_shot_relation": plan.cuts[index].next_shot_relation,
            }
            for index, cut in enumerate(job_cuts)
        ],
        "audio_pipeline": audio_pipeline,
        "pipeline_status": {
            "video": {
                "status": "waiting_for_video_generator",
                "clips_dir": str(clips_dir),
            },
            "audio": audio_pipeline,
            "subtitles": audio_pipeline.get("subtitles", {}),
            "final_export": audio_pipeline.get("final_export", {}),
        },
    }
    write_json(render_manifest_file, render_manifest)
    write_json(output_dir / "manifest" / "audio_pipeline.json", audio_pipeline)
    write_json(
        job_dir / "manifest.json",
        {
            **dump_model(response),
            "selected_project": project_slug,
            "render_manifest_file": str(render_manifest_file),
            "pipeline": [
                "storyboard_locked",
                "cut_prompts_written",
                "cut_json_written",
                "cut_prompt_txt_written",
                "render_manifest_written",
                "generated_clips_slots_reserved",
                "export_package_zip_created",
                "render_queue_ready",
                "waiting_for_video_generator",
            ],
        },
    )
    create_export_zip(export_package_file, clips_dir)
    metadata = load_project_metadata(project_slug, project_dir)
    metadata["motion_selection"] = motion_enabled_by_cut
    metadata["topic"] = plan.topic
    metadata["style"] = plan.style
    metadata["duration"] = plan.duration
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project_json_path(project_dir), metadata)
    write_json(project_plan_path(project_dir), dump_model(plan))
    run = ensure_current_run(
        project_dir,
        selected_cuts_from_motion_selection(motion_enabled_by_cut),
        target_duration=plan.duration,
    )
    run["job_id"] = job_id
    write_current_run(project_dir, run)
    return response


@app.post("/video-jobs", response_model=VideoJobResponse)
def create_video_job(request: VideoJobRequest):
    return create_video_job_manifest(request.plan, request.selected_cut, request.motion_selection, request.selected_project)


@app.post("/sync-motion-selection")
def sync_motion_selection(request: MotionSelectionRequest):
    render_manifest_path = find_render_manifest_path(request.job_id)
    if not render_manifest_path:
        raise HTTPException(status_code=404, detail="render_manifest.json not found for this job.")

    project_slug, project_dir = resolve_project_dir(request.selected_project)
    selected_cuts = selected_cuts_from_motion_selection(request.motion_enabled)
    metadata = load_project_metadata(project_slug, project_dir)
    current_run = ensure_current_run(
        project_dir,
        selected_cuts,
        target_duration=metadata.get("duration"),
        reset_if_changed=True,
    )
    current_run["job_id"] = request.job_id
    write_current_run(project_dir, current_run)

    manifest = read_json_file(render_manifest_path)
    clip_metadata = list(manifest.get("clip_metadata") or [])
    for row in clip_metadata:
        cut_number = int(row.get("cut_number", 0))
        if cut_number <= 0:
            continue
        enabled = resolve_motion_enabled_for_cut(cut_number, request.motion_enabled)
        row["motion_enabled"] = enabled
        row["timeline_type"] = "motion" if enabled else "still"
        if not enabled:
            row["still_hold_duration"] = float(row.get("still_hold_duration") or STILL_HOLD_DURATION_SECONDS)
            row["clip_duration"] = int(round(row["still_hold_duration"]))

    skipped_still_cuts = sorted(
        {
            int(cut_number)
            for cut_number in (request.skipped_still_cuts or [])
            if int(cut_number) > 0
        }
    )
    if not skipped_still_cuts:
        skipped_still_cuts = [
            int(row.get("cut_number", 0))
            for row in clip_metadata
            if int(row.get("cut_number", 0)) > 0 and not row.get("motion_enabled", True)
        ]

    for row in clip_metadata:
        cut_number = int(row.get("cut_number", 0))
        if cut_number in skipped_still_cuts:
            row["video_status"] = "still_hold"
            row["status"] = "still_hold"
            row["motion_enabled"] = False
            row["timeline_type"] = "still"
            row["still_hold_duration"] = float(row.get("still_hold_duration") or STILL_HOLD_DURATION_SECONDS)
            row["clip_duration"] = int(round(row["still_hold_duration"]))
            row["provider"] = "still_hold"
            row["render_progress"] = 100

    manifest["clip_metadata"] = clip_metadata
    manifest["motion_enabled"] = request.motion_enabled
    manifest["selected_project"] = project_slug
    manifest["skipped_still_cuts"] = skipped_still_cuts
    write_json(render_manifest_path, manifest)
    return {
        "job_id": request.job_id,
        "motion_enabled": request.motion_enabled,
        "skipped_still_cuts": skipped_still_cuts,
        "motion_shots": build_motion_shots_summary(manifest),
        "render_manifest_url": f"/generated_clips/{request.job_id}/render_manifest.json",
    }


@app.post("/generate-video-clip", response_model=GenerateVideoClipResponse)
def generate_video_clip(request: GenerateVideoClipRequest):
    resolve_project_dir(request.selected_project)
    provider_name = request.provider.strip().lower()
    if request.disallow_mock_provider and provider_name == "mock":
        raise HTTPException(
            status_code=400,
            detail="Mock video provider is not allowed for pipeline generation. Select replicate.",
        )
    if request.cut_id is not None and request.cut_id != request.cut_number:
        raise HTTPException(status_code=400, detail="cut_id must match cut_number for single-cut generation.")
    if request.selected_cut is not None and request.selected_cut != request.cut_number:
        raise HTTPException(status_code=400, detail="selected_cut must match cut_number for single-cut generation.")
    if request.motion_enabled and not request.motion_prompt.strip():
        raise HTTPException(status_code=400, detail="motion_prompt is required when motion_enabled is true.")

    if provider_name == "replicate":
        local_image = resolve_local_image_path(request.image_path)
        if local_image is not None and not local_image.exists():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Start image not found for CUT {request.cut_number}: {local_image}. "
                    "Regenerate storyboard images before Replicate video generation."
                ),
            )

    created_at = datetime.now(timezone.utc).isoformat()
    if request.job_id:
        job_id = request.job_id.strip()
        job_dir = GENERATED_CLIPS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
    else:
        job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        job_dir = GENERATED_CLIPS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
    output_dir = ensure_job_output_layout(job_id)
    audio_pipeline = load_or_init_audio_pipeline(job_id) or build_default_audio_pipeline_state(job_id, output_dir)
    timeline_type = "motion" if request.motion_enabled else "still"
    still_hold_duration = float(request.still_hold_duration or STILL_HOLD_DURATION_SECONDS)

    try:
        if request.motion_enabled:
            provider = get_video_provider(request.provider)
            provider_request = VideoGenerationRequest(
                job_id=job_id,
                cut_number=request.cut_number,
                image_path=request.image_path,
                image_prompt=request.image_prompt,
                motion_prompt=request.motion_prompt,
                duration=request.duration,
                output_dir=job_dir,
                character_lock_prompt=request.character_lock_prompt,
                scene_context_prompt=request.scene_context_prompt,
                continuity_constraints=request.continuity_constraints,
            )
            if provider_name == "replicate":
                print(
                    f"[video] Generating CUT {request.cut_number} with replicate "
                    f"(project={request.selected_project}, job={job_id})"
                )
            provider_result = provider.generate_clip(provider_request)
            print(
                f"[video] CUT {request.cut_number} completed · provider={provider_result.provider} "
                f"· status={provider_result.status}"
            )
            result = {
                "job_id": provider_result.job_id,
                "status": provider_result.status,
                "provider": provider_result.provider,
                "clip_path": provider_result.clip_path,
                "manifest_path": provider_result.manifest_path,
                "prompt_path": provider_result.prompt_path,
                "message": provider_result.message,
                "motion_enabled": True,
                "timeline_type": "motion",
                "still_hold_duration": still_hold_duration,
            }
        else:
            print(f"Creating still hold for CUT {request.cut_number}...")
            result = create_still_hold_clip(
                job_id=job_id,
                cut_number=request.cut_number,
                image_path=request.image_path,
                still_hold_duration=still_hold_duration,
                image_prompt=request.image_prompt,
            )
            print("Still hold completed")
    except HTTPException:
        raise
    except Exception as error:
        failure_manifest = {
            "job_id": job_id,
            "status": "failed",
            "provider": request.provider,
            "selected_project": request.selected_project,
            "created_at": created_at,
            "cut_number": request.cut_number,
            "cut_id": request.cut_id or request.cut_number,
            "selected_cut": request.selected_cut,
            "cut_type": request.cut_type,
            "visual_style_lock": request.visual_style_lock,
            "action_state": request.action_state,
            "emotion_state": request.emotion_state,
            "motion_state": request.motion_state,
            "allowed_subject_motion": request.allowed_subject_motion,
            "blocked_actions": request.blocked_actions,
            "character_lock_prompt": request.character_lock_prompt,
            "continuity_constraints": request.continuity_constraints,
            "motion_grammar": request.motion_grammar,
            "active_character": request.active_character,
            "image_path": request.image_path,
            "image_prompt": request.image_prompt,
            "motion_prompt": request.motion_prompt,
            "duration": request.duration,
            "cut_index": request.cut_index,
            "per_cut_duration": request.per_cut_duration,
            "total_duration": request.total_duration,
            "selected_cuts": request.selected_cuts,
            "message": str(error),
        }
        write_json(job_dir / "job_manifest.json", failure_manifest)
        write_json(
            job_dir / "render_manifest.json",
            {
                **failure_manifest,
                "render_timestamp": created_at,
                "job_metadata": {
                    "job_id": job_id,
                    "status": "failed",
                    "provider": request.provider,
                    "created_at": created_at,
                    "clips_dir": str(job_dir),
                    "render_mode": "current_cut",
                },
                "clip_metadata": [failure_manifest],
            },
        )
        raise HTTPException(status_code=400, detail=str(error)) from error

    clip_path = Path(result["clip_path"])
    clip_url = ""
    try:
        clip_url = f"/generated_clips/{clip_path.relative_to(GENERATED_CLIPS_DIR)}"
    except ValueError:
        clip_url = result["clip_path"]
    latest_video_url = sync_latest_cut_video(
        clip_path=clip_path,
        source_job_id=result["job_id"],
        request=request,
        clip_url=clip_url,
        message=result["message"],
    )

    response_status = (
        "completed" if result["status"] in {"completed", "mock_completed", "replicate_completed"} else result["status"]
    )
    clip_duration = float(still_hold_duration) if not request.motion_enabled else float(request.duration)
    root_manifest = {
        "job_id": result["job_id"],
        "status": response_status,
        "provider_status": result["status"],
        "provider": result["provider"],
        "selected_project": request.selected_project,
        "created_at": created_at,
        "cut_number": request.cut_number,
        "cut_id": request.cut_id or request.cut_number,
        "selected_cut": request.selected_cut,
        "cut_type": request.cut_type,
        "visual_style_lock": request.visual_style_lock,
        "action_state": request.action_state,
        "emotion_state": request.emotion_state,
        "motion_state": request.motion_state,
        "allowed_subject_motion": request.allowed_subject_motion,
        "blocked_actions": request.blocked_actions,
        "character_lock_prompt": request.character_lock_prompt,
        "continuity_constraints": request.continuity_constraints,
        "motion_grammar": request.motion_grammar,
        "active_character": request.active_character,
        "image_path": request.image_path,
        "image_prompt": request.image_prompt,
        "motion_prompt": request.motion_prompt,
        "duration": clip_duration,
        "cut_index": request.cut_index,
        "per_cut_duration": request.per_cut_duration,
        "total_duration": request.total_duration,
        "selected_cuts": request.selected_cuts,
        "motion_enabled": request.motion_enabled,
        "timeline_type": timeline_type,
        "still_hold_duration": still_hold_duration,
        "clip_path": result["clip_path"],
        "clip_url": clip_url,
        "video_url": clip_url,
        "latest_video_url": latest_video_url,
        "video_prompt_txt": result["prompt_path"],
        "clip_manifest_file": result["manifest_path"],
        "message": result["message"],
        "adapter_contract": {
            "input": ["provider", "cut_id", "image_prompt", "motion_prompt", "duration", "motion_enabled", "job_id"],
            "output": ["job_id", "status", "clip_path", "provider", "message", "timeline_type"],
        },
    }
    manifest_file = job_dir / "job_manifest.json"
    write_json(manifest_file, root_manifest)
    clip_payload = {
        "cut_number": request.cut_number,
        "cut_id": request.cut_id or request.cut_number,
        "status": response_status,
        "video_status": response_status,
        "provider_status": result["status"],
        "provider": result["provider"],
        "selected_project": request.selected_project,
        "video_file": result["clip_path"],
        "video_url": clip_url,
        "latest_video_url": latest_video_url,
        "duration": clip_duration,
        "clip_duration": clip_duration,
        "image_prompt": request.image_prompt,
        "motion_prompt": request.motion_prompt,
        "character_lock_prompt": request.character_lock_prompt,
        "continuity_constraints": request.continuity_constraints,
        "clip_manifest_file": result["manifest_path"],
        "prompt_txt_file": result["prompt_path"],
        "motion_enabled": request.motion_enabled,
        "timeline_type": timeline_type,
        "still_hold_duration": still_hold_duration,
        "render_progress": 100 if response_status == "completed" else 0,
    }
    render_manifest_url = upsert_render_manifest_clip(job_id, clip_payload)
    render_manifest_file = GENERATED_CLIPS_DIR / job_id / "render_manifest.json"
    render_manifest = read_json_file(render_manifest_file)
    render_manifest["audio_pipeline"] = audio_pipeline
    render_manifest["pipeline_status"] = {
        "video": {"status": response_status, "clips_dir": str(job_dir)},
        "audio": audio_pipeline,
        "subtitles": audio_pipeline.get("subtitles", {}),
        "final_export": audio_pipeline.get("final_export", {}),
    }
    write_json(render_manifest_file, render_manifest)
    write_json(output_dir / "manifest" / "audio_pipeline.json", audio_pipeline)
    if response_status == "completed" and request.motion_enabled:
        project_slug, project_dir = resolve_project_dir(request.selected_project)
        motion_selection = load_project_motion_selection(project_slug, project_dir)
        selected_cuts = selected_cuts_from_motion_selection(motion_selection)
        if not selected_cuts:
            selected_cuts = [request.cut_number]
        run = update_current_run_done(project_dir, selected_cuts, "video_done", [request.cut_number])
        run["job_id"] = result["job_id"]
        write_current_run(project_dir, run)

    return GenerateVideoClipResponse(
        job_id=result["job_id"],
        status=response_status,
        provider_status=result["status"],
        selected_cut=request.selected_cut,
        clip_path=result["clip_path"],
        provider=result["provider"],
        message=result["message"],
        manifest_file=str(manifest_file),
        render_manifest_file=str(render_manifest_file),
        render_manifest_url=render_manifest_url,
        clip_url=clip_url,
        video_url=clip_url,
        latest_video_url=latest_video_url,
        prompt_txt_file=result["prompt_path"],
        clip_manifest_file=result["manifest_path"],
        motion_enabled=request.motion_enabled,
        timeline_type=timeline_type,
        still_hold_duration=still_hold_duration,
    )


@app.post("/generate-sequentially", response_model=GenerateVideoClipResponse)
def generate_sequentially(request: GenerateSequentiallyRequest):
    validate_selected_cut(request.plan, request.selected_cut)
    cut = next((item for item in request.plan.cuts if item.cut_number == request.selected_cut), None)
    if cut is None:
        raise HTTPException(status_code=400, detail=f"selected_cut {request.selected_cut} is not included in the storyboard.")

    motion_enabled = getattr(cut, "motion_enabled", default_motion_enabled_for_cut(cut.cut_number))
    if not motion_enabled:
        raise HTTPException(
            status_code=400,
            detail=f"CUT {cut.cut_number} is a still frame (motion_enabled=false) and is skipped from the video queue.",
        )

    return generate_video_clip(
        GenerateVideoClipRequest(
            cut_number=cut.cut_number,
            selected_cut=request.selected_cut,
            motion_enabled=True,
            cut_type=cut.cut_type,
            visual_style_lock=cut.visual_style_lock,
            action_state=cut.action_state,
            emotion_state=cut.emotion_state,
            motion_state=cut.motion_state,
            allowed_subject_motion=cut.allowed_subject_motion,
            blocked_actions=cut.blocked_actions,
            character_lock_prompt=cut.character_lock_prompt,
            scene_context_prompt=cut.scene_context_prompt,
            continuity_constraints=cut.continuity_constraints,
            motion_grammar=cut.motion_grammar,
            active_character=cut.active_character,
            image_path=cut.image_url or cut.sample_image_url,
            image_prompt=cut.image_prompt,
            motion_prompt=build_provider_motion_prompt(cut),
            duration=cut.recommended_duration,
            provider=request.provider,
            selected_project=request.selected_project,
        )
    )


@app.get("/video-jobs/{job_id}", response_model=VideoJobResponse)
def get_video_job(job_id: str):
    manifest_path = VIDEO_JOBS_DIR / job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Video job not found")

    return VideoJobResponse(**json.loads(manifest_path.read_text(encoding="utf-8")))


def crop_to_16_9(image_bytes: bytes) -> bytes:
    if Image is None:
        return image_bytes

    with Image.open(BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        width, height = image.size
        target_ratio = 16 / 9
        current_ratio = width / height

        if current_ratio > target_ratio:
            new_width = int(height * target_ratio)
            left = (width - new_width) // 2
            image = image.crop((left, 0, left + new_width, height))
        elif current_ratio < target_ratio:
            new_height = int(width / target_ratio)
            top = (height - new_height) // 2
            image = image.crop((0, top, width, top + new_height))

        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def generate_image_for_cut(
    cut_number: int,
    prompt: str,
    fallback_url: str,
    *,
    image_provider: str = "openai",
    continuity_inheritance_strength: str = DEFAULT_CONTINUITY_INHERITANCE_STRENGTH,
    reference_image_path: Path | None = None,
    previous_cut_image_path: Path | None = None,
    use_reference_inheritance: bool = False,
    output_path: Path | None = None,
) -> tuple[str, str, str | None, dict]:
    strength = normalize_continuity_strength(continuity_inheritance_strength)
    provider_name = (image_provider or "openai").strip().lower()
    final_prompt = prompt

    if use_reference_inheritance and reference_image_path and reference_image_path.exists():
        final_prompt = build_reference_frame_edit_prompt(
            base_prompt=prompt,
            strength=strength,
            previous_cut_image_path=previous_cut_image_path,
        )
    elif "Reference Character:" in prompt or "REFERENCE CHARACTER" in prompt:
        final_prompt = (
            f"{prompt}. {REFERENCE_CHARACTER_IDENTITY_RULES}. "
            "Treat the reference character as the exact same individual in this frame."
        )

    final_prompt = (
        f"{final_prompt}. cinematic architecture style, realistic film still, "
        "wide 16:9 composition, premium production design, no text, no watermark. "
        f"Negative prompt: {build_negative_prompt_for_cut(cut_number)}."
    )

    try:
        provider = get_image_provider(provider_name, crop_to_16_9=crop_to_16_9)
    except ValueError as exc:
        return fallback_url, "failed", str(exc), {}

    result = provider.generate_image(
        ImageGenerationRequest(
            cut_number=cut_number,
            prompt=final_prompt,
            fallback_url=fallback_url,
            reference_image_path=reference_image_path if use_reference_inheritance else None,
            previous_cut_image_path=previous_cut_image_path if use_reference_inheritance else None,
            continuity_inheritance_strength=strength,
            output_path=output_path or GENERATED_IMAGE_DIR / f"cut_{cut_number}.png",
        )
    )

    if provider_name == "mock" or result.status == "mock":
        return (
            result.image_url,
            "mock",
            None,
            {
                **result.reference_frame,
                "image_provider": result.provider,
                "inheritance_applied": False,
            },
        )

    if result.status != "generated":
        error_message = result.error or f"{provider_name} image generation failed"
        print(f"[image] CUT {cut_number} failed ({provider_name}): {error_message}")
        return fallback_url, "failed", error_message, result.reference_frame or {}

    image_url = result.image_url
    if result.status == "generated" and output_path is not None:
        try:
            image_url = project_url_for_path(output_path)
        except ValueError:
            image_url = result.image_url
    if result.status == "generated" and image_url.startswith("/generated_images/"):
        image_url = f"{image_url.split('?', 1)[0]}?v={int(time.time())}"
    elif result.status == "generated" and image_url.startswith("/projects/"):
        image_url = f"{image_url.split('?', 1)[0]}?v={int(time.time())}"

    reference_frame_meta = {
        **result.reference_frame,
        "stored_path": reference_public_url_for_path(reference_image_path)
        if reference_image_path and reference_image_path.exists()
        else REFERENCE_CHARACTER_PUBLIC_PATH,
        "public_path": reference_public_url_for_path(reference_image_path)
        if reference_image_path and reference_image_path.exists()
        else REFERENCE_CHARACTER_PUBLIC_PATH,
        "continuity_inheritance_strength": strength,
        "image_provider": result.provider,
        "reference_image_path": str(reference_image_path) if reference_image_path else None,
        "previous_cut_image_path": str(previous_cut_image_path) if previous_cut_image_path else None,
        "inheritance_applied": use_reference_inheritance and result.status == "generated",
    }
    image_warning = result.error if result.status == "generated" and result.error else None
    log_reference_image_usage(
        reference_image_path=reference_image_path,
        reference_frame=result.reference_frame,
        character_name=reference_image_path.parent.name if reference_image_path else DEFAULT_ACTIVE_CHARACTER,
    )
    return image_url, result.status, image_warning or result.error, reference_frame_meta


def create_platform_prompts(
    *,
    style: str,
    topic: str,
    template: dict,
    start_frame_prompt: str,
    end_frame_prompt: str,
    visual_style_lock: dict[str, str],
    cut_number: int = 1,
) -> dict[str, str]:
    acting_layer = template.get("acting_layer_prompt", "")
    video_beats = build_video_start_motion_end(template, style, topic, cut_number)
    emotion_block = template.get("emotion_acting_block") or build_emotion_acting_block(video_beats["beat"], cut_number)
    shared_motion = sanitize_static_acting_phrases(
        f"Start: {video_beats['start']} "
        f"Motion: {video_beats['motion']} "
        f"End: {video_beats['end']} "
        f"ACTING LAYER: {acting_layer} "
        f"{emotion_block} "
        f"Face shape lock: {BPOSIK_FACE_SHAPE_LOCK}. {ACTING_FACE_SHAPE_GUARD}. "
        f"Body identity lock: {BPOSIK_BODY_IDENTITY_LOCK}. {ACTING_BODY_SHAPE_GUARD}. "
        f"Pose directive: {get_cut_pose_directive(cut_number)}. {POSE_VARIETY_RULE}. "
        f"{template['camera_path']}. {template['subject_motion']}. "
        f"{template['environmental_motion']}. {template['motion_intensity']}. "
        f"Acting state lock: action={template['action_state']}, emotion={template['emotion_state']}, motion={template['motion_state']}. "
        f"Only allowed subject action: {template['allowed_subject_motion']}. "
        f"Forbidden conflicting actions: {', '.join(template['blocked_actions'])}. "
        f"{continuity_memory_text('Previous cut memory', template.get('previous_cut_memory', {}))}. "
        f"{continuity_memory_text('Current cut end memory', template.get('continuity_memory', {}))}. "
        f"Continuity: {template['continuity_notes']}."
    )
    shot_context = (
        f"{style}, {topic}, {template['shot_type']}, {template['lens']}, "
        f"{template['framing']}, visual focus: {template['visual_focus']}, "
        f"emotional pacing: {template['cinematic_pacing']}"
    )
    style_lock = visual_style_lock_text(visual_style_lock)
    scene_context = template.get("scene_context_prompt", "")
    continuity_constraints = continuity_memory_text("Continuity constraints", template.get("continuity_constraints", {}))
    cut_scene = template.get("cut_scene_prompt", shot_context)
    camera_prompt = template.get("camera_prompt", "")
    cut_story_beat = template.get("cut_story_beat", "")
    prompt_stack = combine_image_prompt(
        reference_character_section=template.get("reference_character_section", BPOSIK_REFERENCE_CHARACTER),
        character_lock=BPOSIK_STRONG_IDENTITY_LOCK,
        scene_context_prompt=scene_context,
        cut_story_beat=cut_story_beat,
        acting_layer_prompt=acting_layer,
        emotion_acting_block=emotion_block,
        style_layer=build_style_layer_text(style),
        cut_scene_prompt=cut_scene,
        camera_prompt=camera_prompt or cut_scene,
        cut_number=cut_number,
        negative_prompt=build_negative_prompt_for_cut(cut_number, topic),
        topic=topic,
    )

    return {
        "runway_prompt": (
            f"{prompt_stack} {continuity_constraints} {style_lock} "
            f"Start frame: {start_frame_prompt}. Motion: {shared_motion} "
            f"End frame: {end_frame_prompt}. Realistic cinematic camera inertia, layered depth, no text, no watermark."
        ),
        "kling_prompt": (
            f"{prompt_stack} {continuity_constraints} {style_lock} Precise subject and camera motion: {shared_motion} "
            f"tain consistent subject identity, stable anatomy, coherent foreground-midground-background depth. "
            f"Opening image: {start_frame_prompt}. Closing image: {end_frame_prompt}."
        ),
        "veo_prompt": (
            f"{prompt_stack} {continuity_constraints} {style_lock} Create a filmic video shot with cinematic blocking, realistic acceleration and deceleration, "
            f"spatially consistent camera path, layered environmental motion, and restrained emotional pacing. "
            f"Start: {video_beats['start']}. Motion: {video_beats['motion']}. End: {video_beats['end']}. "
            f"Transition style: {template['transition_style']}."
        ),
        "pika_prompt": (
            f"{prompt_stack} {continuity_constraints} {style_lock} Camera move: {template['camera_path']}. Subject motion: {template['subject_motion']}. "
            f"Environment: {template['environmental_motion']}. Keep motion {template['motion_intensity']}. "
            f"Start: {video_beats['start']}. Motion: {video_beats['motion']}. End: {video_beats['end']}."
        ),
    }


def build_enhanced_motion_prompt(template: dict) -> str:
    grammar = get_motion_grammar(template["cut_type"])
    acting_layer = template.get("acting_layer_prompt", "")
    return sanitize_static_acting_phrases(
        (
            f"AI video motion prompt for {template['cut_type']} cut. "
            f"{template.get('character_lock_prompt', '')}. "
            f"{template.get('scene_context_prompt', '')}. "
            f"{acting_layer} "
            f"{continuity_memory_text('Continuity constraints', template.get('continuity_constraints', {}))}. "
            f"Acting state lock: action_state={template['action_state']}; emotion_state={template['emotion_state']}; "
            f"motion_state={template['motion_state']}. "
            f"The subject is allowed only this action: {template['allowed_subject_motion']}. "
            f"Forbidden conflicting actions: {', '.join(template['blocked_actions'])}. "
            f"Shot-type grammar: {grammar['strategy']}. "
            f"Grammar movement: {grammar['camera_movement']}. "
            f"Grammar depth: {grammar['depth']}. "
            f"{template['camera_path']}. Subject motion: {template['subject_motion']}. "
            f"Environmental/parallax motion: {template['environmental_motion']}. "
            f"Motion intensity: {template['motion_intensity']}. "
            f"Transition plan: {template['transition_style']}; {template['camera_transition']}; "
            f"{template['spatial_transition']}. "
            f"Continuity lock: {template['continuity_notes']} {grammar['continuity']}. "
            f"{continuity_memory_text('Previous cut memory', template.get('previous_cut_memory', {}))}. "
            f"{continuity_memory_text('Current cut end memory', template.get('continuity_memory', {}))}. "
            f"Preserve screen direction, light direction, weather/atmosphere, subject identity, and depth layering. "
            f"Pacing: {template['cinematic_pacing']} Camera speed: {template['camera_speed']}. "
            f"Avoid {grammar['avoid']}, jump cuts, abrupt speed ramps, warped geometry, flicker, and decorative camera motion."
        )
    )


def default_motion_prompt_for_cut(cut_number: int) -> str:
    defaults = {
        1: "slow push in",
        2: "gentle pan right",
        3: "follow shot",
        4: "close-up drift",
        5: "slow pull back",
    }
    return defaults.get(cut_number, "gentle cinematic camera move")


@app.post("/video-plan", response_model=VideoPlanResponse)
def create_video_plan(request: VideoPlanRequest):
    selected_project, selected_project_dir = resolve_project_dir(request.selected_project)
    selected_project_dirs = project_asset_dirs(selected_project_dir)
    clear_project_storyboard_image_cache(selected_project, selected_project_dir)
    update_project_metadata(selected_project, selected_project_dir, topic=request.topic, duration=request.duration)
    base_duration = request.duration // 5
    extra_seconds = request.duration % 5
    character_name = normalize_active_character_name(request.character_name)
    active_character = load_or_create_character_profile(character_name)
    canonical_reference_path = resolve_reference_image_for_character(character_name)
    use_fixed_character_reference = bool(canonical_reference_path and canonical_reference_path.exists())
    if use_fixed_character_reference:
        print(
            f"[reference] using fixed character reference: {character_name} -> "
            f"{canonical_reference_path.name} ({reference_public_url_for_path(canonical_reference_path)})"
        )
    character_prompt_prefix = f"{active_character.character_prompt} " if active_character else ""
    visual_style_lock = build_visual_style_lock(request.style)
    master_character = MasterCharacter()
    scene_context = build_scene_context(request.topic, request.style)
    continuity_state = build_continuity_state(scene_context, request.topic)
    character_lock_prompt = f"{BPOSIK_STRONG_IDENTITY_LOCK} {character_prompt_prefix}".strip()
    continuity_constraints = dump_model(continuity_state)

    cut_templates = [
        {
            "cut_type": "ESTABLISHING",
            "scene": "영상의 분위기를 여는 도입 장면. '{topic}'의 공간과 시간대가 천천히 드러난다.",
            "narration": "오늘의 이야기는 {topic}에서 시작됩니다.",
            "subtitle": "{topic}",
            "shot_type": "Establishing Wide Shot",
            "lens": "24mm wide angle",
            "movement": "Slow dolly in",
            "lighting": "Soft ambient light with practical highlights",
            "mood_color": "#6f7f8d",
            "prompt": "{style}, 조용한 도입부, 넓은 화면, 부드러운 카메라 이동, 현실적인 조명",
            "motion_prompt": "",
            "start_frame_prompt": "{style}, {topic}, first frame, wide cinematic establishing composition, foreground atmospheric layer, small subject presence, deep background space, realistic film lighting, 16:9, no text",
            "end_frame_prompt": "{style}, {topic}, final frame of the shot, camera closer to the  environment, foreground parallax clearer, subject or entry path established, deep cinematic depth, realistic lighting, 16:9, no text",
            "camera_path": "Slow diagonal dolly-in from wide to medium-wide, steadicam-stable with subtle downward tilt correction and soft inertia at start and stop",
            "environmental_motion": "Rain, haze, curtain movement, practical light flicker, and distant background activity drift at different depth speeds",
            "subject_motion": "Subject res small or enters slowly into the midground, no sudden gesture, movement supports the reveal",
            "motion_intensity": "Low intensity, gradual reveal, avoid fast object motion or aggressive camera drift",
            "transition_style": "Fade in from black into atmospheric establish",
            "continuity_notes": "Preserve weather direction, color temperature, and screen direction for the next detail cut",
            "previous_shot_relation": "Opening shot; establishes geography, mood, and visual grammar before any character-level detail.",
            "next_shot_relation": "Hands off to Cut 2 by pushing from broad spatial context into intimate tactile detail.",
            "emotional_transition": "Neutral atmosphere to quiet anticipation.",
            "camera_transition": "Dolly-in energy resolves into a closer handheld observational drift.",
            "spatial_transition": "Wide environment compresses into a foreground-obscured medium detail plane.",
            "pacing_curve": "Slow rise / exposition",
            "framing": "Wide 16:9 establishing frame with generous negative space",
            "foreground": "Soft architectural edge, rain streak, doorway, window frame, or passing texture for parallax",
            "midground": "Primary subject or entry path placed small within the environment",
            "background": "Deep spatial layer with practical lights, weather, skyline, corridor, or interior depth",
            "camera_height": "Chest to eye level, slightly below neutral for scale",
            "subject_direction": "Subject moves inward or res still while the camera approaches",
            "emotional_intensity": "Quiet anticipation",
            "visual_focus": "Spatial reveal and emotional geography",
            "composition_rule": "Rule of thirds with leading lines into the subject",
            "start_frame_description": "Black resolves into a wide exterior or spatial establishing frame where '{topic}' is still distant and atmospheric.",
            "end_frame_description": "The camera settles closer to the  environment, with foreground depth and the central visual subject clearly established.",
            "motion_strength": "Low",
            "camera_speed": "0.35x slow cinematic drift",
            "transition_duration": "1.2s",
            "cinematic_pacing": "Opening beat with a calm inhale, gradual information reveal, no early visual climax.",
            "transition": "Fade in from black with a soft atmospheric dissolve",
            "sound_design": "Low room tone, distant ambience, soft environmental texture, restrained first cue hit",
            "background_music": "Minimal low piano pad, sparse notes, warm analog texture",
            "pacing": "Slow reveal, 4-6 second hold before the next cut",
            "image_prompt": "{style}, {topic}, establishing shot, cinematic lighting, 16:9 frame",
        },
        {
            "cut_type": "EMOTIONAL",
            "scene": "주요 피사체와 주변 환경을 가까이 보여주며 감정의 방향을 만든다.",
            "narration": "익숙한 풍경 속에서도 작은 감정의 변화가 느껴집니다.",
            "subtitle": "익숙하지만 낯선 순간",
            "shot_type": "Medium Detail Shot",
            "lens": "35mm prime",
            "movement": "Gentle handheld drift",
            "lighting": "Window side light with low contrast shadows",
            "mood_color": "#8a7765",
            "prompt": "{style}, 미디엄 샷, 섬세한 디테일, 차분한 색감, 자연스러운 움직임",
            "motion_prompt": "",
            "start_frame_prompt": "{style}, {topic}, first frame, medium detail composition, foreground partially obscures the subject, tactile texture, shallow depth of field, realistic cinematic lighting, 16:9, no text",
            "end_frame_prompt": "{style}, {topic}, final frame of the shot, emotional detail in clean focus, foreground falls away slightly, background res soft and layered, realistic film still, 16:9, no text",
            "camera_path": "Small handheld lateral drift with a restrained pan across the detail, micro tilt corrections, no abrupt shake",
            "environmental_motion": "Soft room reflections, fabric movement, rain on glass, dust, or ambient background motion kept secondary",
            "subject_motion": "Hands, face, prop, or body detail shifts slightly through the midground to motivate the camera",
            "motion_intensity": "Medium-low intensity, intimate movement, keep motion readable and natural",
            "transition_style": "Match cut on direction or texture from the establishing shot",
            "continuity_notes": "Match the opener's light direction and carry the same weather or ambience into the detail layer",
            "previous_shot_relation": "Receives the established environment from Cut 1 and translates it into a human-scale detail.",
            "next_shot_relation": "Prepares Cut 3 by isolating the emotional trigger that becomes the hero frame.",
            "emotional_transition": "Quiet anticipation to contained vulnerability.",
            "camera_transition": "Wide dolly inertia softens into controlled handheld micro-movement, then steadies for the hero push.",
            "spatial_transition": "Foreground texture gives way to a centered subject plane.",
            "pacing_curve": "Tightening / attention pull",
            "framing": "Medium detail frame with selective headroom and close environmental context",
            "foreground": "Soft practical object, sleeve, table edge, wall texture, or rain-lit glass",
            "midground": "Hands, face, prop, or emotional detail carrying the scene beat",
            "background": "Muted room or street layer with low-contrast movement",
            "camera_height": "Subject eye line or hand level depending on the detail",
            "subject_direction": "Small lateral or inward movement across the frame",
            "emotional_intensity": "Contained vulnerability",
            "visual_focus": "The detail that reveals the emotional shift",
            "composition_rule": "Layered over-the-shoulder or off-center thirds composition",
            "start_frame_description": "Begin on a medium detail frame where the subject or object is partially obscured by foreground texture.",
            "end_frame_description": "End with the important emotional detail in clean focus while the surrounding environment res softly layered.",
            "motion_strength": "Medium-low",
            "camera_speed": "0.45x observational move",
            "transition_duration": "0.5s",
            "cinematic_pacing": "Detail beat that tightens attention, shorter than the opener but still unhurried.",
            "transition": "Match cut on movement from the establishing frame",
            "sound_design": "Close foley details, soft fabric or footsteps, subtle room reflections",
            "background_music": "Piano motif continues with a faint sustained string layer",
            "pacing": "Measured and observational, 3-5 seconds with a soft midpoint emphasis",
            "image_prompt": "{style}, {topic}, medium shot, emotional details, muted colors, cinematic still",
        },
        {
            "cut_type": "REVEAL",
            "scene": "영상의 중심 장면. 주제의 핵심 정서와 상황이 가장 선명하게 표현된다.",
            "narration": "{topic}이라는 장면은 조용하지만 분명한 이야기를 품고 있습니다.",
            "subtitle": "장면이 말을 걸어오는 시간",
            "shot_type": "Hero Cinematic Shot",
            "lens": "50mm prime",
            "movement": "Locked frame with subtle push",
            "lighting": "Focused key light and deep background falloff",
            "mood_color": "#d6a64f",
            "prompt": "{style}, 시네마틱 구도, 감정적인 중심 장면, 깊이감 있는 공간, 고해상도",
            "motion_prompt": "",
            "start_frame_prompt": "{style}, {topic}, first frame, hero cinematic composition, emotionally centered subject, layered foreground and background, focused key light, deep falloff, 16:9, no text",
            "end_frame_prompt": "{style}, {topic}, final frame of the shot, slightly closer hero frame, subject carries stronger emotional weight, background depth compressed gently, dramatic realistic lighting, 16:9, no text",
            "camera_path": "Precision dolly push-in on the optical axis, minimal pan, soft ease-in and ease-out, steadicam weight preserved",
            "environmental_motion": "Atmosphere, practical highlights, and distant background layers move subtly while the subject res dominant",
            "subject_motion": "Subject holds, breathes, turns subtly, or shifts gaze only when emotionally motivated",
            "motion_intensity": "Low physical motion, high emotional intensity, avoid decorative camera moves",
            "transition_style": "Hard clean cut on emotional beat",
            "continuity_notes": "tain subject screen position and emotional eye line from the previous cut",
            "previous_shot_relation": "Receives Cut 2's emotional detail and expands it into the central story beat.",
            "next_shot_relation": "Releases the held emotion into Cut 4's lateral movement and wider scene rhythm.",
            "emotional_transition": "Contained vulnerability to restrained emotional peak.",
            "camera_transition": "Handheld drift locks into a precision dolly push, then exits into lateral tracking energy.",
            "spatial_transition": "Detail plane deepens into a balanced hero composition with readable background falloff.",
            "pacing_curve": "Peak hold / emotional plateau",
            "framing": "Hero medium-wide or medium frame with strong subject dominance",
            "foreground": "Minimal, only a soft edge or light leak if it supports depth",
            "midground": " subject locked as the emotional anchor",
            "background": "Deep falloff with controlled highlights and slow ambient movement",
            "camera_height": "Eye level for empathy and direct emotional access",
            "subject_direction": "Subject holds position or turns subtly toward the emotional source",
            "emotional_intensity": "High but restrained",
            "visual_focus": "Face, posture, or central symbolic object",
            "composition_rule": "Centered hero frame with balanced depth planes",
            "start_frame_description": "Begin on the strongest composed hero frame, with '{topic}' emotionally centered and visual layers already readable.",
            "end_frame_description": "Finish a little closer, with the subject occupying more emotional weight and background depth compressed slightly.",
            "motion_strength": "Low but emotionally focused",
            "camera_speed": "0.25x precision push",
            "transition_duration": "0.2s",
            "cinematic_pacing": "Primary emotional hold, longest sustained beat, allow the audience to read the frame.",
            "transition": "Clean cut timed to the strongest visual beat",
            "sound_design": "Focused presence tone, reduced ambience, one clear emotional accent",
            "background_music": " theme swells gently without overpowering narration",
            "pacing": "Longest emotional hold, 5-7 seconds, let the frame breathe",
            "image_prompt": "{style}, {topic}, hero shot, deep composition, high resolution, dramatic mood",
        },
        {
            "cut_type": "TRANSITION",
            "scene": "카메라가 다른 각도로 전환되며 장면의 리듬과 여운을 확장한다.",
            "narration": "시선이 머무는 곳마다 지나온 시간의 흔적이 남아 있습니다.",
            "subtitle": "천천히 번지는 여운",
            "shot_type": "Profile Tracking Shot",
            "lens": "40mm anamorphic",
            "movement": "Slow lateral pan",
            "lighting": "Streaked practical light with reflective texture",
            "mood_color": "#4f6770",
            "prompt": "{style}, 측면 구도, 느린 패닝, 분위기 있는 조명, 영화 같은 질감",
            "motion_prompt": "",
            "start_frame_prompt": "{style}, {topic}, first frame, profile tracking composition, foreground edge entering frame, subject or environment at the side, long background perspective, cinematic lighting, 16:9, no text",
            "end_frame_prompt": "{style}, {topic}, final frame of the shot, lateral move completes on a new angle, foreground wipe clears, directional line points toward the ending cut, atmospheric depth, 16:9, no text",
            "camera_path": "Smooth lateral steadicam track parallel to subject, slow pan follows movement, slight counter-tilt keeps horizon stable",
            "environmental_motion": "Reflections, haze, practical lights, and foreground surfaces slide at varied speeds for layered parallax",
            "subject_motion": "Subject crosses left-to-right or right-to-left with readable body rhythm and consistent screen direction",
            "motion_intensity": "Medium intensity, flowing bridge motion, no sudden whip pan or speed ramp",
            "transition_style": "Foreground wipe or soft cross-dissolve into the moving angle",
            "continuity_notes": "Carry screen direction from the prior emotional beat and prepare the final shot's retreat direction",
            "previous_shot_relation": "Takes Cut 3's held emotional peak and converts it into directional movement.",
            "next_shot_relation": "Guides Cut 5 by slowing lateral momentum into a final retreat and resolution.",
            "emotional_transition": "Restrained peak to searching momentum.",
            "camera_transition": "Hero push releases into lateral steadicam track, then decelerates toward dolly-back.",
            "spatial_transition": "Centered depth opens into a side-profile travel path with foreground wipe continuity.",
            "pacing_curve": "Release / kinetic bridge",
            "framing": "Profile tracking frame with strong horizontal travel",
            "foreground": "Passing wall, column, reflective surface, or shadow edge for wipe transitions",
            "midground": "Subject crossing frame with readable profile and body rhythm",
            "background": "Long perspective layer with streaked practical lights or reflections",
            "camera_height": "Shoulder height, parallel to the subject path",
            "subject_direction": "Left-to-right or right-to-left movement that carries the edit forward",
            "emotional_intensity": "Searching momentum",
            "visual_focus": "Directional movement and shifting spatial relationship",
            "composition_rule": "Leading lines and lateral negative space ahead of motion",
            "start_frame_description": "Begin from a side angle with the subject or environment entering the frame edge and foreground elements crossing the lens.",
            "end_frame_description": "End after the lateral move reveals a new angle, leaving a clean directional line into the final cut.",
            "motion_strength": "Medium",
            "camera_speed": "0.6x smooth lateral track",
            "transition_duration": "0.7s",
            "cinematic_pacing": "Bridge beat with forward momentum, slightly more kinetic than the hero shot.",
            "transition": "Foreground wipe or soft cross-dissolve into the tracking angle",
            "sound_design": "Directional movement texture, light surface reflections, restrained environmental swell",
            "background_music": "Rhythmic pulse enters quietly under the piano texture",
            "pacing": "Flowing bridge shot, 3-5 seconds, slightly faster than the hero cut",
            "image_prompt": "{style}, {topic}, side angle, atmospheric lighting, film texture, quiet rhythm",
        },
        {
            "cut_type": "ENDING",
            "scene": "마무리 장면. 전체 영상의 감정을 정리하고 잔잔한 결말을 만든다.",
            "narration": "그리고 이 장면은 조용한 여운을 남긴 채 끝을 맺습니다.",
            "subtitle": "남겨진 조용한 여운",
            "shot_type": "Ending Wide Shot",
            "lens": "28mm wide angle",
            "movement": "Slow zoom out",
            "lighting": "Dim backlight with warm edge glow",
            "mood_color": "#2f3a46",
            "prompt": "{style}, 엔딩 샷, 느린 줌아웃, 안정적인 구도, 감성적인 마무리",
            "motion_prompt": "",
            "start_frame_prompt": "{style}, {topic}, first frame, stable final emotional composition, subject or key location settled in midground, warm edge light, deep cinematic space, 16:9, no text",
            "end_frame_prompt": "{style}, {topic}, final frame, wider calmer composition, subject recedes into environment, atmosphere continues softly, clean fade-ready ending frame, realistic film lighting, 16:9, no text",
            "camera_path": "Slow dolly-back from medium-wide to wide, minimal pan, optional tiny upward tilt for breathing room, soft deceleration into final hold",
            "environmental_motion": "Room tone, haze, distant lights, rain, curtains, or exterior atmosphere continue after subject settles",
            "subject_motion": "Subject slows, turns away, or becomes still before the camera retreats",
            "motion_intensity": "Low release, decelerating motion, preserve calm final readability",
            "transition_style": "Slow fade to black after a final hold",
            "continuity_notes": "Resolve previous screen direction and tain color palette while reducing motion energy",
            "previous_shot_relation": "Receives Cut 4's travel direction and resolves it into a wider final composition.",
            "next_shot_relation": "Final shot; no outgoing cut, only fade-out and emotional tail.",
            "emotional_transition": "Searching momentum to soft resolution.",
            "camera_transition": "Lateral tracking energy decelerates into a weighted dolly-back and final hold.",
            "spatial_transition": "Moving profile space expands into a calm wide environment.",
            "pacing_curve": "Decrescendo / resolution tail",
            "framing": "Ending wide frame that gives the subject room to recede",
            "foreground": "Subtle edge element or darkness that frames the exit",
            "midground": "Subject or key location settling into stillness",
            "background": "Wide environmental layer carrying the final emotional afterimage",
            "camera_height": "Neutral eye level drifting slightly higher only if the space needs release",
            "subject_direction": "Subject slows, turns away, or res still as camera retreats",
            "emotional_intensity": "Soft resolution",
            "visual_focus": "The final relationship between subject and space",
            "composition_rule": "Balanced wide composition with receding depth lines",
            "start_frame_description": "Begin on the final emotional composition where the story point has already landed.",
            "end_frame_description": "End wider and calmer, with '{topic}' receding into the environment before the fade-out.",
            "motion_strength": "Low release",
            "camera_speed": "0.3x closing retreat",
            "transition_duration": "1.5s",
            "cinematic_pacing": "Resolution beat with a long tail, decelerate visually into the fade.",
            "transition": "Slow fade to black after the final hold",
            "sound_design": "Ambience opens wider, final foley detail decays naturally, no sharp ending",
            "background_music": "Theme resolves into a warm sustained chord and fades out",
            "pacing": "Gentle release, 4-6 seconds with a final 1-second tail",
            "image_prompt": "{style}, {topic}, ending shot, slow zoom out feeling, cinematic final frame",
        },
    ]

    cuts = []
    previous_memory: dict[str, str] = {}
    inheritance_strength = normalize_continuity_strength(request.continuity_inheritance_strength)
    image_provider = (request.image_provider or "openai").strip().lower()
    provider_log_label = {
        "openai": "OpenAI",
        "replicate": "Replicate",
        "mock": "Mock",
    }.get(image_provider, image_provider)
    print(
        f"[image] active image provider: {provider_log_label}, "
        f"Reference Character: {character_name}"
    )
    storyboard_audit_path = selected_project_dir / "storyboard_image_run.txt"
    storyboard_audit_path.write_text(
        "\n".join(
            [
                f"Reference Character: {character_name}",
                f"Strong Identity Lock: {BPOSIK_STRONG_IDENTITY_LOCK}",
                f"Negative Prompt: {CHARACTER_NEGATIVE_PROMPT}",
                f"active image provider: {provider_log_label}",
                f"started_at: {datetime.now(timezone.utc).isoformat()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    plan_reference_frame: ReferenceFrame | None = None
    reference_character: ReferenceCharacter | None = None
    reference_saved = use_fixed_character_reference
    if use_fixed_character_reference and canonical_reference_path is not None:
        ref_public = reference_public_url_for_path(canonical_reference_path)
        reference_character = ReferenceCharacter(
            path=ref_public,
            source_cut_number=0,
            prompt=build_reference_character_prompt(
                character_name=character_name,
                reference_image_path=canonical_reference_path,
                strength=inheritance_strength,
            ),
        )
        plan_reference_frame = ReferenceFrame(
            stored_path=ref_public,
            public_path=ref_public,
            source_cut_number=0,
            continuity_inheritance_strength=inheritance_strength,
            image_provider=image_provider,
            reference_image_path=str(canonical_reference_path),
            inheritance_applied=True,
        )
    cut_prompt_overrides: dict[int, CutPromptItem] = {}
    if request.cut_prompts:
        for item in request.cut_prompts:
            cut_prompt_overrides[item.cut_number] = item

    for index, template in enumerate(cut_templates, start=1):
        template = apply_acting_state(template, request.topic, request.style, index)
        template = adapt_template_for_scene_context(template, scene_context, request.topic)
        template = adapt_template_for_dog_cafe(template, index, request.topic, request.style)
        cut_override = cut_prompt_overrides.get(index)
        if cut_override:
            if cut_override.scene_description:
                template["scene"] = normalize_cut_scene_for_topic(
                    cut_override.scene_description,
                    index,
                    request.topic,
                )
            if cut_override.narration:
                template["narration"] = cut_override.narration
            if cut_override.subtitle:
                template["subtitle"] = cut_override.subtitle
            if cut_override.emotion:
                template["story_emotion"] = cut_override.emotion
            if cut_override.image_prompt and not topic_implies_dog_cafe(request.topic):
                template["image_prompt"] = cut_override.image_prompt
            if cut_override.cut_type:
                template["cut_type"] = cut_override.cut_type
        for field in ("scene", "narration", "subtitle", "start_frame_description", "end_frame_description"):
            value = template.get(field, "")
            if isinstance(value, str) and ("{topic}" in value or "{style}" in value):
                template[field] = value.format(topic=request.topic, style=request.style)
        if isinstance(template.get("scene"), str):
            template["scene"] = normalize_cut_scene_for_topic(template["scene"], index, request.topic)
        template = apply_style_acting(template, request.style, request.topic, index)
        scene_context_prompt = build_scene_context_prompt(scene_context, request.topic, index)
        template["character_lock_prompt"] = character_lock_prompt
        template["scene_context_prompt"] = scene_context_prompt
        template["continuity_constraints"] = continuity_constraints
        template["previous_cut_memory"] = previous_memory
        template["continuity_memory"] = build_continuity_memory(template, scene_context, previous_memory)
        recommended_duration = base_duration + (1 if index <= extra_seconds else 0)
        fallback_url = f"/static/placeholders/cut-{index}.svg"
        if cut_override and cut_override.image_prompt and not topic_implies_dog_cafe(request.topic):
            scene_image_prompt = cut_override.image_prompt
        elif topic_implies_dog_cafe(request.topic):
            scene_image_prompt = build_dog_cafe_image_scene_prompt(
                style=request.style,
                topic=request.topic,
                cut_number=index,
                action_hint=template.get("scene", request.topic),
            )
        else:
            scene_image_prompt = template["image_prompt"].format(
                style=request.style,
                topic=request.topic,
            )
        cut_scene_prompt = build_cut_scene_prompt(scene_image_prompt, topic=request.topic, cut_number=index)
        camera_prompt = build_camera_prompt(template, visual_style_lock)
        template["cut_scene_prompt"] = cut_scene_prompt
        template["camera_prompt"] = camera_prompt
        reference_section = (
            build_reference_character_section(
                character_name=character_name,
                reference_image_path=canonical_reference_path,
                strength=inheritance_strength,
            )
            if use_fixed_character_reference and canonical_reference_path is not None
            else ""
        )
        template["reference_character_section"] = reference_section
        reference_prefix = (
            build_reference_character_prompt(
                character_name=character_name,
                reference_image_path=canonical_reference_path,
                strength=inheritance_strength,
            )
            if use_fixed_character_reference and canonical_reference_path is not None
            else ""
        )
        cut_story_beat = template.get("cut_story_beat") or build_cut_story_beat(index, template, request.topic)
        acting_layer_prompt = template.get("acting_layer_prompt", "")
        emotion_acting_block = template.get("emotion_acting_block") or build_emotion_acting_block(
            get_refined_cut_acting_beat(
                index,
                scene=template.get("scene", request.topic),
                emotion=template.get("story_emotion") or template.get("emotion_state", ""),
                narration=template.get("narration", ""),
                subtitle=template.get("subtitle", ""),
            ),
            index,
        )
        style_layer = build_style_layer_text(request.style)
        image_prompt = combine_image_prompt(
            reference_character_section=reference_section,
            character_lock=BPOSIK_STRONG_IDENTITY_LOCK,
            scene_context_prompt=scene_context_prompt,
            cut_story_beat=cut_story_beat,
            acting_layer_prompt=acting_layer_prompt,
            emotion_acting_block=emotion_acting_block,
            style_layer=style_layer,
            cut_scene_prompt=cut_scene_prompt,
            camera_prompt=camera_prompt,
            cut_number=index,
            negative_prompt=build_negative_prompt_for_cut(index, request.topic),
            topic=request.topic,
        )
        start_frame_prompt = template["start_frame_prompt"].format(
            style=request.style,
            topic=request.topic,
        )
        end_frame_prompt = template["end_frame_prompt"].format(
            style=request.style,
            topic=request.topic,
        )
        platform_prompts = create_platform_prompts(
            style=request.style,
            topic=request.topic,
            template=template,
            start_frame_prompt=start_frame_prompt,
            end_frame_prompt=end_frame_prompt,
            visual_style_lock=visual_style_lock,
            cut_number=index,
        )
        motion_prompt = template.get("motion_prompt") or build_motion_prompt_with_acting(
            template,
            request.style,
            request.topic,
            index,
        )
        motion_grammar = get_motion_grammar(template["cut_type"])
        reference_file_path = (
            canonical_reference_path
            if use_fixed_character_reference and canonical_reference_path is not None
            else (CURRENT_REFERENCE_FILE if reference_saved and CURRENT_REFERENCE_FILE.exists() else None)
        )
        previous_cut_path = selected_project_dirs["images"] / f"cut_{index - 1}.png"
        use_reference_inheritance = bool(reference_file_path and reference_file_path.exists())
        effective_image_provider = image_provider
        if effective_image_provider == "replicate" and not use_reference_inheritance:
            effective_image_provider = "openai"
        template["image_provider"] = effective_image_provider
        log_acting_prompt_audit(
            index,
            template,
            request.style,
            request.topic,
            image_provider=effective_image_provider,
            character_name=character_name,
        )

        image_url, image_status, image_error, reference_frame_meta = generate_image_for_cut(
            cut_number=index,
            prompt=image_prompt,
            fallback_url=fallback_url,
            image_provider=effective_image_provider,
            continuity_inheritance_strength=inheritance_strength,
            reference_image_path=reference_file_path,
            previous_cut_image_path=previous_cut_path if previous_cut_path.exists() else None,
            use_reference_inheritance=use_reference_inheritance,
            output_path=selected_project_dirs["images"] / f"cut_{index}.png",
        )
        if image_status == "generated":
            normalized_image_url = image_url.split("?", 1)[0]
            image_path = resolve_local_image_path(normalized_image_url)
            update_project_metadata(
                selected_project,
                selected_project_dir,
                topic=request.topic,
                image={
                    "cut_number": index,
                    "path": str(image_path) if image_path else normalized_image_url,
                    "url": normalized_image_url,
                    "description": template["scene"].format(topic=request.topic),
                    "provider": effective_image_provider,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        if index == 1 and image_status == "generated" and not use_fixed_character_reference:
            saved_reference, save_error = save_reference_character_from_image_url(
                image_url,
                source_cut_number=1,
            )
            reference_character = ReferenceCharacter(
                path=saved_reference.path,
                source_cut_number=saved_reference.source_cut_number,
                prompt=build_reference_character_prompt(
                    source_cut_number=saved_reference.source_cut_number,
                    strength=inheritance_strength,
                    character_name=character_name,
                    reference_image_path=canonical_reference_path,
                ),
            )
            reference_saved = save_error is None and CURRENT_REFERENCE_FILE.exists()
            if save_error:
                image_error = f"{image_error or ''} Reference save warning: {save_error}".strip()
            if reference_saved:
                plan_reference_frame = ReferenceFrame(
                    stored_path=REFERENCE_CHARACTER_PUBLIC_PATH,
                    public_path=REFERENCE_CHARACTER_PUBLIC_PATH,
                    source_cut_number=1,
                    continuity_inheritance_strength=inheritance_strength,
                    image_provider=image_provider,
                    reference_image_path=str(CURRENT_REFERENCE_FILE),
                    inheritance_applied=True,
                )
        if use_fixed_character_reference and canonical_reference_path is not None:
            reference_path = reference_public_url_for_path(canonical_reference_path)
            reference_prompt_for_cut = reference_prefix
        else:
            reference_path = reference_character.path if reference_character else REFERENCE_CHARACTER_PUBLIC_PATH
            reference_prompt_for_cut = reference_prefix if index >= 2 and reference_saved else ""
        previous_cut_public = (
            project_url_for_path(previous_cut_path) if previous_cut_path.exists() else ""
        )
        cuts.append(
            CutPlan(
                cut_number=index,
                cut_type=template["cut_type"],
                visual_style_lock=visual_style_lock,
                action_state=template["action_state"],
                emotion_state=template["emotion_state"],
                motion_state=template["motion_state"],
                allowed_subject_motion=template["allowed_subject_motion"],
                blocked_actions=template["blocked_actions"],
                conflict_warnings=template["conflict_warnings"],
                character_lock_prompt=character_lock_prompt,
                scene_context_prompt=scene_context_prompt,
                reference_character_path=reference_path,
                reference_character_prompt=reference_prompt_for_cut,
                previous_cut_image_path=previous_cut_public,
                reference_frame_path=reference_path if use_reference_inheritance else "",
                continuity_inheritance_strength=inheritance_strength,
                reference_frame_applied=bool(reference_frame_meta.get("inheritance_applied")),
                reference_frame=reference_frame_meta,
                continuity_constraints=continuity_constraints,
                active_character=active_character.character_name if active_character else "",
                character_summary=active_character.character_summary if active_character else "",
                character_prompt=active_character.character_prompt if active_character else "",
                scene_description=(
                    cut_override.scene_description
                    if cut_override and cut_override.scene_description
                    else template["scene"].format(topic=request.topic)
                ),
                narration=(
                    cut_override.narration
                    if cut_override and cut_override.narration
                    else template["narration"].format(topic=request.topic)
                ),
                subtitle=(
                    cut_override.subtitle
                    if cut_override and cut_override.subtitle
                    else template["subtitle"].format(topic=request.topic)
                ),
                shot_type=template["shot_type"],
                lens=template["lens"],
                movement=template["movement"],
                lighting=template["lighting"],
                mood_color=template["mood_color"],
                video_prompt=(
                    f"{BPOSIK_REFERENCE_CHARACTER}. {BPOSIK_IDENTITY_LOCK} {scene_context_prompt} "
                    f"{cut_story_beat} {acting_layer_prompt} "
                    f"{template['prompt'].format(style=request.style, topic=request.topic)}"
                ),
                motion_prompt=motion_prompt,
                motion_grammar=motion_grammar,
                start_frame_prompt=start_frame_prompt,
                end_frame_prompt=end_frame_prompt,
                camera_path=template["camera_path"],
                environmental_motion=template["environmental_motion"],
                subject_motion=template["subject_motion"],
                motion_intensity=template["motion_intensity"],
                transition_style=template["transition_style"],
                continuity_notes=template["continuity_notes"],
                previous_cut_memory=template["previous_cut_memory"],
                continuity_memory=template["continuity_memory"],
                runway_prompt=platform_prompts["runway_prompt"],
                kling_prompt=platform_prompts["kling_prompt"],
                veo_prompt=platform_prompts["veo_prompt"],
                pika_prompt=platform_prompts["pika_prompt"],
                previous_shot_relation=template["previous_shot_relation"],
                next_shot_relation=template["next_shot_relation"],
                emotional_transition=template["emotional_transition"],
                camera_transition=template["camera_transition"],
                spatial_transition=template["spatial_transition"],
                pacing_curve=template["pacing_curve"],
                framing=template["framing"],
                foreground=template["foreground"],
                midground=template["midground"],
                background=template["background"],
                camera_height=template["camera_height"],
                subject_direction=template["subject_direction"],
                emotional_intensity=template["emotional_intensity"],
                visual_focus=template["visual_focus"],
                composition_rule=template["composition_rule"],
                start_frame_description=template["start_frame_description"].format(topic=request.topic),
                end_frame_description=template["end_frame_description"].format(topic=request.topic),
                motion_strength=template["motion_strength"],
                camera_speed=template["camera_speed"],
                transition_duration=template["transition_duration"],
                cinematic_pacing=template["cinematic_pacing"],
                transition=template["transition"],
                sound_design=template["sound_design"],
                background_music=template["background_music"],
                pacing=template["pacing"],
                image_prompt=image_prompt,
                acting_layer_prompt=acting_layer_prompt,
                cut_story_beat=cut_story_beat,
                image_url=image_url,
                image_status=image_status,
                image_error=image_error,
                sample_image_url=fallback_url,
                recommended_duration=recommended_duration,
            )
        )
        previous_memory = template["continuity_memory"]

    storyboard_cuts = [
        {
            "cut": cut.cut_number,
            "title": f"CUT {cut.cut_number}",
            "description": cut.scene_description,
            "motion_prompt": cut.motion_prompt,
            "provider_prompts": {
                "replicate": cut.motion_prompt,
                "runway": cut.runway_prompt,
                "veo": cut.veo_prompt,
                "kling": cut.kling_prompt,
            },
        }
        for cut in cuts
    ]
    write_json(
        project_storyboard_path(selected_project_dir),
        build_project_storyboard_payload(request.topic, request.style, storyboard_cuts),
    )
    metadata = load_project_metadata(selected_project, selected_project_dir)
    metadata["motion_prompts"] = [
        {"cut": item["cut"], "motion_prompt": item["motion_prompt"]}
        for item in storyboard_cuts
    ]
    metadata["topic"] = request.topic
    metadata["style"] = request.style
    metadata["duration"] = request.duration
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project_json_path(selected_project_dir), metadata)

    if reference_character is None:
        reference_character = ReferenceCharacter(
            path=REFERENCE_CHARACTER_PUBLIC_PATH,
            source_cut_number=1,
            prompt=build_reference_character_prompt(source_cut_number=1, strength=inheritance_strength),
        )

    if plan_reference_frame is None:
        plan_reference_frame = ReferenceFrame(
            stored_path=REFERENCE_CHARACTER_PUBLIC_PATH,
            public_path=REFERENCE_CHARACTER_PUBLIC_PATH,
            source_cut_number=reference_character.source_cut_number,
            continuity_inheritance_strength=inheritance_strength,
            image_provider=image_provider,
            reference_image_path=str(CURRENT_REFERENCE_FILE) if CURRENT_REFERENCE_FILE.exists() else None,
            inheritance_applied=reference_saved,
        )

    plan_response = VideoPlanResponse(
        topic=request.topic,
        style=request.style,
        duration=request.duration,
        master_character=master_character,
        scene_context=scene_context,
        reference_character=reference_character,
        reference_frame=plan_reference_frame,
        continuity_state=continuity_state,
        active_character=active_character,
        cuts=cuts,
    )
    write_json(project_plan_path(selected_project_dir), dump_model(plan_response))
    ensure_current_run(
        selected_project_dir,
        [
            cut.cut_number
            for cut in plan_response.cuts
            if default_motion_enabled_for_cut(cut.cut_number)
        ],
        target_duration=request.duration,
        reset_if_changed=True,
    )
    return plan_response


@app.post("/set-reference-character", response_model=SetReferenceCharacterResponse)
def set_reference_character(request: SetReferenceCharacterRequest):
    reference_character, error = save_reference_character_from_image_url(
        request.image_url,
        source_cut_number=request.cut_number,
    )
    if error:
        raise HTTPException(status_code=400, detail=error)

    return SetReferenceCharacterResponse(
        reference_character=reference_character,
        reference_character_path=reference_character.path,
        message=f"CUT {request.cut_number} image saved as reference character.",
    )


@app.post("/generate-narration", response_model=GenerateNarrationResponse)
def generate_narration(request: AudioPipelineJobRequest):
    if not request.cuts:
        raise HTTPException(status_code=400, detail="cuts가 비어 있습니다. storyboard narration 텍스트를 전달해 주세요.")

    output_dir = ensure_job_output_layout(request.job_id)
    audio_pipeline = load_or_init_audio_pipeline(request.job_id)

    narration_generator = NarrationGenerator(output_dir / "audio" / "narration")
    narration_results = narration_generator.generate_for_cuts(to_narration_cut_inputs(request.cuts))

    bgm_generator = BgmGenerator(output_dir / "audio" / "bgm")
    bgm_result = bgm_generator.generate(request.topic, request.style)

    audio_pipeline["narration"] = {
        "status": "mock_completed",
        "provider": narration_generator.provider,
        "cuts": [
            {
                "cut_number": item.cut_number,
                "status": item.status,
                "text": item.text,
                "mp3_path": item.mp3_path,
                "public_url": item.public_url,
                "duration_seconds": item.duration_seconds,
            }
            for item in narration_results
        ],
    }
    audio_pipeline["bgm"] = {
        "status": bgm_result.status,
        "provider": bgm_result.provider,
        "mood": {
            "label": bgm_result.mood.mood,
            "tempo": bgm_result.mood.tempo,
            "instrumentation": bgm_result.mood.instrumentation,
            "energy": bgm_result.mood.energy,
            "style_tags": bgm_result.mood.style_tags,
            "rationale": bgm_result.mood.rationale,
        },
        "path": bgm_result.mp3_path,
        "url": bgm_result.public_url,
    }
    render_manifest_url = sync_audio_pipeline_to_render_manifest(request.job_id, audio_pipeline)

    return GenerateNarrationResponse(
        job_id=request.job_id,
        status="mock_completed",
        output_dir=str(output_dir),
        output_url=generated_output_url_for_path(output_dir),
        narration=audio_pipeline["narration"],
        bgm=audio_pipeline["bgm"],
        audio_pipeline=audio_pipeline,
        render_manifest_url=render_manifest_url,
        message="Mock narration mp3 paths and BGM mood recommendation were generated.",
    )


@app.post("/generate-subtitles", response_model=GenerateSubtitlesResponse)
def generate_subtitles(request: AudioPipelineJobRequest):
    if not request.cuts:
        raise HTTPException(status_code=400, detail="cuts가 비어 있습니다. narration/subtitle 텍스트를 전달해 주세요.")

    output_dir = ensure_job_output_layout(request.job_id)
    audio_pipeline = load_or_init_audio_pipeline(request.job_id)

    subtitle_generator = SubtitleGenerator(output_dir / "audio" / "subtitles")
    subtitle_bundle = subtitle_generator.generate_bundle(to_subtitle_cut_inputs(request.cuts))

    audio_pipeline["subtitles"] = {
        "status": subtitle_bundle.status,
        "provider": subtitle_bundle.provider,
        "master_srt_path": subtitle_bundle.master_srt_path,
        "master_srt_url": subtitle_bundle.master_srt_url,
        "cuts": [
            {
                "cut_number": item.cut_number,
                "status": item.status,
                "srt_path": item.srt_path,
                "public_url": item.public_url,
                "cue_count": item.cue_count,
            }
            for item in subtitle_bundle.cuts
        ],
    }
    render_manifest_url = sync_audio_pipeline_to_render_manifest(request.job_id, audio_pipeline)

    return GenerateSubtitlesResponse(
        job_id=request.job_id,
        status=subtitle_bundle.status,
        subtitles=audio_pipeline["subtitles"],
        audio_pipeline=audio_pipeline,
        render_manifest_url=render_manifest_url,
        message="Mock SRT subtitles were generated from narration text.",
    )


@app.post("/assemble-final-video", response_model=AssembleFinalVideoResponse)
def assemble_final_video(request: AudioPipelineJobRequest):
    selected_project, selected_project_dir = resolve_project_dir(request.selected_project)
    export_payload = generate_project_final_export(selected_project, selected_project_dir)
    audio_pipeline = load_or_init_audio_pipeline(request.job_id)

    audio_pipeline["final_export"] = {
        "status": "completed",
        "mode": "project_ffmpeg",
        "path": export_payload["output_file"],
        "url": export_payload["output_url"],
        "project_path": export_payload["output_file"],
        "project_url": export_payload["output_url"],
        "ffmpeg_command": export_payload.get("ffmpeg_command", ""),
        "ffmpeg_log_url": export_payload.get("ffmpeg_log_url"),
        "narration_track_path": export_payload.get("narration_track_file"),
        "narration_track_url": export_payload.get("narration_track_url"),
        "export_logs": export_payload.get("export_logs") or [],
        "probe_summary": export_payload.get("probe_summary") or {},
        "message": export_payload.get("message", ""),
        "inputs": {
            "video": "final/full_video.mp4",
            "audio_dir": "audio/*.mp3",
            "audio_count": export_payload.get("final_export", {}).get("audio_count", 0),
            "subtitle": "subtitles/subtitle.srt",
            "output": "exports/final_export.mp4",
            "narration_track": "exports/narration_track.mp3",
        },
    }
    render_manifest_url = sync_audio_pipeline_to_render_manifest(request.job_id, audio_pipeline)

    return AssembleFinalVideoResponse(
        job_id=request.job_id,
        status="completed",
        final_export=audio_pipeline["final_export"],
        audio_pipeline=audio_pipeline,
        render_manifest_url=render_manifest_url,
        message=export_payload.get("message", "Final export completed."),
    )
