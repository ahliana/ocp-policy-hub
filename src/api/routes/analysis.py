"""Single URL analysis endpoint — full pipeline on one URL."""

import os

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import get_config, get_policy_store, request_is_admin
from ...core.config import ConfigLoader
from ...core.crawler import AsyncCrawler
from ...core.extractor import HtmlExtractor
from ...core.keywords import build_keyword_matcher
from ...core.llm import ClaudeClient, LLMError
from ...core.log_setup import log_audit_event
from ...core.models import AnalyzeRequest
from ...core.verifier import Verifier
from ...storage.store import PolicyStore

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/analyze")
async def analyze_url(
    request: AnalyzeRequest,
    config: ConfigLoader = Depends(get_config),
    store: PolicyStore = Depends(get_policy_store),
):
    """Analyze a single URL through the full pipeline."""
    return await run_url_analysis(request.url, config, store)


async def run_url_analysis(
    url: str, config: ConfigLoader, store: PolicyStore,
) -> dict:
    """Full fetch->extract->keywords->LLM pipeline for one URL.

    Shared by the /analyze endpoint and lead chasing.
    """
    settings = config.settings

    # 1. Fetch
    crawler = AsyncCrawler(
        max_depth=0,
        max_pages=1,
        delay_seconds=0.5,
        timeout_seconds=settings.crawl.timeout_seconds,
        user_agent=settings.crawl.user_agent,
    )

    try:
        results = await crawler.crawl_domain(
            base_url=url,
            start_paths=[""],
            domain_id="single_analysis",
        )
    finally:
        await crawler.close()

    if not results or not results[0].is_success:
        status = results[0].status.value if results else "fetch_failed"
        error = results[0].error_message if results else "No response"
        return {
            "url": url,
            "crawl_status": status,
            "error": error,
        }

    result = results[0]

    # 2. Extract
    extractor = HtmlExtractor(settings.config_dir)
    extracted = extractor.extract(result.content, url)

    # 3. Keywords
    keyword_matcher = build_keyword_matcher(config, str(store.data_dir))
    kw_result = keyword_matcher.match(extracted.text)
    kw_relevant = keyword_matcher.is_relevant(kw_result, url=url)

    response = {
        "url": url,
        "title": extracted.title,
        "language": extracted.language,
        "word_count": extracted.word_count,
        "crawl_status": result.status.value,
        "keyword_score": kw_result.score + kw_result.url_bonus,
        "keyword_matches": [m.model_dump() for m in kw_result.matches],
        "categories_matched": kw_result.categories_matched,
        "passes_keyword_threshold": kw_relevant,
    }

    # 4. LLM analysis (if API key available)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key and kw_relevant:
        llm = ClaudeClient(
            api_key=api_key,
            analysis_model=settings.analysis.analysis_model,
            screening_model=settings.analysis.screening_model,
        )
        try:
            screening = await llm.screen_relevance(extracted.text, url)
            response["screening"] = {
                "relevant": screening.relevant,
                "confidence": screening.confidence,
            }

            if screening.relevant:
                analysis = await llm.analyze_policy(
                    extracted.text, url, extracted.language,
                )
                found = llm.to_policies(
                    analysis, url,
                    language=extracted.language or "en",
                )

                if found:
                    # Verify
                    verifier = Verifier()
                    for policy in found:
                        policy.verification_flags = verifier.verify(policy)

                    added = store.add_policies(found)

                    primary = found[0]
                    response["policy"] = primary.model_dump(mode="json")
                    if len(found) > 1:
                        response["additional_policies"] = [
                            p.model_dump(mode="json") for p in found[1:]
                        ]
                    response["verification_flags"] = [
                        f.value for f in primary.verification_flags
                    ]
                    response["saved"] = added > 0
                    response["saved_to"] = str(store.policies_file)

                    if added:
                        for policy in found:
                            log_audit_event(
                                data_dir=str(store.data_dir),
                                event="policy_found",
                                scan_id=policy.scan_id,
                                domain_id=policy.domain_id,
                                policy_name=policy.policy_name,
                                url=policy.url,
                                relevance=policy.relevance_score,
                                source="api_analyze",
                            )
                else:
                    response["policy"] = None

                response["analysis"] = {
                    "is_relevant": analysis.is_relevant,
                    "relevance_score": analysis.relevance_score,
                    "policy_type": analysis.policy_type,
                }
        except LLMError as e:
            response["llm_error"] = str(e)
        finally:
            await llm.close()
    elif not api_key:
        response["llm_note"] = "No ANTHROPIC_API_KEY set — LLM analysis skipped"

    return response


# Secret-bearing fields on OutputSettings that must never leave the server,
# even to an authenticated admin, through this diagnostic endpoint.
_SECRET_SETTING_FIELDS = ("google_credentials_b64",)


@router.get("/config/keywords")
def get_keywords_config(request: Request, config: ConfigLoader = Depends(get_config)):
    """Get keywords configuration (admin-only)."""
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")
    return config.keywords_config


@router.get("/config/settings")
def get_settings(request: Request, config: ConfigLoader = Depends(get_config)):
    """Get application settings (admin-only, secrets redacted).

    Admin-gated like every other config-exposing route; additionally, the
    Google service-account credentials blob is stripped unconditionally so a
    secret can never flow out through this diagnostic endpoint again.
    """
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")
    settings = config.settings.model_dump()
    output = settings.get("output")
    if isinstance(output, dict):
        for field in _SECRET_SETTING_FIELDS:
            output.pop(field, None)
    return settings
