"""Two-stage Claude LLM client: Haiku screening + Sonnet analysis."""

import asyncio
import json
import logging
from datetime import date
from typing import Optional

import anthropic
from pydantic import ValidationError

from .. import aispend
from .models import (
    Policy, PolicyType, PolicyAnalysis, ScreeningResult, CostInfo,
    DEFAULT_ANALYSIS_MODEL, DEFAULT_SCREENING_MODEL,
)
from .pricing import PricingLoader

logger = logging.getLogger(__name__)

# --- Prompts ---

SCREENING_PROMPT = """You are a RECALL-FIRST relevance screener. Goal: never discard a page
that could plausibly AFFECT data center waste-heat reuse, even indirectly.
When in doubt, keep it (relevant=true with lower confidence).

Mark relevant=true if the page is, or references, government policy touching ANY of:
- Data center waste heat reuse/recovery, energy efficiency, or reporting requirements
- District heating / heat networks: expansion plans, connection mandates, feed-in
  tariffs, or waste-heat feed-in rules - WHETHER OR NOT data centers are named
- Energy efficiency directives or laws with any heat-recovery or waste-heat article
  (e.g. EU EED, Article 26, national transpositions like EnEfG)
- Building or construction codes requiring heat recovery or waste-heat use
- Tax incentives, exemptions, or grants for waste heat, heat recovery, or heat networks
- Cost-benefit analysis requirements for waste heat utilization
- Grid, utility, or heat-network regulation, tariffs, or third-party access rules
- Planning, zoning, or permitting rules for data centers, large energy users, or
  heat sources that mention heat, cooling, or energy reuse
- Index or listing pages that LINK to any of the above

This is a broad net on purpose: a page need not mention data centers to be relevant.
Content may be in any language (EN, DE, FR, SV, DA, NO, FI, IS, NL, PL, JA, KO, etc.).

URL: {url}

CONTENT (excerpt):
{content}

RESPOND WITH JSON ONLY (no explanation):
{{"relevant": true/false, "confidence": 1-10}}
"""

ANALYSIS_PROMPT = """
Analyze this government web page for data center heat reuse policy information.

URL: {url}
Language: {language}

CONTENT:
{content}

TASK:
1. Determine if this describes a policy related to:
   - Data center waste heat / heat reuse
   - Energy efficiency requirements for data centers
   - District heating with data centers
   - Heat recovery mandates or incentives
   - Energy performance reporting requirements for data centers
   - Cost-benefit analysis requirements for waste heat utilization
   - Tax incentives or exemptions for heat recovery or district heating
   - Energy efficiency directives applicable to data centers (e.g. EU EED Article 26)

   The content may be in any language. Look for policy substance regardless of language.

2. If relevant, extract EVERY distinct policy the page describes.
   Listing/index pages often contain several. Put the most significant
   policy in the top-level fields and each further one in
   additional_policies. For each policy:
   - Policy name/title (in original language if not English). If the page
     is relevant but states no clear title, write a short descriptive
     label (e.g. "Dutch waste heat feed-in regulation") - never leave the
     name empty for a relevant policy.
   - Policy name translated to English (policy_name_en). When the original
     name is already English, repeat it exactly.
   - Jurisdiction (country/region)
   - Type (law/regulation/directive/incentive/grant/plan)
   - Brief summary (2-3 sentences). Write the summary in English,
     regardless of the page's language.
   - Effective date (if stated)
   - Key requirements

3. Rate relevance 1-10:
   - 1-3: Not relevant
   - 4-6: Tangentially relevant
   - 7-8: Relevant
   - 9-10: Highly relevant (specifically about data center heat reuse)

4. Extract referenced legislation:
   - List any bill numbers, law names, directive references, or related policies mentioned
   - List any URLs linking to other relevant policy documents

5. Determine the lifecycle stage of the policy:
   - proposed (draft bill or announced intention), consultation (open for
     public comment), in_committee, passed (adopted but not in force),
     enacted (in force), amended, or unknown
   - Documents that are drafts, bills, or consultations are valuable EARLY
     signals; identify them as such rather than defaulting to enacted

RESPOND WITH JSON ONLY:
{{
    "is_relevant": true/false,
    "relevance_score": 1-10,
    "relevance_explanation": "Brief explanation",
    "policy_name": "Name or null",
    "policy_name_en": "Name translated to English (repeat if already English)",
    "jurisdiction": "Country/region or null",
    "policy_type": "law|regulation|directive|incentive|grant|plan|unknown",
    "summary": "2-3 sentences, in English, or null",
    "effective_date": "YYYY-MM-DD or null",
    "key_requirements": "Key points or null",
    "bill_number": "Number or null",
    "lifecycle_stage": "proposed|consultation|in_committee|passed|enacted|amended|unknown",
    "referenced_policies": ["Related law/directive names or empty list"],
    "referenced_urls": ["URLs to related policy documents or empty list"],
    "additional_policies": [
        {{
            "is_relevant": true,
            "relevance_score": 1-10,
            "policy_name": "Name (never empty)",
            "policy_name_en": "Name translated to English (repeat if already English)",
            "jurisdiction": "Country/region",
            "policy_type": "law|regulation|directive|incentive|grant|plan|unknown",
            "summary": "2-3 sentences",
            "effective_date": "YYYY-MM-DD or null",
            "key_requirements": "Key points or null",
            "bill_number": "Number or null"
        }}
    ]
}}
"""


# Screening excerpt sizing: head window plus an anchor window so that long
# documents whose relevant article sits past the head still get screened on it.
SCREENING_HEAD_CHARS = 8000
SCREENING_ANCHOR_WINDOW = 2000
SCREENING_MAX_CHARS = 12500


def screening_excerpt(content: str, anchor_terms: list[str] | None) -> str:
    """Build the text window the screening model sees.

    Head-only truncation loses statutes whose heat article appears late in
    the document. If any anchor term (matched keyword) first occurs beyond
    the head window, append a window of text around that occurrence.
    """
    if len(content) <= SCREENING_HEAD_CHARS:
        return content

    excerpt = content[:SCREENING_HEAD_CHARS]
    lowered = content.lower()
    for term in anchor_terms or []:
        pos = lowered.find(term.lower())
        if pos > SCREENING_HEAD_CHARS:
            start = max(0, pos - SCREENING_ANCHOR_WINDOW // 2)
            end = min(len(content), pos + SCREENING_ANCHOR_WINDOW)
            excerpt = excerpt + "\n[...]\n" + content[start:end]
            break

    return excerpt[:SCREENING_MAX_CHARS]


# --- Errors ---

class LLMError(Exception):
    def __init__(self, message: str, url: str = "", recoverable: bool = True):
        super().__init__(message)
        self.url = url
        self.recoverable = recoverable


class LLMAuthError(LLMError):
    def __init__(self, message: str):
        super().__init__(message, recoverable=False)


class LLMRateLimitError(LLMError):
    pass


class LLMParseError(LLMError):
    def __init__(self, message: str, raw_response: str = "", url: str = ""):
        super().__init__(message, url=url)
        self.raw_response = raw_response


class LLMServiceError(LLMError):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


# --- Helpers ---

_NULL_VALUES = (
    None, "null", "None", "N/A", "n/a", "", "unknown", "Unknown",
    "nicht verfügbar", "non disponible", "不明", "não disponível",
)


def _extract_json(text: str) -> str:
    """Extract JSON from LLM response, handling code blocks and raw JSON."""
    if "```json" in text:
        try:
            return text.split("```json")[1].split("```")[0].strip()
        except IndexError:
            pass
    if "```" in text:
        try:
            return text.split("```")[1].split("```")[0].strip()
        except IndexError:
            pass
    text = text.strip()
    if text.startswith("{"):
        depth = 0
        for i, char in enumerate(text):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[:i + 1]
    return text


def _coerce_types(data: dict) -> dict:
    """Coerce common type mismatches from LLM responses."""
    result = data.copy()

    # is_relevant → bool
    if "is_relevant" in result:
        val = result["is_relevant"]
        if isinstance(val, str):
            result["is_relevant"] = val.lower() in (
                "true", "yes", "1", "ja", "oui", "sí", "是", "да", "sim"
            )
        elif isinstance(val, (int, float)):
            result["is_relevant"] = bool(val)

    # relevance_score → int 0-10
    if "relevance_score" in result:
        raw_val = result["relevance_score"]
        val = raw_val
        if isinstance(val, str):
            try:
                val = val.split("/")[0].split(" ")[0].strip()
                result["relevance_score"] = int(float(val))
            except (ValueError, IndexError):
                logger.warning(
                    "LLM returned unparseable relevance_score=%r - defaulting to 0. "
                    "This may cause the policy to be ranked lower than expected.",
                    raw_val,
                )
                result["relevance_score"] = 0
        elif isinstance(val, float):
            result["relevance_score"] = int(val)
        if isinstance(result["relevance_score"], int):
            result["relevance_score"] = max(0, min(10, result["relevance_score"]))

    # Normalize null-like values
    # Optional[str] fields get None; required str fields get ""
    _OPTIONAL_FIELDS = {"effective_date", "bill_number", "policy_name_en"}
    for key in ["policy_name", "policy_name_en", "jurisdiction", "summary",
                "effective_date", "key_requirements", "bill_number",
                "relevance_explanation"]:
        if key in result and result[key] in _NULL_VALUES:
            if key == "relevance_explanation":
                result[key] = "No explanation provided"
            elif key in _OPTIONAL_FIELDS:
                result[key] = None
            else:
                result[key] = ""  # required str fields can't be None

    if "relevance_explanation" not in result or not result["relevance_explanation"]:
        result["relevance_explanation"] = "No explanation provided"

    # policy_type default
    if not result.get("policy_type") or result["policy_type"] in _NULL_VALUES:
        result["policy_type"] = "not_relevant" if not result.get("is_relevant") else "unknown"

    # lifecycle_stage: constrain to the known vocabulary
    from .models import LIFECYCLE_STAGES
    stage = result.get("lifecycle_stage")
    if not isinstance(stage, str) or stage not in LIFECYCLE_STAGES:
        result["lifecycle_stage"] = "unknown"

    # Normalize list fields (referenced_policies, referenced_urls)
    for list_key in ("referenced_policies", "referenced_urls"):
        val = result.get(list_key)
        if val is None or val in _NULL_VALUES:
            result[list_key] = []
        elif isinstance(val, str):
            result[list_key] = [val] if val else []
        elif isinstance(val, list):
            result[list_key] = [item for item in val if item and item not in _NULL_VALUES]

    # Coerce each nested additional policy through the same rules
    extras = result.get("additional_policies")
    if not isinstance(extras, list):
        result["additional_policies"] = []
    else:
        result["additional_policies"] = [
            _coerce_types(item) for item in extras if isinstance(item, dict)
        ]

    return result


# --- Model validation ---


def _resolve_model(
    client: anthropic.Anthropic,
    model_id: str,
    role: str,
    family: str,
) -> str:
    """Validate a model exists; if not, find the newest alternative in the same family.

    Args:
        client: Sync Anthropic client (for the models API).
        model_id: Configured model ID to validate.
        role: Human-readable role ("screening" or "analysis") for log messages.
        family: Model family substring to match (e.g. "haiku", "sonnet").

    Returns:
        The validated model ID, or a fallback if the original is unavailable.
    """
    try:
        client.models.retrieve(model_id=model_id)
        return model_id
    except anthropic.NotFoundError:
        logger.warning(
            "%s model '%s' is no longer available -- searching for a "
            "%s-family alternative...",
            role.capitalize(), model_id, family,
        )
    except Exception:
        # Network/auth errors - don't block startup, assume model is fine
        return model_id

    # Model not found - try to find the newest model in the same family
    try:
        available = list(client.models.list(limit=100))
        candidates = [m for m in available if family in m.id]
        if candidates:
            # Newest model first (by created_at timestamp)
            candidates.sort(
                key=lambda m: getattr(m, "created_at", "") or "",
                reverse=True,
            )
            replacement = candidates[0].id
            logger.warning(
                "Auto-resolved %s model: '%s' -> '%s'. "
                "Update %s_MODEL in .env to make this permanent.",
                role, model_id, replacement, role.upper(),
            )
            return replacement
    except Exception as exc:
        logger.warning("Could not list available models: %s", exc)

    logger.error(
        "No %s-family model found. Update %s_MODEL in your .env. "
        "See: https://docs.anthropic.com/en/docs/about-claude/models",
        family, role.upper(),
    )
    return model_id  # Return original - will fail at first actual use


# --- Client ---

class ClaudeClient:
    """Async Claude API client with two-stage analysis.

    Rate limit handling: Both screening and analysis retry on 429 errors
    using the API's retry-after header when available, falling back to
    exponential backoff.  BASE_DELAY is set generously because Anthropic
    rate limit windows are typically 60-120 seconds.
    """

    MAX_RETRIES = 3
    BASE_DELAY = 10.0   # generous fallback - API retry-after is usually 60-120s
    MAX_DELAY = 120.0   # cap matches typical API retry-after values
    MAX_CONTENT_CHARS = 45000

    def __init__(
        self,
        api_key: str,
        analysis_model: str = DEFAULT_ANALYSIS_MODEL,
        screening_model: str = DEFAULT_SCREENING_MODEL,
    ):
        self._api_key = api_key
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.analysis_model = analysis_model
        self.screening_model = screening_model
        self.cost = CostInfo()
        self._pricing = PricingLoader()
        self._validate_models()

    # ------------------------------------------------------------------
    # Startup model validation
    # ------------------------------------------------------------------

    def _validate_models(self) -> None:
        """Check that configured models exist, auto-resolving if stale."""
        try:
            sync_client = anthropic.Anthropic(api_key=self._api_key)
        except Exception:
            return  # Can't create sync client - skip validation

        self.screening_model = _resolve_model(
            sync_client, self.screening_model, "screening", "haiku",
        )
        self.analysis_model = _resolve_model(
            sync_client, self.analysis_model, "analysis", "sonnet",
        )

    async def screen_relevance(
        self, content: str, url: str, anchor_terms: list[str] | None = None,
    ) -> ScreeningResult:
        """Quick relevance screening using Haiku.

        Retries on rate limits to avoid failing open unnecessarily - if
        screening fails open, the page goes to expensive Sonnet analysis,
        which worsens rate limit pressure and costs.  Only falls open on
        non-retryable errors (connection issues, parse failures).

        The confidence gate (drop only when the model is confident the page
        is irrelevant) is applied by the caller - see DomainScanner.

        Args:
            content: Full page text.
            url: Source URL (for logging and prompt context).
            anchor_terms: Matched keywords; if one first occurs beyond the
                head window, the excerpt includes text around it so long
                statutes are not screened on their preamble alone.

        Returns:
            ScreeningResult with relevant=True/False and confidence 1-10.
        """
        screening_content = screening_excerpt(content, anchor_terms)
        prompt = SCREENING_PROMPT.format(url=url, content=screening_content)
        delay = self.BASE_DELAY

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                import time
                _t0 = time.monotonic()
                response = await aispend.acreate(
                    self.client, label="core:llm",
                    model=self.screening_model,
                    max_tokens=50,
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}],
                )
                _latency_ms = int((time.monotonic() - _t0) * 1000)
                self.cost.screening_calls += 1
                if hasattr(response, "usage"):
                    self.cost.input_tokens += response.usage.input_tokens
                    self.cost.output_tokens += response.usage.output_tokens
                    self.cost.screening_input_tokens += response.usage.input_tokens
                    self.cost.screening_output_tokens += response.usage.output_tokens
                    logger.info(
                        "llm_call: screening model=%s url=%s "
                        "input_tokens=%d output_tokens=%d latency_ms=%d",
                        self.screening_model, url,
                        response.usage.input_tokens,
                        response.usage.output_tokens,
                        _latency_ms,
                    )

                raw = response.content[0].text
                try:
                    data = json.loads(_extract_json(raw))
                except json.JSONDecodeError:
                    logger.warning(f"Screening parse failed for {url}, assuming relevant")
                    return ScreeningResult(relevant=True, confidence=5)

                relevant = data.get("relevant", True)
                if isinstance(relevant, str):
                    relevant = relevant.lower() in ("true", "yes", "1")
                confidence = data.get("confidence", 5)
                if isinstance(confidence, str):
                    try:
                        confidence = int(confidence)
                    except ValueError:
                        confidence = 5
                confidence = max(1, min(10, confidence))

                return ScreeningResult(relevant=relevant, confidence=confidence)

            except anthropic.AuthenticationError as e:
                raise LLMAuthError(f"Authentication failed: {e}") from e

            except anthropic.RateLimitError as e:
                # Retry screening on rate limit - falling open here would
                # send ALL pages to expensive Sonnet analysis, making the
                # rate limit cascade worse.
                if attempt < self.MAX_RETRIES:
                    retry_after = delay
                    try:
                        if hasattr(e, "response") and e.response:
                            retry_after = float(
                                e.response.headers.get("retry-after", delay)
                            )
                    except (ValueError, AttributeError):
                        pass
                    logger.warning(
                        f"Screening rate limited for {url}, "
                        f"waiting {retry_after:.1f}s (attempt {attempt}/{self.MAX_RETRIES})"
                    )
                    await asyncio.sleep(retry_after)
                    delay = min(delay * 2, self.MAX_DELAY)
                else:
                    # Exhausted retries - fail open as last resort
                    logger.warning(
                        f"Screening rate limit exhausted for {url}, assuming relevant"
                    )
                    return ScreeningResult(relevant=True, confidence=5)

            except anthropic.NotFoundError:
                # Model doesn't exist - log ONCE and disable screening
                if not getattr(self, "_screening_model_warned", False):
                    logger.error(
                        f"Screening model '{self.screening_model}' not found (404). "
                        f"All pages will bypass screening and go directly to analysis. "
                        f"Fix: update 'screening_model' in config/settings.yaml to a valid model."
                    )
                    self._screening_model_warned = True
                return ScreeningResult(relevant=True, confidence=5)

            except Exception as e:
                # Fail open: any non-retryable error → assume relevant
                logger.warning(f"Screening error for {url}: {e}, assuming relevant")
                return ScreeningResult(relevant=True, confidence=5)

        # Should never reach here, but fail open for safety
        return ScreeningResult(relevant=True, confidence=5)

    async def analyze_policy(
        self, content: str, url: str, language: Optional[str] = None,
    ) -> PolicyAnalysis:
        """Full policy analysis with Sonnet. Retries on transient errors."""
        if len(content) > self.MAX_CONTENT_CHARS:
            content = content[:self.MAX_CONTENT_CHARS]

        last_error = None
        delay = self.BASE_DELAY

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                return await self._call_analysis(content, url, language)

            except anthropic.AuthenticationError as e:
                raise LLMAuthError(f"Authentication failed: {e}") from e

            except anthropic.RateLimitError as e:
                if attempt < self.MAX_RETRIES:
                    retry_after = delay
                    try:
                        if hasattr(e, "response") and e.response:
                            retry_after = float(e.response.headers.get("retry-after", delay))
                    except (ValueError, AttributeError):
                        pass
                    logger.warning(f"Rate limited for {url}, waiting {retry_after:.1f}s")
                    await asyncio.sleep(retry_after)
                    delay = min(delay * 2, self.MAX_DELAY)
                else:
                    raise LLMRateLimitError(f"Rate limit after {self.MAX_RETRIES} retries") from e

            except anthropic.BadRequestError as e:
                error_msg = str(e).lower()
                if any(w in error_msg for w in ("context", "token", "length")):
                    if len(content) > 10000:
                        content = content[:10000]
                        logger.warning(f"Context too long for {url}, retrying truncated")
                        continue
                raise LLMError(f"Bad request: {e}", url=url) from e

            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                if attempt < self.MAX_RETRIES:
                    logger.warning(f"Connection error for {url}, retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self.MAX_DELAY)
                    last_error = e
                else:
                    raise LLMServiceError(f"Connection failed after retries: {e}") from e

            except (anthropic.InternalServerError, anthropic.APIStatusError) as e:
                if attempt < self.MAX_RETRIES:
                    logger.warning(f"Service error for {url}, retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self.MAX_DELAY)
                    last_error = e
                else:
                    raise LLMServiceError(f"Service error after retries: {e}") from e

            except LLMParseError:
                raise

        if last_error:
            raise LLMServiceError(f"Failed after {self.MAX_RETRIES} retries") from last_error
        raise LLMError("Failed for unknown reason", url=url)

    async def _call_analysis(
        self, content: str, url: str, language: Optional[str],
    ) -> PolicyAnalysis:
        """Make the actual analysis API call."""
        import time

        prompt = ANALYSIS_PROMPT.format(
            url=url, language=language or "Unknown", content=content,
        )
        _t0 = time.monotonic()
        response = await aispend.acreate(
            self.client, label="core:llm",
            model=self.analysis_model,
            max_tokens=1024,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        _latency_ms = int((time.monotonic() - _t0) * 1000)
        self.cost.analysis_calls += 1
        if hasattr(response, "usage"):
            self.cost.input_tokens += response.usage.input_tokens
            self.cost.output_tokens += response.usage.output_tokens
            self.cost.analysis_input_tokens += response.usage.input_tokens
            self.cost.analysis_output_tokens += response.usage.output_tokens
            logger.info(
                "llm_call: analysis model=%s url=%s "
                "input_tokens=%d output_tokens=%d latency_ms=%d",
                self.analysis_model, url,
                response.usage.input_tokens,
                response.usage.output_tokens,
                _latency_ms,
            )

        raw = response.content[0].text
        try:
            data = json.loads(_extract_json(raw))
        except json.JSONDecodeError as e:
            raise LLMParseError(f"Invalid JSON: {e}", raw_response=raw[:1000], url=url)

        data = _coerce_types(data)

        try:
            return PolicyAnalysis(**{
                k: v for k, v in data.items()
                if k in PolicyAnalysis.model_fields
            })
        except ValidationError as e:
            raise LLMParseError(f"Validation failed: {e}", raw_response=str(data)[:1000], url=url)

    def to_policy(
        self, analysis: PolicyAnalysis, url: str, language: str,
        domain_id: str = "", scan_id: str = "",
    ) -> Optional[Policy]:
        """Convert a single PolicyAnalysis to a Policy model.

        A relevant policy without a stated title gets a synthesized
        descriptive name instead of being dropped.
        """
        if not analysis.is_relevant:
            return None

        policy_name = analysis.policy_name
        if not policy_name:
            jurisdiction = analysis.jurisdiction or "Unknown jurisdiction"
            kind = analysis.policy_type if analysis.policy_type not in (
                "", "unknown", "not_relevant",
            ) else "policy"
            policy_name = f"Untitled {kind} ({jurisdiction})"

        effective_date = None
        if analysis.effective_date:
            try:
                effective_date = date.fromisoformat(analysis.effective_date)
            except ValueError:
                pass

        try:
            policy_type = PolicyType(analysis.policy_type)
        except ValueError:
            policy_type = PolicyType.UNKNOWN

        return Policy(
            url=url,
            policy_name=policy_name,
            policy_name_en=analysis.policy_name_en,
            jurisdiction=analysis.jurisdiction or "Unknown",
            policy_type=policy_type,
            summary=analysis.summary or "",
            relevance_score=analysis.relevance_score,
            effective_date=effective_date,
            source_language=language,
            key_requirements=analysis.key_requirements,
            domain_id=domain_id,
            scan_id=scan_id,
            referenced_policies=analysis.referenced_policies,
            referenced_urls=analysis.referenced_urls,
            lifecycle_stage=analysis.lifecycle_stage,
        )

    def to_policies(
        self, analysis: PolicyAnalysis, url: str, language: str,
        domain_id: str = "", scan_id: str = "",
    ) -> list[Policy]:
        """Convert an analysis (primary + additional policies) to Policy models.

        Index and listing pages describe several policies; all of them
        share the page URL as their source.
        """
        policies = []
        primary = self.to_policy(analysis, url, language, domain_id, scan_id)
        if primary:
            policies.append(primary)
        for extra in analysis.additional_policies:
            policy = self.to_policy(extra, url, language, domain_id, scan_id)
            if policy:
                policies.append(policy)
        return policies

    def update_cost_estimate(self):
        """Recompute USD cost exactly from per-stage token usage (WP-22).

        Screening tokens are priced at ``self.screening_model``, analysis
        tokens at ``self.analysis_model`` - each from the WP-19 pricing
        table. No call-count-fraction blend over a shared token pool: the
        two stages use different models with a 3x price gap, so blending by
        call count (rather than tracking each stage's tokens separately)
        used to misprice every scan that mixed both stages.
        """
        screening_price = self._pricing.pricing_for(self.screening_model)
        analysis_price = self._pricing.pricing_for(self.analysis_model)

        screening_cost = screening_price.cost_usd(
            self.cost.screening_input_tokens, self.cost.screening_output_tokens,
        )
        analysis_cost = analysis_price.cost_usd(
            self.cost.analysis_input_tokens, self.cost.analysis_output_tokens,
        )

        self.cost.total_usd = round(screening_cost + analysis_cost, 4)

    async def close(self):
        await self.client.close()
