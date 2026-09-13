from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    secret_key: str
    stubhub_bearer_token: str = ""

    class Config:
        env_file = ".env"

settings = Settings()
