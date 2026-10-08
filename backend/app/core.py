import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, URL

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    db_host: str = 'localhost'
    db_name: str = 'claimshield'
    db_port: int = 5432
    db_password: str = 'claimshield_local_demo'
    data_dir: Path = Path('/data')
    artifact_dir: Path = Path('/artifacts')
    groq_api_key: str = ''
    groq_model: str = 'openai/gpt-oss-120b'
    groq_timeout_seconds: float = 45

settings = Settings()
engine = create_engine(URL.create('postgresql+psycopg', username='claimshield', password=settings.db_password, host=settings.db_host, port=settings.db_port, database=settings.db_name), pool_pre_ping=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')

def now():
    return datetime.now(timezone.utc)

def clean(value):
    def default(v):
        if isinstance(v, (datetime, date)): return v.isoformat()
        if isinstance(v, Decimal): return float(v)
        if hasattr(v, 'item'): return v.item()
        raise TypeError(type(v).__name__)
    return json.loads(json.dumps(value, default=default, allow_nan=False))

