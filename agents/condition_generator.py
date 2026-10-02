"""자연어 요청을 키움증권 조건검색식 JSON으로 변환한다.

`.claude/agents/condition-searcher.md`에 정의된 condition-searcher 에이전트
지침을 그대로 읽어와 프롬프트에 포함시킨 뒤, Claude Code CLI를 subprocess로
호출하여 조건식 JSON을 생성한다. 실계좌 매매의 진입/청산 판단과는 무관한
조건식 저작(authoring) 도구이며, 사람이 결과를 검토한 뒤 config.py의
STRATEGY_MAP 등록 여부를 직접 결정한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

_AGENT_NAME = "condition-searcher"
_AGENT_MD_PATH = Path(__file__).resolve().parent.parent / ".claude" / "agents" / f"{_AGENT_NAME}.md"
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class ConditionGenerationResult:
    """condition-searcher 에이전트 호출 결과.

    condition_json이 None이면 응답에서 JSON을 파싱하지 못한 것이며,
    raw_text에 담긴 원본 응답을 사람이 직접 확인해야 한다.
    """

    condition_json: dict[str, Any] | None
    raw_text: str


def _load_agent_instructions() -> str:
    """condition-searcher.md에서 frontmatter를 제외한 본문 지침을 읽는다."""
    if not _AGENT_MD_PATH.is_file():
        raise FileNotFoundError(
            f"condition-searcher 에이전트 정의 파일을 찾을 수 없습니다: {_AGENT_MD_PATH}"
        )
    text = _AGENT_MD_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def _build_prompt(nl_request: str) -> str:
    instructions = _load_agent_instructions()
    return (
        f"{instructions}\n\n"
        "---\n\n"
        f"사용자 자연어 요청: {nl_request}\n\n"
        "위 condition-searcher 지침을 그대로 적용하여 이 요청을 키움증권 조건검색식 "
        "JSON으로 변환하라. 지침에 없는 지표나 범위를 요청받았다면 §9 규칙대로 경고하고 "
        "JSON 대신 경고 메시지만 출력하라. 최종 응답에는 ```json 코드블록 하나만 포함하고, "
        "그 밖의 설명 텍스트는 덧붙이지 마라."
    )


async def _run_claude_cli(prompt: str, *, model: str, timeout_sec: float) -> str:
    if shutil.which(config.CLAUDE_BIN) is None and not os.path.isfile(config.CLAUDE_BIN):
        raise RuntimeError(
            f"Claude Code CLI 실행 파일을 찾을 수 없습니다: {config.CLAUDE_BIN} "
            "(PATH에 claude가 있는지, 또는 CLAUDE_BIN 환경변수를 확인하세요)"
        )

    proc = await asyncio.create_subprocess_exec(
        config.CLAUDE_BIN,
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"condition-searcher 호출이 {timeout_sec:.0f}초 내에 끝나지 않았습니다."
        ) from None

    if proc.returncode != 0:
        raise RuntimeError(
            f"Claude Code CLI 실행 실패 (exit={proc.returncode}): "
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )

    return stdout.decode("utf-8", errors="replace")


def _extract_result_text(cli_stdout: str) -> str:
    """`--output-format json`으로 감싸진 CLI 출력에서 최종 응답 텍스트만 뽑아낸다."""
    try:
        payload = json.loads(cli_stdout)
    except json.JSONDecodeError:
        return cli_stdout

    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, str):
            return result

    return cli_stdout


def _extract_condition_json(text: str) -> dict[str, Any] | None:
    match = _JSON_BLOCK_RE.search(text)
    candidate = match.group(1) if match else text.strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def generate_condition(
    nl_request: str,
    *,
    model: str | None = None,
    timeout_sec: float | None = None,
) -> ConditionGenerationResult:
    """자연어 요청(예: "거래량 급증 + 5일선 돌파 종목")을 조건식 JSON으로 변환한다."""
    prompt = _build_prompt(nl_request)
    logger.debug("condition-searcher 프롬프트 길이=%d", len(prompt))

    stdout = await _run_claude_cli(
        prompt,
        model=model or config.ENTRY_MODEL,
        timeout_sec=timeout_sec or config.ENTRY_AGENT_TIMEOUT_SEC,
    )
    result_text = _extract_result_text(stdout)
    condition_json = _extract_condition_json(result_text)

    if condition_json is None:
        logger.warning("condition-searcher 응답에서 JSON을 파싱하지 못했습니다.")

    return ConditionGenerationResult(condition_json=condition_json, raw_text=result_text)


async def _main() -> None:
    if len(sys.argv) < 2:
        print('사용법: uv run python -m agents.condition_generator "<자연어 요청>"')
        raise SystemExit(1)

    nl_request = " ".join(sys.argv[1:])
    result = await generate_condition(nl_request)

    if result.condition_json is not None:
        print(json.dumps(result.condition_json, ensure_ascii=False, indent=2))
    else:
        print("[경고] 응답에서 JSON을 파싱하지 못했습니다. 원본 응답:\n")
        print(result.raw_text)


if __name__ == "__main__":
    asyncio.run(_main())
