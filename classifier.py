"""
Hedgeye Content Classifier
Uses Claude API to classify email/scrape content and extract structured signals.
"""

import os
import json
import logging
import anthropic

from corpus_rag import fetch_and_format

log = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """You are a financial research analyst assistant specializing in Hedgeye Risk Management content.
Your job is to classify and extract structured data from Hedgeye research content.

You must respond with ONLY valid JSON — no preamble, no markdown, no backticks.

Classify the content into one of these types:
- "trade_signal"       : Contains explicit long/short trade recommendations with tickers
- "market_situation"   : Macro market commentary, gamma exposure, vol analysis, systematic flows
- "sector_research"    : Deep dive on a specific sector (Financials, Retail, Energy, etc.)
- "risk_manager"       : Keith's Risk Manager notes, quad framework, macro regime calls
- "morning_brief"      : Daily morning newsletter or briefing
- "podcast_summary"    : Hedgeye TV or podcast transcript/summary
- "other"              : Anything that doesn't fit above categories

Return this exact JSON structure:
{
  "classified_type": "<type from list above>",
  "summary": "<2-3 sentence plain English summary of key takeaway>",
  "macro_regime": "<Quad 1/2/3/4 or null if not mentioned>",
  "market_tone": "<bullish/bearish/neutral/mixed or null>",
  "spx_levels": {
    "support": <number or null>,
    "resistance": <number or null>
  },
  "vol_regime": "<long_gamma/short_gamma/neutral or null>",
  "systematic_flow": "<positive/negative/neutral or null>",
  "tickers": [
    {
      "ticker": "<SYMBOL>",
      "direction": "<Long/Short>",
      "conviction": "<Best Idea/Adding/Monitor/Reducing/Remove>",
      "sector": "<sector name>",
      "asset_class": "<Equity/ETF/Options/Macro/Fixed Income/Commodity>",
      "thesis": "<1 sentence thesis or null>"
    }
  ],
  "key_levels": [
    {"instrument": "<name>", "level": <number>, "type": "<support/resistance/target/stop>"}
  ],
  "action_required": <true if contains trade signals or urgent calls, false otherwise>,
  "author": "<author name if identified, else null>",
  "tags": ["<relevant topic tags>"]
}

If a field is not present in the content, use null for scalars and [] for arrays.
For tickers[], only include if there are EXPLICIT ticker mentions with directional calls.
Do not invent tickers that aren't in the content.

If the user message includes a <corpus_context> block, that's background reference
material from prior Hedgeye Macro Show notes, VolStudies course material, SpotGamma
reports, and Hedgeye University lessons. Use it ONLY to:
  - Disambiguate quad regime / vol regime references in the email being classified
  - Recognize sector themes and Hedgeye-specific terminology
  - Inform the `tags[]` and `summary` fields with relevant prior context
NEVER pull tickers, directions, or convictions from the corpus. Those come from
the email content only. The corpus is background, not source data."""


def classify_and_extract(item: dict) -> dict:
    """
    Pass a raw scraped/email item through Claude for classification and extraction.
    Returns the item dict enriched with structured fields.

    GATED OFF BY DEFAULT (operator decision 2026-07-11): the per-email LLM
    classify path had been dead for a while (retired model 404'd silently);
    fixing the model string would have revived per-email spend the operator
    chose not to resume. Behavior with the gate closed matches the status
    quo: classified_type='other', no API call, dedicated Python parsers own
    the pipeline. Revive anytime with CLASSIFIER_ENABLED=1 on Railway
    (model via CLASSIFIER_MODEL, default claude-sonnet-5).
    """
    if os.environ.get("CLASSIFIER_ENABLED", "0") != "1":
        item["classified_type"] = "other"
        log.debug("classifier gated off (CLASSIFIER_ENABLED != 1)")
        return item

    # Build content string from available fields
    content_parts = []
    if item.get("title"):
        content_parts.append(f"TITLE: {item['title']}")
    if item.get("subject"):
        content_parts.append(f"SUBJECT: {item['subject']}")
    if item.get("full_content"):
        content_parts.append(f"FULL CONTENT:\n{item['full_content'][:6000]}")
    elif item.get("body"):
        content_parts.append(f"BODY:\n{item['body'][:3000]}")

    if not content_parts:
        log.warning("Item has no extractable content, skipping classification.")
        item["classified_type"] = "other"
        return item

    content_str = "\n\n".join(content_parts)

    # Pull corpus context based on tickers/themes mentioned in the email.
    # Failure here is non-fatal — we proceed without context if anything goes wrong.
    corpus_block = ""
    try:
        # Use subject + first ~2000 chars of content for term extraction.
        seed_text = "\n".join([
            item.get("subject") or "",
            item.get("title") or "",
            content_str[:2000],
        ])
        corpus_block, terms_used, hit_count = fetch_and_format(
            seed_text, limit=4, snippet_chars=500, max_prompt_chars=3000,
        )
        if corpus_block:
            log.info(f"Corpus RAG: {hit_count} hits for terms={terms_used}")
    except Exception as e:
        log.warning(f"Corpus RAG fetch failed (proceeding without context): {e}")
        corpus_block = ""

    user_message = f"Classify and extract from this Hedgeye content:\n\n{content_str}"
    if corpus_block:
        user_message = (
            "Background reference material (do not extract tickers/convictions from this — "
            "use it only for regime, theme, and terminology context):\n\n"
            f"{corpus_block}\n\n"
            "─────────────────────────────\n\n"
            f"Now classify and extract from this Hedgeye content:\n\n{content_str}"
        )

    try:
        response = client.messages.create(
            # claude-sonnet-4-20250514 RETIRED (404'd live 2026-07-11 via the
            # OCR path using the same string — email classification was
            # silently broken). Env-overridable without a deploy.
            model=os.environ.get("CLASSIFIER_MODEL", "claude-sonnet-5"),
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )

        raw = response.content[0].text.strip()

        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        extracted = json.loads(raw)

        # Merge extracted fields into item
        item.update(extracted)

        # Convenience top-level fields for easy access
        if extracted.get("tickers"):
            first = extracted["tickers"][0]
            item["ticker"]    = first.get("ticker")
            item["direction"] = first.get("direction", "Long")
            item["conviction"]= first.get("conviction")
            item["sector"]    = first.get("sector")

        log.info(f"Classified as '{extracted.get('classified_type')}' | action={extracted.get('action_required')}")

    except json.JSONDecodeError as e:
        log.error(f"JSON parse error from Claude: {e}")
        item["classified_type"] = "other"
    except anthropic.APIError as e:
        log.error(f"Anthropic API error: {e}")
        item["classified_type"] = "other"
    except Exception as e:
        log.error(f"Classification error: {e}")
        item["classified_type"] = "other"

    return item
