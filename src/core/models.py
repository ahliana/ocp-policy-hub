"""Data models for the OCP CE HR Policy Searcher."""

from datetime import datetime, date
from enum import Enum
from typing import Optional, Any, Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Model IDs - single source of truth for Claude model defaults.
#
# Users override via .env (SCREENING_MODEL, ANALYSIS_MODEL) or settings.yaml.
# All other modules import these constants instead of hardcoding model strings.
# Use undated aliases (e.g. "claude-sonnet-4-6") where available so that the
# API automatically resolves to the latest patch version.
# ---------------------------------------------------------------------------
DEFAULT_ANALYSIS_MODEL = "claude-sonnet-4-6"
DEFAULT_SCREENING_MODEL = "claude-haiku-4-5-20251001"


# --- Enums ---

class PolicyType(str, Enum):
    LAW = "law"
    REGULATION = "regulation"
    DIRECTIVE = "directive"
    INCENTIVE = "incentive"
    TAX_INCENTIVE = "tax_incentive"
    GRANT = "grant"
    PLAN = "plan"
    REQUIREMENT = "requirement"
    STANDARD = "standard"
    GUIDANCE = "guidance"
    MATCHING_PLATFORM = "matching_platform"
    UNKNOWN = "unknown"


class PageStatus(str, Enum):
    SUCCESS = "success"
    PAYWALL_DETECTED = "paywall"
    CAPTCHA_DETECTED = "captcha"
    LOGIN_REQUIRED = "login_required"
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    RATE_LIMITED = "rate_limited"
    JS_REQUIRED = "js_required"
    UNKNOWN_ERROR = "unknown_error"


class ScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DomainScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class VerificationFlag(str, Enum):
    JURISDICTION_MISMATCH = "jurisdiction_mismatch"
    FUTURE_DATE = "future_date"
    GENERIC_NAME = "generic_name"
    DUPLICATE_URL = "duplicate_url"
    LOW_CONFIDENCE_HIGH_SCORE = "low_confidence_high_score"


# --- Domain Configuration ---

class DomainConfig(BaseModel):
    id: str
    name: str
    base_url: str
    enabled: bool = True
    region: list[str] = Field(default_factory=list)
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    policy_types: list[str] = Field(default_factory=list)
    start_paths: list[str] = Field(default_factory=lambda: ["/"])
    max_depth: Optional[int] = None
    max_pages: Optional[int] = None
    requires_playwright: bool = False
    min_keyword_score: Optional[float] = None
    allowed_path_patterns: list[str] = Field(default_factory=list)
    blocked_path_patterns: list[str] = Field(default_factory=list)


# --- Crawl Results ---

class CrawlResult(BaseModel):
    url: str
    status: PageStatus
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    content: Optional[str] = None
    content_type: Optional[str] = None
    title: Optional[str] = None
    language: Optional[str] = None
    response_time_ms: Optional[int] = None
    content_length: Optional[int] = None
    error_message: Optional[str] = None
    requires_human_review: bool = False
    used_playwright: bool = False
    domain_id: Optional[str] = None
    # Set by structured sources that know the document's stage (bill
    # status, open consultation); overrides analysis inference.
    lifecycle_stage: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.status == PageStatus.SUCCESS

    @property
    def is_blocked(self) -> bool:
        return self.status in {
            PageStatus.PAYWALL_DETECTED,
            PageStatus.CAPTCHA_DETECTED,
            PageStatus.LOGIN_REQUIRED,
            PageStatus.ACCESS_DENIED,
        }


class ExtractedContent(BaseModel):
    text: str
    title: Optional[str] = None
    language: Optional[str] = None
    word_count: int = 0


# --- Keyword Results ---

class KeywordMatch(BaseModel):
    term: str
    category: str
    weight: float
    language: str


class KeywordResult(BaseModel):
    score: float
    matches: list[KeywordMatch] = Field(default_factory=list)
    categories_matched: list[str] = Field(default_factory=list)
    url_bonus: float = 0.0
    passes_threshold: bool = False
    is_near_miss: bool = False
    is_excluded: bool = False


# --- LLM Results ---

class ScreeningResult(BaseModel):
    relevant: bool
    confidence: int = 5
    error: Optional[str] = None


class PolicyAnalysis(BaseModel):
    is_relevant: bool = False
    relevance_score: int = 0
    policy_type: str = "unknown"
    policy_name: str = ""
    # English translation of policy_name (WP-35). Optional/defaults to None
    # so older LLM responses and fixtures that predate this field still
    # parse without a validation error.
    policy_name_en: Optional[str] = None
    jurisdiction: str = ""
    summary: str = ""
    key_requirements: str = ""
    effective_date: Optional[str] = None
    source_language: str = "English"
    confidence: int = 5
    referenced_policies: list[str] = Field(default_factory=list)
    referenced_urls: list[str] = Field(default_factory=list)
    lifecycle_stage: str = "unknown"
    # Index/listing pages describe several policies; the primary one uses
    # the fields above, the rest arrive here.
    additional_policies: list["PolicyAnalysis"] = Field(default_factory=list)


# --- Policy (final output) ---

# Where a policy sits in its life: sources set this when they know it
# (bill status, consultation window); otherwise analysis infers it.
LIFECYCLE_STAGES = (
    "proposed", "consultation", "in_committee", "passed",
    "enacted", "transposition_notified", "amended", "unknown",
)


class Policy(BaseModel):
    url: str
    policy_name: str
    # English translation of policy_name (WP-35); None when not yet
    # translated (see src/output/backfill_english.py) or when policy_name
    # was already English.
    policy_name_en: Optional[str] = None
    jurisdiction: str
    policy_type: PolicyType
    summary: str
    relevance_score: int
    effective_date: Optional[date] = None
    source_language: str = "English"
    bill_number: Optional[str] = None
    key_requirements: Optional[str] = None
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    crawl_status: str = "success"
    error_details: Optional[str] = None
    review_status: str = "new"
    scan_id: Optional[str] = None
    domain_id: Optional[str] = None
    verification_flags: list[VerificationFlag] = Field(default_factory=list)
    referenced_policies: list[str] = Field(default_factory=list)
    referenced_urls: list[str] = Field(default_factory=list)
    lifecycle_stage: str = "unknown"

    @staticmethod
    def sheet_headers() -> list[str]:
        """Staging headers, aligned with the Heat Reuse Policies Database tab."""
        from .policy_schema import STAGING_HEADERS
        return list(STAGING_HEADERS)

    def to_sheet_row(self) -> list:
        """Serialize into a Staging row matching sheet_headers()."""
        from .policy_schema import to_staging_row
        return to_staging_row(self)


# --- Scan Events (WebSocket) ---

class ScanEvent(BaseModel):
    scan_id: str
    type: Literal[
        "scan_started",
        "domain_started",
        "page_fetched",
        "keyword_match",
        "policy_found",
        "domain_complete",
        "verification_complete",
        "audit_complete",
        "scan_complete",
        "error",
    ]
    domain_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# --- Scan Job ---

class DomainProgress(BaseModel):
    domain_id: str
    domain_name: str
    status: DomainScanStatus = DomainScanStatus.PENDING
    pages_crawled: int = 0
    pages_filtered: int = 0
    # Rejection breakdown: pages_filtered alone lumps every drop reason
    # into one opaque number, hiding recall loss.
    filtered_short_content: int = 0
    filtered_excluded: int = 0
    filtered_keywords: int = 0
    filtered_screening: int = 0
    near_misses: int = 0
    keywords_matched: int = 0
    policies_found: int = 0
    llm_skipped: int = 0
    errors: int = 0
    error_message: Optional[str] = None


class ScanProgress(BaseModel):
    total_domains: int = 0
    completed_domains: int = 0
    running_domains: int = 0
    domains: list[DomainProgress] = Field(default_factory=list)


class CostInfo(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    screening_calls: int = 0
    analysis_calls: int = 0
    # Per-stage token counters (WP-22) - input_tokens/output_tokens above
    # remain the maintained totals (screening + analysis + auditor) so the
    # scans table and any other consumer of the aggregate keeps working;
    # these let update_cost_estimate() price each stage at its own model
    # instead of blending by call-count fraction over a shared pool.
    screening_input_tokens: int = 0
    screening_output_tokens: int = 0
    analysis_input_tokens: int = 0
    analysis_output_tokens: int = 0
    total_usd: float = 0.0


class SheetsExportStatus(BaseModel):
    """Tracks Google Sheets export state throughout a scan."""
    configured: bool = False           # Were credentials + spreadsheet_id provided?
    connected: bool = False            # Did initial connection succeed?
    exported_count: int = 0            # Policies successfully written to Sheets
    failed_count: int = 0              # Policies that failed to export
    error: Optional[str] = None        # Last error message (if any)
    status: str = "not_configured"     # not_configured | connected | failed | skipped


class ScanJob(BaseModel):
    scan_id: str
    status: ScanStatus = ScanStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    domain_group: str = ""
    domain_count: int = 0
    policy_count: int = 0
    progress: ScanProgress = Field(default_factory=ScanProgress)
    cost: CostInfo = Field(default_factory=CostInfo)
    audit_advisory: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)
    sheets_export: SheetsExportStatus = Field(default_factory=SheetsExportStatus)
    # Set once running cost reaches the scan's budget_usd cap (WP-22b);
    # the scan still completes normally with whatever domains finished.
    budget_reached: bool = False


# --- API Request/Response Schemas ---

# Channels a scan can be scoped to. "news" has its own runner (not
# scan_manager) and never appears as a domain's classified channel.
VALID_SCAN_CHANNELS = {"crawl", "law_apis", "transposition", "news"}


class ScanRequest(BaseModel):
    domains: str = "quick"
    max_concurrent: int = Field(default=5, ge=1, le=20)
    skip_llm: bool = False
    dry_run: bool = False
    deep: bool = False
    discover: bool = False
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    policy_type: Optional[str] = None
    channels: list[str] = Field(default_factory=lambda: ["crawl"])
    # Per-request overrides for structured sources (e.g. {"state": "CA",
    # "terms": [...]} from a place-first search). Crawl domains ignore this.
    source_params: Optional[dict] = None
    # Mid-scan budget stop (WP-22b). None = no cap (default, unchanged
    # behavior). Schedules pass their remaining monthly ceiling here so a
    # scan stops launching further domains once running cost reaches it.
    budget_usd: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_scan_mode(self) -> "ScanRequest":
        if self.deep and self.discover:
            raise ValueError("Choose one scan mode: standard, deep, or discover")
        return self

    @model_validator(mode="after")
    def validate_channels(self) -> "ScanRequest":
        invalid = sorted(set(self.channels) - VALID_SCAN_CHANNELS)
        if invalid:
            raise ValueError(
                f"Invalid channel(s): {invalid}. "
                f"Valid values: {sorted(VALID_SCAN_CHANNELS)}"
            )
        return self


class AnalyzeRequest(BaseModel):
    url: str


class CostEstimateRequest(BaseModel):
    domains: str


class CostEstimate(BaseModel):
    domain_count: int
    estimated_pages: int
    estimated_keyword_passes: int
    estimated_screening_calls: int
    estimated_analysis_calls: int
    estimated_cost_usd: float


class PolicyStats(BaseModel):
    total: int = 0
    by_jurisdiction: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    by_score_range: dict[str, int] = Field(default_factory=dict)
    flagged_count: int = 0


# --- Settings ---

class CrawlSettings(BaseModel):
    max_depth: int = Field(default=3, ge=1, le=10)
    max_pages_per_domain: int = Field(default=200, ge=1)
    delay_seconds: float = Field(default=3.0, ge=0.5)
    timeout_seconds: int = Field(default=30, ge=5)
    max_concurrent: int = Field(default=3, ge=1, le=10)
    user_agent: str = "OCP-PolicyHub/1.0"
    respect_robots_txt: bool = True
    max_retries: int = 3
    force_playwright: bool = False


class AnalysisSettings(BaseModel):
    min_keyword_score: float = Field(default=3.0, ge=0)
    min_relevance_score: int = Field(default=5, ge=1, le=10)
    min_keyword_matches: int = Field(default=2, ge=1)
    enable_llm_analysis: bool = True
    analysis_model: str = DEFAULT_ANALYSIS_MODEL
    screening_model: str = DEFAULT_SCREENING_MODEL
    max_content_length: int = Field(default=50000)
    enable_two_stage: bool = True
    screening_min_confidence: int = Field(default=5, ge=1, le=10)


class OutputSettings(BaseModel):
    spreadsheet_id: Optional[str] = None
    staging_sheet_name: str = "Staging"
    google_credentials_b64: Optional[str] = None


class AppSettings(BaseModel):
    crawl: CrawlSettings = Field(default_factory=CrawlSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    anthropic_api_key: Optional[str] = None
    config_dir: str = "config"
    data_dir: str = "data"
    max_concurrent_scans: int = 5
