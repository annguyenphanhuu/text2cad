"""
Language utility functions for multilingual support.

Architecture:
  - detect_language_llm()  : async, uses gpt-4.1-nano (most accurate, session-aware)
  - detect_language()      : sync fallback (accent regex → langdetect → domain prior)
  - _SESSION_LANG_CACHE    : {session_id → lang_code}, populated on first substantive message
  - _MESSAGES              : single catalog for all user-facing strings (no hardcoded dicts)

Design principles:
  1. Detect ONCE per session (first substantive message), cache forever.
  2. Short/ambiguous messages ("ok", "yes", "go") → always use cached session lang.
  3. gpt-4.1-nano is preferred: it understands mixed technical text and short phrases.
  4. All public get_*_message() functions receive a lang CODE, not user_text.
"""
import re
import asyncio
import logging
from typing import Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

logger = logging.getLogger(__name__)

# ── French accent pre-filter ──────────────────────────────────────────────────
# Characters unambiguously French in this domain context.
# Used as a zero-cost fast path before LLM or langdetect.
_FRENCH_ACCENT_PATTERN = re.compile(r'[àâäéèêëîïôùûüçœæ]', re.IGNORECASE)

# ── Domain default ────────────────────────────────────────────────────────────
# Fallback when detection fails or text is too short/ambiguous.
# 80%+ of users are French-speaking.
_DOMAIN_DEFAULT_LANG = "fr"

# ── Supported languages ───────────────────────────────────────────────────────
# Maps ISO 639-1 codes → full language name used in LLM prompts.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "fr": "French",
    "en": "English",
    "es": "Spanish",
    "de": "German",
    "vi": "Vietnamese",
    "zh-cn": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}

# ── Session language cache ────────────────────────────────────────────────────
# Populated by detect_language_llm() on the first substantive message.
# All subsequent calls for the same session_id return the cached value instantly.
# Key: session_id (str) → Value: ISO 639-1 code (str)
_SESSION_LANG_CACHE: dict[str, str] = {}

# ── Per-session async locks ────────────────────────────────────────────────────
# Prevents double LLM calls when two concurrent requests hit the same session
# for the very first time (cache miss race condition).
# Key: session_id (str) → asyncio.Lock
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def _get_session_lock(session_id: str) -> asyncio.Lock:
    """Get-or-create an asyncio.Lock for the given session_id."""
    if session_id not in _SESSION_LOCKS:
        _SESSION_LOCKS[session_id] = asyncio.Lock()
    return _SESSION_LOCKS[session_id]


# ═════════════════════════════════════════════════════════════════════════════
# Session cache API
# ═════════════════════════════════════════════════════════════════════════════

def set_session_language(session_id: str, lang: str) -> None:
    """
    Store the detected language for a session.
    Called by detect_language_llm() and by TextToCADAgent after detection.
    """
    if session_id and lang:
        _SESSION_LANG_CACHE[session_id] = lang
        logger.debug(f"[LANG_CACHE] session={session_id} → lang={lang}")


def get_session_language(session_id: Optional[str] = None) -> str:
    """
    Return the cached language for a session.
    Falls back to domain default ("fr") if session_id is unknown.
    """
    if session_id and session_id in _SESSION_LANG_CACHE:
        return _SESSION_LANG_CACHE[session_id]
    return _DOMAIN_DEFAULT_LANG


# ═════════════════════════════════════════════════════════════════════════════
# Core detection functions
# ═════════════════════════════════════════════════════════════════════════════

async def detect_language_llm(
    text: str,
    llm,
    session_id: Optional[str] = None,
) -> str:
    """
    Detect language using gpt-4.1-nano (preferred path).

    Fully async — safe for multi-user concurrent environments.

    Priority:
      1. Session cache hit            → return immediately (zero cost, zero LLM)
      2. French accent regex          → return "fr" (zero cost)
      3. Acquire per-session lock     → prevents double LLM call on race condition
      4. Re-check cache inside lock   → fast exit if another coroutine already filled it
      5. gpt-4.1-nano LLM call       → await chain.ainvoke (non-blocking)
      6. langdetect fallback          → asyncio.to_thread (non-blocking)
      7. Domain prior "fr"            → final safety net
    """
    if not text or not text.strip():
        return get_session_language(session_id)

    # ── 1. Fast cache hit (no lock needed — dict reads are atomic in CPython) ──
    if session_id and session_id in _SESSION_LANG_CACHE:
        cached = _SESSION_LANG_CACHE[session_id]
        logger.debug(f"[LANG_DETECT] Cache hit | session={session_id} → {cached}")
        return cached

    # ── 2. French accent fast path (zero LLM cost, 100% reliable) ────────────
    if _FRENCH_ACCENT_PATTERN.search(text):
        lang = "fr"
        logger.info(f"[LANG_DETECT] French accent → fr | session={session_id}")
        if session_id:
            set_session_language(session_id, lang)
        return lang

    # ── 3–5. Acquire per-session lock to prevent concurrent double-detection ──
    if session_id:
        lock = _get_session_lock(session_id)
        async with lock:
            # ── 4. Re-check inside lock (another coroutine may have just set it) ──
            if session_id in _SESSION_LANG_CACHE:
                return _SESSION_LANG_CACHE[session_id]

            lang = await _run_llm_detection(text, llm, session_id)
            set_session_language(session_id, lang)
            return lang
    else:
        # No session_id → no lock, no cache write
        return await _run_llm_detection(text, llm, session_id=None)


async def _run_llm_detection(text: str, llm, session_id: Optional[str]) -> str:
    """
    Internal: run LLM detection then langdetect fallback.
    Both paths are fully non-blocking (await / asyncio.to_thread).
    """
    _DETECT_PROMPT = (
        "You are a language detection tool. "
        "Detect the language of the text below and reply with ONLY its ISO 639-1 code "
        "(e.g. fr, en, es, de, vi, zh-cn, ja, ko). "
        "If the text is too short, ambiguous, or purely technical (numbers/units only), "
        "reply 'fr' (domain default — most users are French-speaking).\n\n"
        "Text: {text}\n\n"
        "ISO code:"
    )

    # ── 5. LLM detection — await, never blocks event loop ─────────────────────
    try:
        prompt = ChatPromptTemplate.from_template(_DETECT_PROMPT)
        chain = prompt | llm | StrOutputParser()

        raw: str = await chain.ainvoke({"text": text[:500]})
        lang = raw.strip().lower().split()[0]

        if lang not in SUPPORTED_LANGUAGES:
            logger.warning(
                f"[LANG_DETECT] LLM returned unknown code '{lang}' → domain prior"
            )
            lang = _DOMAIN_DEFAULT_LANG

        logger.info(f"[LANG_DETECT] LLM={lang} | session={session_id}")
        return lang

    except Exception as exc:
        logger.warning(f"[LANG_DETECT] LLM call failed ({exc}) → langdetect fallback")

    # ── 6. langdetect fallback — run in thread pool (non-blocking) ────────────
    lang = await asyncio.to_thread(_detect_sync_no_cache, text)
    logger.info(f"[LANG_DETECT] langdetect fallback → {lang} | session={session_id}")
    return lang


def detect_language(text: str, session_id: Optional[str] = None) -> str:
    """
    Sync language detection — no LLM call.

    Use this only in sync contexts (e.g. background tasks, non-async callers).
    Prefer detect_language_llm() for async contexts.

    Priority:
      1. Session cache (if session_id provided)
      2. French accent regex
      3. langdetect with prob threshold (only for text >= 4 words)
      4. Domain prior "fr"
    """
    if not text or not text.strip():
        return get_session_language(session_id)

    # ── 1. Session cache ──────────────────────────────────────────────────────
    if session_id and session_id in _SESSION_LANG_CACHE:
        return _SESSION_LANG_CACHE[session_id]

    # ── 2. French accent regex ────────────────────────────────────────────────
    if _FRENCH_ACCENT_PATTERN.search(text):
        lang = "fr"
        if session_id:
            set_session_language(session_id, lang)
        return lang

    # ── 3. langdetect (only reliable on >= 4 words) ───────────────────────────
    lang = _detect_sync_no_cache(text)
    if session_id:
        set_session_language(session_id, lang)
    return lang


def _detect_sync_no_cache(text: str) -> str:
    """
    Internal: run langdetect on text, no cache read/write.
    Returns domain prior if text is too short or detection fails.
    """
    words = text.strip().split()
    if len(words) >= 4:
        try:
            import langdetect
            from langdetect import DetectorFactory
            from langdetect.lang_detect_exception import LangDetectException
            DetectorFactory.seed = 0

            lang_probs = langdetect.detect_langs(text)
            if lang_probs:
                top = lang_probs[0]
                if top.prob >= 0.85 and top.lang in SUPPORTED_LANGUAGES:
                    return top.lang
        except Exception:
            pass

    # ── 4. Domain prior ───────────────────────────────────────────────────────
    return _DOMAIN_DEFAULT_LANG


def lang_code_to_name(lang_code: str) -> str:
    """Convert ISO 639-1 code to full language name. Defaults to 'French'."""
    return SUPPORTED_LANGUAGES.get(lang_code, "French")


# ═════════════════════════════════════════════════════════════════════════════
# Message catalog — single source of truth for all user-facing strings
# ═════════════════════════════════════════════════════════════════════════════

_MESSAGES: dict[str, dict[str, str]] = {
    "success": {
        "fr": "Génération de votre pièce réussie.",
        "en": "Your part generation successful.",
        "es": "La generación de su pieza ha sido exitosa.",
        "de": "Die Erstellung Ihres Teils war erfolgreich.",
        "vi": "Tạo chi tiết thành công.",
        "zh-cn": "您的零件已成功生成。",
        "ja": "部品の生成に成功しました。",
        "ko": "부품 생성이 완료되었습니다.",
    },
    "error_general": {
        "fr": "Une erreur s'est produite lors du traitement de votre demande.",
        "en": "An error occurred while processing your request.",
        "es": "Se produjo un error al procesar su solicitud.",
        "de": "Bei der Bearbeitung Ihrer Anfrage ist ein Fehler aufgetreten.",
        "vi": "Đã xảy ra lỗi khi xử lý yêu cầu của bạn.",
        "zh-cn": "处理您的请求时发生错误。",
        "ja": "リクエストの処理中にエラーが発生しました。",
        "ko": "요청을 처리하는 중에 오류가 발생했습니다.",
    },
    "error_agent_not_initialized": {
        "fr": "L'agent Text-to-CAD n'est pas initialisé correctement. Vérifiez les clés API et les connexions.",
        "en": "Text-to-CAD agent not initialized properly. Check API keys and connections.",
        "es": "El agente Text-to-CAD no se inicializó correctamente. Verifique las claves API y las conexiones.",
        "de": "Text-to-CAD-Agent nicht ordnungsgemäß initialisiert. Überprüfen Sie API-Schlüssel und Verbindungen.",
        "vi": "Agent Text-to-CAD chưa được khởi tạo đúng. Kiểm tra API keys và kết nối.",
        "zh-cn": "Text-to-CAD代理未正确初始化。检查API密钥和连接。",
        "ja": "Text-to-CADエージェントが正しく初期化されていません。APIキーと接続を確認してください。",
        "ko": "Text-to-CAD 에이전트가 제대로 초기화되지 않았습니다. API 키와 연결을 확인하세요.",
    },
    "error_processing_completed": {
        "fr": "Traitement terminé.",
        "en": "Processing completed.",
        "es": "Procesamiento completado.",
        "de": "Verarbeitung abgeschlossen.",
        "vi": "Xử lý hoàn tất.",
        "zh-cn": "处理完成。",
        "ja": "処理が完了しました。",
        "ko": "처리가 완료되었습니다.",
    },
    # Perforated sheet — user asked for a named/branded hole pattern (e.g. "motif AUBE
    # de chez ACIANOV") that has no equivalent in our supported notation (R/C/LR/LC +
    # T/U/Z — see data/Info/Perforated_Sheet/info.json). Friendly redirect instead of
    # silently ignoring the request or hallucinating a match.
    "perforated_unknown_pattern": {
        "fr": "Désolé, je ne connais pas encore ce motif ni ce fabricant 🙂 Je peux réaliser des perçages ronds, carrés ou oblongs (arrondis ou rectangulaires) avec un pas carré, en quinconce ou personnalisé — dites-moi la forme et l'espacement souhaités et je m'en occupe !",
        "en": "Sorry, I don't recognize this pattern or manufacturer yet 🙂 I can create round, square, or oblong holes (rounded or rectangular) with a square, staggered, or custom pitch — just tell me the shape and spacing you'd like and I'll take care of it!",
        "es": "Lo siento, todavía no conozco este motivo ni este fabricante 🙂 Puedo crear perforaciones redondas, cuadradas u oblongas (redondeadas o rectangulares) con un paso cuadrado, escalonado o personalizado — dime la forma y el espaciado que deseas y me encargo de ello.",
        "de": "Entschuldigung, dieses Muster oder diesen Hersteller kenne ich noch nicht 🙂 Ich kann runde, quadratische oder längliche Löcher (gerundet oder rechteckig) mit quadratischem, versetztem oder individuellem Raster erstellen — sagen Sie mir einfach die gewünschte Form und den Abstand.",
        "vi": "Xin lỗi, tôi chưa biết mẫu đục lỗ hay hãng sản xuất này 🙂 Tôi có thể tạo lỗ tròn, vuông hoặc oblong (bo tròn hoặc chữ nhật) với khoảng cách kiểu vuông, so le hoặc tùy chỉnh — hãy cho tôi biết hình dạng và khoảng cách bạn muốn nhé!",
        "zh-cn": "抱歉，我暂时还不认识这个花纹或这个厂商 🙂 我可以制作圆形、方形或长圆形（圆头或矩形）的孔，排列方式可以是正方形、错列或自定义——请告诉我您想要的孔型和间距，我来处理！",
        "ja": "申し訳ありませんが、この模様やメーカーはまだ認識できません 🙂 丸穴、角穴、長穴（丸形または矩形）を、正方配列・千鳥配列・カスタム配列で作成できます。ご希望の穴形状とピッチを教えてください！",
        "ko": "죄송하지만 아직 이 무늬나 제조사는 인식하지 못합니다 🙂 원형, 사각형 또는 장공(둥근 또는 사각) 홀을 정사각, 어긋배열 또는 맞춤 피치로 만들 수 있습니다 — 원하시는 구멍 모양과 간격을 알려주세요!",
    },
    # Perforated sheet — when hole + % are known but grid family (T/U/Z) is still unknown.
    # Lang code MUST come from get_session_language(session_id) (same cache as LANG_DETECT / unified).
    "perforated_pitch_type_question": {
        "fr": "Quel type de grille souhaitez-vous ? T (quinconce / triangulaire), U (aligné / carré), ou Z (stagger générique) ?",
        "en": "Which grid type do you want? T (staggered 60° / triangular), U (square / inline), or Z (generic stagger)?",
        "es": "¿Qué tipo de malla desea? T (escalonado 60° / triangular), U (cuadrada / en línea) o Z (escalonado genérico)?",
        "de": "Welchen Raster-Typ möchten Sie? T (versetzt 60° / dreieckig), U (quadratisch / inline) oder Z (allgemein versetzt)?",
        "vi": "Bạn muốn loại lưới đục nào? T (so le 60° / tam giác), U (vuông / thẳng hàng), hay Z (so le tổng quát)?",
        "zh-cn": "您希望哪种穿孔排列？T（60°错列/三角形）、U（正方形/顺排）还是 Z（通用错列）？",
        "ja": "どのグリッド形式にしますか？T（千鳥60°/三角配列）、U（正方/直列）、Z（汎用千鳥）？",
        "ko": "어떤 격자 형식을 원하시나요? T(60° 어긋삼각), U(정사각/일렬), Z(일반 어긋배열)?",
    },
}


def _get_message(key: str, lang: str) -> str:
    """
    Core message lookup.
    Falls back: requested lang → domain default ("fr") → "en".
    """
    catalog = _MESSAGES.get(key, {})
    return (
        catalog.get(lang)
        or catalog.get(_DOMAIN_DEFAULT_LANG)
        or catalog.get("en", "")
    )


# ═════════════════════════════════════════════════════════════════════════════
# Public message API — all functions take lang CODE (not user_text)
# ═════════════════════════════════════════════════════════════════════════════

def get_success_message(lang: str) -> str:
    """
    Return success message for the given language code.
    Args:
        lang: ISO 639-1 code, e.g. 'fr', 'en'. Get via get_session_language(session_id).
    """
    return _get_message("success", lang)


def get_error_message(lang: str, error_type: str = "general") -> str:
    """
    Return error message for the given language code and error type.
    Args:
        lang:       ISO 639-1 code.
        error_type: One of "general", "agent_not_initialized", "processing_completed".
    """
    key = f"error_{error_type}"
    result = _get_message(key, lang)
    if not result:
        result = _get_message("error_general", lang)
    return result


def get_perforated_unknown_pattern_message(lang: str) -> str:
    """
    Friendly redirect when the user asks for a named/branded perforation
    pattern (e.g. "motif AUBE de chez ACIANOV") that has no equivalent in
    our supported notation (R/C/LR/LC + T/U/Z).

    Args:
        lang: ISO 639-1 code from get_session_language(session_id).
    """
    return _get_message("perforated_unknown_pattern", lang)


def get_perforated_pitch_type_question(lang: str) -> str:
    """
    Ask user to choose perforated hole grid type (T / U / Z).

    Args:
        lang: ISO 639-1 code from get_session_language(session_id) — same source as
        unified flow / [LANG_DETECT], not the free-form session state string.
    """
    return _get_message("perforated_pitch_type_question", lang)


# ═════════════════════════════════════════════════════════════════════════════
# Utility functions (no language detection, purely rule-based)
# ═════════════════════════════════════════════════════════════════════════════

def detect_confirm_intent(text: str) -> bool:
    """
    Lightweight keyword-based YES/NO detector — no LLM call.
    Returns True if the user's message signals confirmation.
    Returns False if it signals rejection or is a new request.
    """
    t = text.lower().strip()

    YES_KEYWORDS = [
        "oui", "yes", "ok", "okay", "yep", "yeah", "sure", "confirme", "confirm",
        "confirmé", "allons-y", "go", "proceed", "continue", "lancer", "générer",
        "generer", "c'est bon", "c'est correct", "ça me convient", "parfait",
        "d'accord", "dacord", "j'accepte", "je confirme", "validé", "valider",
        "approve", "approved", "agreed", "correct", "exactement", "exactly",
        "✅", "👍",
    ]

    NO_KEYWORDS = [
        "non", "no", "nope", "nein", "pas", "annule", "cancel", "stop", "change",
        "modifier", "modifie", "changer", "correction", "corriger", "wrong",
        "incorrect", "pas correct", "pas bon", "not correct", "not right",
        "revise", "update", "❌", "👎",
    ]

    # Check NO first (more specific → avoids "ok no" being YES)
    for kw in NO_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', t):
            return False

    for kw in YES_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', t):
            return True

    return False
