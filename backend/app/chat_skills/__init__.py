"""Versioned instructions for the app's three skills; execution lives in chat_tools."""
from pathlib import Path

ROOT = Path(__file__).parent
SKILLS = {
    "data_analysis_plan": ("현재 데이터 통계·ML 분석", None),
    "evaluation_explanation": ("승인 근거 설명", "evaluation_explanation.md"),
    "query": ("쿼리 작성", "query.md"),
    "dataset": ("데이터셋 생성", "dataset.md"),
    "card": ("카드 구성", "card.md"),
}


def instructions(skill):
    from ..measurement_items import DOMAIN_GUIDANCE
    if skill == 'data_analysis_plan':
        from ..chat_data_contracts import DATA_ANALYSIS_PROMPT
        return DATA_ANALYSIS_PROMPT + '\n' + DOMAIN_GUIDANCE
    text = (ROOT / SKILLS[skill][1]).read_text(encoding="utf-8")
    return text + '\n' + DOMAIN_GUIDANCE + ('\n\n' + (ROOT / 'chart_style.md').read_text(encoding='utf-8') if skill == 'card' else '')
