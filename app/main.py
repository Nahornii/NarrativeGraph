from pathlib import Path

import httpx

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.llm import LLMClient
from app.nlp import MAX_CHARACTERS
from app.pipeline import analyze
from app.schemas import AnalysisResult

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title='NarrativeGraph', version='1.3')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


class AnalyzeInput(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARACTERS)


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/status')
def status():
    cfg = settings()
    return {'configured': bool(cfg.api_key), 'model': cfg.model, 'max_characters': MAX_CHARACTERS}


@app.post('/api/analyze', response_model=AnalysisResult)
def analyze_route(payload: AnalyzeInput):
    cfg = settings()
    if not cfg.api_key:
        raise HTTPException(status_code=400, detail='Додай API_KEY у local_config.py та перезапусти сервер.')
    llm = LLMClient(cfg)
    try:
        return analyze(payload.text, llm)
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f'Час очікування LLM вичерпано на етапі «{llm.stage}». '
                   'Перевір доступ до API командою: python3 check_api.py. '
                   'Якщо короткий запит працює, повтори аналіз із меншим текстом.'
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        message = str(exc)
        if any(code in message for code in ['401', '403', '429']):
            raise HTTPException(status_code=502, detail='Помилка авторизації, квоти або доступу до LLM API. Перевір ключ, модель і ліміти.') from exc
        raise HTTPException(status_code=502, detail=f'Не вдалося завершити аналіз: {type(exc).__name__}. Перевір налаштування API та мережу.') from exc
