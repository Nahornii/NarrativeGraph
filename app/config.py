import os
from dataclasses import dataclass

try:
    import local_config
except ImportError:
    local_config = None


@dataclass(frozen=True)
class Settings:
    api_key: str
    provider: str
    model: str
    base_url: str


def settings() -> Settings:
    def value(name: str, default: str = '') -> str:
        env = os.getenv(name)
        if env is not None:
            return env.strip()
        return str(getattr(local_config, name, default) if local_config else default).strip()

    provider = value('PROVIDER', 'gemini')
    base_url = value('BASE_URL')
    if not base_url:
        base_url = (
            'https://generativelanguage.googleapis.com/v1beta/openai/'
            if provider == 'gemini'
            else 'https://api.openai.com/v1/'
        )
    model = value('MODEL', 'gemini-3.8-flash' if provider == 'gemini' else 'gpt-4o-mini')
    return Settings(api_key=value('API_KEY'), provider=provider, model=model, base_url=base_url)
