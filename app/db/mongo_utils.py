"""
Shared MongoDB helpers for cloud and local deployments.
"""
import os


def resolve_mongo_uri(primary_env: str, fallback_env: str = "MONGODB_URI") -> str:
    uri = os.getenv(primary_env, os.getenv(fallback_env, "")).strip()
    if uri:
        return uri

    local_only = os.getenv("RAILMAN_LOCAL_ONLY", "0").strip().lower() in {"1", "true", "yes"}
    if local_only:
        return os.getenv("RAILMAN_LOCAL_MONGODB_URI", "mongodb://127.0.0.1:27017").strip()

    return ""


def build_mongo_client_kwargs(uri: str) -> dict:
    kwargs = {
        "serverSelectionTimeoutMS": 5000,
        "connectTimeoutMS": 5000,
        "maxPoolSize": 20,
        "minPoolSize": 2,
    }

    tls_mode = os.getenv("MONGODB_TLS", "auto").strip().lower()
    is_srv = uri.startswith("mongodb+srv://")
    is_local = uri.startswith("mongodb://127.0.0.1") or uri.startswith("mongodb://localhost")

    if tls_mode == "true":
        kwargs["tls"] = True
        kwargs["tlsAllowInvalidCertificates"] = True
    elif tls_mode == "false":
        pass
    elif is_srv and not is_local:
        kwargs["tls"] = True
        kwargs["tlsAllowInvalidCertificates"] = True

    return kwargs
