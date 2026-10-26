"""Server-only credentials, independent of personal chat credentials."""
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..db import settings
from ..llm_settings import LlmEndpointError, validate_llm_url


class CollectorConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(Path(__file__).resolve().parents[3] / ".env"),
                                     env_prefix="KNOWLEDGE_", extra="ignore", env_file_encoding="utf-8")
    llm_api_key: SecretStr = SecretStr("")


def configuration():
    value = CollectorConfig()
    return value, settings.llm_base_url.strip(), settings.llm_model.strip()


def configured():
    config, url, model = configuration()
    return bool(config.llm_api_key.get_secret_value().strip() and url and model)


class CollectionError(Exception):
    """Only fixed, safe messages may cross the HTTP or audit boundary."""


def _decode(text, key, response_type, passages):
    import re
    from .evaluation_extraction import restore
    if not isinstance(text, str) or not text.strip():
        raise CollectionError('LLM이 정리 결과를 반환하지 않았습니다. 다시 시도해 주세요.')
    if key in text:
        raise CollectionError('LLM 응답이 필요한 정리 형식과 맞지 않습니다. 다시 시도해 주세요.')
    text = text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', text, flags=re.DOTALL | re.IGNORECASE)
    value = response_type.model_validate_json(fenced.group(1) if fenced else text)
    return restore(value, passages) if passages is not None else value


def complete(instruction, payload, response_type):
    import json
    import time
    config, url, model = configuration()
    key = config.llm_api_key.get_secret_value().strip()
    if not key or not url or not model:
        raise CollectionError("서버의 공용 LLM 키·주소·모델 설정이 필요합니다.")
    # No credentials in prompts, database records, response bodies or error logs.
    try:
        from .collector_schemas import ExtractedKnowledge
        from .evaluation_extraction import EvaluationPassages, REFERENCE_PROMPT, TERM_PROMPT, prepare
        passages = None
        if response_type is ExtractedKnowledge and payload.get("kind") == "evaluation":
            resolve_terms = payload.get("resolve_terms", False)
            payload, passages = prepare(payload)
            instruction, response_type = REFERENCE_PROMPT + (TERM_PROMPT if resolve_terms else ""), EvaluationPassages
        endpoint = validate_llm_url(url)
        body = {"model": model, "temperature": 0, "stream": False, "messages": [
            {"role": "system", "content": instruction + "\nJSON schema:\n" + json.dumps(response_type.model_json_schema(), ensure_ascii=False)},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]}
        if urlsplit(endpoint).hostname == "openrouter.ai":
            # Extraction needs a complete JSON answer, not a long reasoning
            # trace that consumes the shared reasoning/output token budget.
            # Keep provider-specific parameters off internal compatible APIs.
            body.update(response_format={"type": "json_object"}, reasoning={"effort": "minimal"})
        with httpx.Client(timeout=75, follow_redirects=False, trust_env=False) as client:
            length_retried = format_retried = False
            for max_tokens in (5000, 10000, 10000):
                body["max_tokens"] = max_tokens
                started = time.monotonic()
                with client.stream("POST", endpoint, headers={"Authorization": "Bearer " + key}, json=body) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 150_000:
                            raise CollectionError("LLM 응답이 허용 크기를 초과했습니다. 사례를 나누어 등록해 주세요.")
                        if time.monotonic() - started > 100:
                            raise CollectionError("LLM 응답 대기 시간이 초과됐습니다. 잠시 후 다시 시도해 주세요.")
                        chunks.append(chunk)
                choice = json.loads(b"".join(chunks))["choices"][0]
                if choice.get("finish_reason") == "length":
                    # Never interpret or save truncated JSON, even if it parses.
                    if not length_retried:
                        length_retried = True
                        continue
                    raise CollectionError("LLM이 응답 길이 한도에 도달해 자동 재시도 후에도 정리를 끝내지 못했습니다. 사례를 나누어 등록해 주세요.")
                if choice.get("finish_reason") in {"content_filter", "refusal"}:
                    raise CollectionError("LLM이 요청 내용을 처리하지 못했습니다. 원문을 확인해 주세요.")
                try:
                    return _decode(choice['message']['content'], key, response_type, passages)
                except (ValueError, TypeError):
                    if passages is None or format_retried:
                        raise
                    format_retried = True
                    # Retry original passages once, without echoing provider text
                    # or raw validation errors into the next request or logs.
                    body['messages'] = [*body['messages'], {'role': 'user', 'content':
                        '이전 응답의 형식 또는 원문 연결 검증에 실패했습니다. 같은 source_passages로 JSON 전체를 다시 작성하세요. '
                        '원문에 없는 용어·관계·수치는 제외하고 term/source_term/target_term/value는 해당 구절의 정확한 부분문자열만 사용하세요. '
                        '관계는 claims로 정리하고 fields는 {}로 두어도 됩니다. fields를 넣는다면 반드시 {"description":"a1p1"} 같은 구절 id만 값으로 쓰세요. 원문 문장이나 용어를 fields 값으로 넣으면 실패합니다. 방향·영향량 미언급은 null입니다. '
                        '승인 지식에 있다는 이유로 원문에 없는 용어를 추가하지 마세요.'}]
            raise CollectionError('LLM 응답을 자동 재시도 후에도 정리하지 못했습니다. 원문은 보존됩니다.')
    except CollectionError:
        raise
    except LlmEndpointError:
        raise CollectionError("LLM 서버 주소 설정을 확인해 주세요.") from None
    except httpx.TimeoutException:
        raise CollectionError("LLM 응답 대기 시간이 초과됐습니다. 잠시 후 다시 시도해 주세요.") from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            message = "공용 LLM 인증 또는 접근 권한을 확인해 주세요."
        elif status == 429:
            message = "공용 LLM 사용 한도에 도달했습니다. 잠시 후 다시 시도해 주세요."
        elif status >= 500:
            message = "LLM 서버에 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        else:
            message = "LLM 서버가 요청을 받지 못했습니다. 모델과 요청 설정을 확인해 주세요."
        raise CollectionError(message) from None
    except httpx.RequestError:
        raise CollectionError("LLM 서버에 연결하지 못했습니다. 서버 연결을 확인해 주세요.") from None
    except Exception:
        # Never echo provider output, HTTP request objects or validation inputs.
        raise CollectionError("LLM 응답이 필요한 정리 형식과 맞지 않습니다. 다시 시도해 주세요.") from None
