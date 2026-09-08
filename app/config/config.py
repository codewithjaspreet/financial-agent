from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    # superuser: migrations, login lookup by email

    app_database_url: str
    # app_user: every tenant-scoped query, RLS-enforced

    app_user_password: str = "app_user_dev_password"
    gemini_api_key: str = ""
    jwt_secret: str
    max_tool_calls: int = 10
    max_seconds: int = 20
    fake_bad_number: bool = False

    class Config:
        env_file = ".env"


settings = Settings()
