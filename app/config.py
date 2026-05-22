from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    supabase_jwt_secret: str

    # Frontend origin allowed by CORS. Must be an explicit origin (not "*")
    # because CORS with allow_credentials=True forbids the wildcard.
    frontend_origin: str = "http://localhost:5173"

    # Auth cookie attributes. cookie_secure=True requires HTTPS; keep False in
    # local dev. cookie_samesite is "strict" by default to defend against CSRF.
    cookie_secure: bool = False
    cookie_samesite: str = "strict"

    model_config = {"env_file": ".env"}


settings = Settings()
